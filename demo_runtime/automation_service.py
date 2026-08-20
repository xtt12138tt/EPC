"""批次 EPC 自动化 Worker。

网页服务只下发单据命令；本模块独占 Playwright 浏览器上下文，并用独立
标签页并发执行多张单据。每张单的日志、确认、暂停与截图都绑定 order_id，
不会再依赖全局 input/print 或单一浏览器锁。
"""
from __future__ import annotations

import asyncio
import builtins
import contextvars
import copy
import json
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from batch_store import Store, clear_execution_overrides
from config import BATCH_CONCURRENCY, BROWSER_CHANNEL, CHROME_USER_DATA, EPC_URL, SLOW_MO, TIMEOUT


BASE_DIR = Path(__file__).parent.resolve()
ACTIVE_RUN: contextvars.ContextVar["OrderRun | None"] = contextvars.ContextVar(
    "epc_active_order_run", default=None
)
_ORIGINAL_PRINT = builtins.print
_PRINT_ROUTER_INSTALLED = False


class OrderRun:
    """一张单据的运行上下文；可由网页线程安全地控制。"""

    def __init__(self, store: Store, order_id: str, data: dict):
        self.store = store
        self.order_id = order_id
        self.data = data
        self.log_items: list[tuple[str, str]] = []
        self.screenshots: list[str] = []
        self.payee_review: dict | None = None
        self.verification: list[dict] = []
        self.waiting: str | None = None
        self.error: str | None = None
        self.done = False
        self.page: Page | None = None
        self.task: asyncio.Task | None = None
        self._responses: queue.Queue[str] = queue.Queue()
        self._lock = threading.RLock()
        self._pause_requested = False
        self._resume_status = "RUNNING"
        self._status = "QUEUED"

    def _append(self, kind: str, message: str) -> None:
        message = str(message)
        with self._lock:
            self.log_items.append((kind, message))
            self.log_items = self.log_items[-500:]
        self.store.append_event(self.order_id, kind, message[:2000])

    def log(self, message: str, kind: str = "log") -> None:
        self._append(kind, message)

    def set_status(self, status: str, step: str = "", error: str | None = None) -> None:
        with self._lock:
            self._status = status
            if error is not None:
                self.error = error or None
        fields: dict[str, Any] = {"status": status, "current_step": step[:240]}
        if error is not None:
            fields["error"] = error
        self.store.update_order(self.order_id, **fields)
        self._append("status", f"{status}{'：' + step if step else ''}")

    def capture_print(self, message: str) -> None:
        if message.startswith("__PAYEE_REVIEW__"):
            try:
                self.payee_review = json.loads(message[len("__PAYEE_REVIEW__"):])
                self._append("payee_review", "收款人差异等待确认")
            except Exception as exc:
                self._append("error", f"收款人差异数据解析失败：{exc}")
            return
        self._append("log", message)

    def respond(self, value: Any) -> None:
        if isinstance(value, (dict, list)):
            # 前端提交平台多余人员/代收审核结果后，立即清空旧审核状态。
            # 否则轮询会持续返回旧 payee_review，导致确认框反复显示。
            with self._lock:
                self.payee_review = None
            value = json.dumps(value, ensure_ascii=False)
        self._responses.put(str(value))

    def request_pause(self) -> bool:
        with self._lock:
            if self.done:
                return False
            self._pause_requested = True
            self._resume_status = self._status
            waiting = self.waiting
            current_status = self._status
        if waiting:
            self.set_status("PAUSED", f"已暂停：等待你的确认（{current_status}）")
            self._append("log", "⏸ 已立即暂停：当前正等待网页确认，不会继续执行下一步")
            return True
        self.store.update_order(
            self.order_id,
            status="PAUSE_REQUESTED",
            current_step="已请求暂停，等待当前安全步骤结束",
        )
        self._append("status", "PAUSE_REQUESTED：等待安全暂停点")
        return True

    def resume(self) -> bool:
        with self._lock:
            if self.done:
                return False
            self._pause_requested = False
            waiting = self.waiting
            resume_status = self._resume_status or self._status or "RUNNING"
        if waiting:
            self.set_status(resume_status, "已继续等待你的确认")
        self._append("status", "已请求继续执行")
        return True

    async def checkpoint(self, step: str) -> None:
        with self._lock:
            should_pause = self._pause_requested
            resume_status = self._resume_status or self._status or "RUNNING"
        if not should_pause:
            return

        self.set_status("PAUSED", f"安全暂停：{step}")
        self._append("log", f"⏸ 已在安全点暂停：{step}")
        while True:
            await asyncio.sleep(0.2)
            with self._lock:
                if not self._pause_requested:
                    break
        self.set_status(resume_status, f"继续执行：{step}")
        self._append("log", f"▶ 已继续：{step}")

    async def ask(self, prompt: str, status: str) -> str:
        with self._lock:
            self.waiting = prompt
        self.set_status(status, prompt)
        self._append("prompt", prompt)
        while True:
            await self.checkpoint("等待网页确认")
            try:
                value = self._responses.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.15)
                continue
            with self._lock:
                self.waiting = None
            self._append("input", f">>> {value}")
            return value

    async def screenshot(self, page: Page, stage: str) -> str:
        safe_order = re.sub(r"[^A-Za-z0-9_-]", "_", self.order_id)
        safe_stage = re.sub(r"[^A-Za-z0-9_-]", "_", stage)
        name = f"screenshots/{safe_order}_{safe_stage}_{int(time.time() * 1000)}.png"
        path = BASE_DIR / name
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)
        with self._lock:
            self.screenshots.append(name)
        self._append("screenshot", f"截图：{name}")
        return name

    def save_verification(self, items: list[dict]) -> None:
        with self._lock:
            self.verification = items
        self.store.save_payee_verifications(self.order_id, items)
        failed = sum(1 for item in items if item.get("result") != "matched")
        self._append("verification", f"收款人核验完成：{len(items)} 条，异常 {failed} 条")

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "log": self.log_items[-200:],
                "waiting": self.waiting,
                "payee_review": self.payee_review,
                "screenshots": list(self.screenshots),
                "verification": list(self.verification),
                "done": self.done,
                "error": self.error,
                "running": not self.done,
                "status": self._status,
            }

    def clear_history(self) -> None:
        with self._lock:
            self.log_items = []
            self.screenshots = []
            self.payee_review = None
            self.verification = []
            self.error = None

    def finish(self, error: str | None = None) -> None:
        with self._lock:
            self.done = True
            if error:
                self.error = error


class AutomationService:
    """单浏览器、多标签页的 EPC 执行服务。"""

    def __init__(self, store: Store, max_concurrency: int = BATCH_CONCURRENCY):
        self.store = store
        self.max_concurrency = max(1, min(3, int(max_concurrency)))
        self._runs: dict[str, OrderRun] = {}
        self._runs_lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._playwright: Playwright | None = None
        self._browser_context: BrowserContext | None = None
        self._browser = None
        self._semaphore: asyncio.Semaphore | None = None
        self._browser_lock: asyncio.Lock | None = None
        self._login_lock: asyncio.Lock | None = None
        self._authenticated = False

    def submit(self, order_id: str, data: dict) -> OrderRun:
        with self._runs_lock:
            existing = self._runs.get(order_id)
            if existing and not existing.done:
                return existing
            # 每次创建新的自动化运行都视为全新一轮填报：不继承上一轮人工保留、
            # 同人关联、忽略核验项或接受总额差异等运行期覆盖。
            fresh_data = copy.deepcopy(data or {})
            fresh_data, overrides_cleared = clear_execution_overrides(fresh_data)
            if overrides_cleared:
                self.store.update_order(order_id, draft=fresh_data)
                self.store.append_event(
                    order_id,
                    "status",
                    "新一轮填报：已清除上一轮人工确认、保留、同人关联和忽略项",
                )
            run = OrderRun(self.store, order_id, fresh_data)
            self._runs[order_id] = run

        run.set_status("QUEUED", "等待 EPC 浏览器标签页")
        self._ensure_worker()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self._create_task(run), self._loop)
        future.result(timeout=5)
        return run

    def get_run(self, order_id: str) -> OrderRun | None:
        with self._runs_lock:
            return self._runs.get(order_id)

    def snapshot(self, order_id: str) -> dict | None:
        run = self.get_run(order_id)
        return run.snapshot() if run else None

    def respond(self, order_id: str, value: Any) -> bool:
        run = self.get_run(order_id)
        if not run or run.done:
            return False
        run.respond(value)
        return True

    def request_pause(self, order_id: str) -> bool:
        run = self.get_run(order_id)
        return bool(run and run.request_pause())

    def resume(self, order_id: str) -> bool:
        run = self.get_run(order_id)
        return bool(run and run.resume())

    def clear_history(self, order_id: str) -> bool:
        run = self.get_run(order_id)
        if run and not run.done:
            return False
        if run:
            run.clear_history()
        return True

    async def _cancel_run(self, run: OrderRun) -> None:
        if run.page is not None and not run.page.is_closed():
            try:
                await run.page.close()
            except Exception:
                pass
        if run.task and not run.task.done():
            run.task.cancel()
        run.finish()

    def cancel_orders(self, order_ids: list[str]) -> None:
        runs = [self.get_run(order_id) for order_id in order_ids]
        active_runs = [run for run in runs if run and not run.done]
        if not active_runs or self._loop is None:
            return
        futures = [asyncio.run_coroutine_threadsafe(self._cancel_run(run), self._loop) for run in active_runs]
        for future in futures:
            try:
                future.result(timeout=5)
            except Exception:
                pass

    def _ensure_worker(self) -> None:
        with self._runs_lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._thread_main,
                name="epc-automation-worker",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("EPC 自动化 Worker 启动超时")

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._semaphore = asyncio.Semaphore(self.max_concurrency)
        self._browser_lock = asyncio.Lock()
        self._login_lock = asyncio.Lock()
        self._install_print_router()
        self._ready.set()
        loop.run_forever()

    async def _create_task(self, run: OrderRun) -> None:
        if run.task and not run.task.done():
            return
        run.task = asyncio.create_task(self._execute(run), name=f"epc-{run.order_id}")

    async def _ensure_browser_context(self) -> BrowserContext:
        if self._browser_context:
            return self._browser_context
        assert self._browser_lock is not None
        async with self._browser_lock:
            # 三张单同时启动时，只有第一个任务允许创建浏览器上下文。
            # 其余任务复用同一上下文，因此只会有一个浏览器窗口。
            if self._browser_context:
                return self._browser_context
            self._playwright = await async_playwright().start()
            if CHROME_USER_DATA:
                self._browser_context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=CHROME_USER_DATA,
                    channel=BROWSER_CHANNEL or None,
                    headless=False,
                    slow_mo=SLOW_MO,
                    args=["--start-maximized"],
                )
            else:
                self._browser = await self._playwright.chromium.launch(
                    channel=BROWSER_CHANNEL or None,
                    headless=False,
                    slow_mo=SLOW_MO,
                    args=["--start-maximized"],
                )
                self._browser_context = await self._browser.new_context(
                    viewport={"width": 1920, "height": 1080}
                )
        return self._browser_context

    async def _new_epc_page(self, run: OrderRun) -> Page:
        context = await self._ensure_browser_context()
        assert self._login_lock is not None
        async with self._login_lock:
            import automation

            if not self._authenticated:
                # 先只使用浏览器首个标签页登录；在登录完成前不创建其他单据标签页。
                page = context.pages[0] if context.pages else await context.new_page()
                run.log("→ 初始化 EPC 登录态")
                await automation.login(page)
                self._authenticated = True
                return page

            # 登录完成后才为后续单据创建新标签页；同一 Context 中只会新增 Tab。
            page = await context.new_page()
            await page.goto(EPC_URL, wait_until="domcontentloaded", timeout=TIMEOUT)
            await page.wait_for_selector(".ant-select-selection", timeout=TIMEOUT)
        return page

    async def _execute(self, run: OrderRun) -> None:
        page: Page | None = None
        token = None
        outcome: str | None = None
        try:
            assert self._semaphore is not None
            async with self._semaphore:
                await run.checkpoint("等待可用 EPC 标签页")
                run.set_status("RUNNING", "打开 EPC 报销页")
                token = ACTIVE_RUN.set(run)
                page = await self._new_epc_page(run)
                run.page = page

                import automation

                outcome = await automation.run_order_in_page(page, run.data, run)
                if outcome == "requires_attention":
                    run.set_status("REQUIRES_ATTENTION", "需要人工修正后再继续")
                elif outcome == "completed":
                    run.set_status("COMPLETED", "第2页已确认，提报完成（未最终提交）")
                elif outcome == "ready_to_submit":
                    run.set_status("READY_TO_SUBMIT", "等待最终提交确认")
                elif run.snapshot()["status"] not in {"PAUSED", "REQUIRES_ATTENTION", "READY_TO_SUBMIT"}:
                    run.set_status("COMPLETED", "自动化流程完成")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            run.log(message, "error")
            if "TargetClosedError" in message or "has been closed" in message:
                run.set_status(
                    "REQUIRES_ATTENTION",
                    "EPC 自动化标签页已关闭或连接中断，请检查页面后重新填报",
                    error="EPC 自动化标签页已关闭或连接中断；未继续后续步骤。",
                )
            else:
                run.set_status("FAILED", "自动化执行失败", error=message)
        finally:
            if token is not None:
                ACTIVE_RUN.reset(token)
            # 除了批次重置明确取消外，所有结束状态（包括自动化失败）均保留 EPC 标签页，
            # 方便用户查看当前页面并在前端根据错误继续处理。
            keep_page = outcome in {"completed", "requires_attention", "ready_to_submit"} or run.error is not None
            if page is not None and not page.is_closed() and not keep_page:
                try:
                    await page.close()
                except Exception:
                    pass
            elif page is not None and not page.is_closed() and keep_page:
                run.log("EPC 标签页已保留，可继续人工复查或修正（未自动关闭）")
            run.finish()

    @staticmethod
    def _install_print_router() -> None:
        global _PRINT_ROUTER_INSTALLED
        if _PRINT_ROUTER_INSTALLED:
            return

        def routed_print(*args, **kwargs):
            run = ACTIVE_RUN.get()
            if run is None:
                return _ORIGINAL_PRINT(*args, **kwargs)
            sep = kwargs.get("sep", " ")
            message = sep.join(str(arg) for arg in args)
            run.capture_print(message)

        builtins.print = routed_print
        _PRINT_ROUTER_INSTALLED = True
