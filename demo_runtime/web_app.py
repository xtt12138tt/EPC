"""
EPC 报销 Agent - 本地 Web 界面
双击 start_web.bat 启动 → 浏览器打开 http://localhost:5000

功能：
  1. 粘贴签到表文本 → 自动解析玩家/接口人
  2. 填关键字段 → 生成 JSON 预览
  3. 确认提交 → 后端跑 Playwright 自动化
  4. 每步进度/截图显示在网页，点按钮确认（不碰 cmd）
"""
import os
import re
import io
import sys
import json
import uuid
import queue
import builtins
import threading
import asyncio
from pathlib import Path

from flask import Flask, request, jsonify, send_file, render_template

from batch_store import ACTIVE_ORDER_STATUSES, Store, clear_execution_overrides
from automation_service import AutomationService
from expense_note import build_standard_expense_note

BASE_DIR = Path(__file__).parent.resolve()
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

# 批次持久化存储 + EPC 单写入锁（第一版并发=1，避免串单）
store = Store()
# Playwright 页面和网页确认队列都只存在原服务进程内；服务重启后不能安全续接。
for _interrupted_order_id in store.recover_interrupted_orders():
    print(f"[恢复] {_interrupted_order_id} 已标记为自动化中断，等待重新开始")
AUTOMATION_LOCK = threading.Lock()
automation_service = AutomationService(store)


# ─────────────────────────────────────────────
# 签到表解析
# ─────────────────────────────────────────────

SCARCITY = ["千万级", "百万级", "十万级", "千级"]
TEST_FORMS = ["实验室测试/座谈会", "入户调研", "线上深访/测试", "街访"]

# 域外类测试形式（同一套费率）：实验室/座谈会、线上、街访
OUTSIDE_FORMS = {"实验室测试/座谈会", "线上深访/测试", "街访"}
INSIDE_FORMS  = {"入户调研"}

# 基础礼金价目表：(连续周期, 类别, 时长下限含, 时长上限不含, 稀缺, 基础礼金)
# 类别：outside=域外(实验室/线上/街访)  inside=入户
RATE_TABLE = [
    ("单日", "outside", 0.0, 2.001, "千万级", 150),
    ("单日", "outside", 0.0, 2.001, "百万级", 180),
    ("单日", "outside", 0.0, 2.001, "十万级", 200),
    ("单日", "outside", 0.0, 2.001, "千级",   260),
    ("单日", "outside", 2.001, 3.001, "千万级", 200),
    ("单日", "outside", 2.001, 3.001, "百万级", 240),
    ("单日", "outside", 2.001, 3.001, "十万级", 260),
    ("单日", "outside", 2.001, 3.001, "千级",   340),
    ("单日", "outside", 3.001, 4.001, "千万级", 250),
    ("单日", "outside", 3.001, 4.001, "百万级", 300),
    ("单日", "outside", 3.001, 4.001, "十万级", 330),
    ("单日", "outside", 3.001, 4.001, "千级",   420),
    ("单日", "inside",  0.0, 2.001, "千万级", 300),
    ("单日", "inside",  0.0, 2.001, "百万级", 420),
    ("单日", "inside",  0.0, 2.001, "十万级", 450),
    ("单日", "inside",  0.0, 2.001, "千级",   540),
    ("单日", "inside",  2.001, 3.001, "千万级", 400),
    ("单日", "inside",  2.001, 3.001, "百万级", 560),
    ("单日", "inside",  2.001, 3.001, "十万级", 600),
    ("单日", "inside",  2.001, 3.001, "千级",   720),
    ("单日", "inside",  3.001, 999, "千万级", 500),
    ("单日", "inside",  3.001, 999, "百万级", 700),
    ("单日", "inside",  3.001, 999, "十万级", 750),
    ("单日", "inside",  3.001, 999, "千级",   900),
    ("多日", "outside", 0.0, 4.001, "千万级", 400),
    ("多日", "outside", 0.0, 4.001, "百万级", 480),
    ("多日", "outside", 0.0, 4.001, "十万级", 520),
    ("多日", "outside", 0.0, 4.001, "千级",   560),
    ("多日", "outside", 4.001, 6.001, "千万级", 500),
    ("多日", "outside", 4.001, 6.001, "百万级", 600),
    ("多日", "outside", 4.001, 6.001, "十万级", 650),
    ("多日", "outside", 4.001, 6.001, "千级",   700),
]

# 默认交通补贴（>2km 则 100，让用户自己覆盖）
DEFAULT_TRANSPORT = 50


def _form_cat(test_form: str) -> str:
    if test_form in INSIDE_FORMS:
        return "inside"
    return "outside"


def infer_scarcity(period: str, test_form: str, hours, base) -> list:
    """根据 (连续周期,测试形式,时长,基础礼金) 反查稀缺级别，返回候选列表"""
    if base is None or hours is None:
        return []
    cat = _form_cat(test_form or "实验室测试/座谈会")
    matches = []
    for p, c, lo, hi, sc, b in RATE_TABLE:
        if p == period and c == cat and lo <= float(hours) < hi and b == base:
            matches.append(sc)
    return matches


def infer_base(period: str, test_form: str, hours, scarcity) -> int:
    """已知 (连续周期,测试形式,时长,稀缺) 查基础礼金"""
    if hours is None:
        return None
    cat = _form_cat(test_form or "实验室测试/座谈会")
    for p, c, lo, hi, sc, b in RATE_TABLE:
        if p == period and c == cat and lo <= float(hours) < hi and sc == scarcity:
            return b
    return None


def _extract_meta(text: str, meta: dict):
    """全文扫元信息"""
    m = re.search(r"(?<![A-Za-z0-9])([A-Za-z]\d{8}[A-Za-z]?)(?![A-Za-z0-9])", text)
    if m:
        meta["project_id"] = m.group(1)
    m = re.search(r"(\d+(?:\.\d+)?)\s*[Hh]\b", text) or re.search(r"(\d+(?:\.\d+)?)\s*小时", text)
    if m:
        v = float(m.group(1))
        meta["测试时长(小时)"] = int(v) if v.is_integer() else v
    for s in SCARCITY:
        if s in text:
            meta["样本稀缺性"] = s
            break
    m = re.search(r"交(?:通)?补(?:贴)?\s*[:：]?\s*(\d+)", text)
    if m:
        meta["transport"] = int(m.group(1))
    for f in TEST_FORMS:
        if f in text:
            meta["测试形式"] = f
            break
    if any(k in text for k in ("多日", "非单日")):
        meta["连续周期"] = "多日"
    elif "单日" in text:
        meta["连续周期"] = "单日"


# 类别关键字
INTERFACE_KWS  = ("接口人", "接口", "渠道", "KOL", "kol", "介绍人", "推荐人")
PARTTIME_KWS   = ("兼职", "甄别", "电访", "邀约", "海捞", "实验室执行", "街访执行", "测试执行")
PLAYER_KWS     = ("玩家", "用户", "参与者", "受访者")
HEADER_KWS     = ("姓名", "手机", "电话", "微信", "类型", "金额", "备注", "序号", "编号",
                  "总礼金", "礼金", "交补", "线下", "线上", "样本量", "样本", "场次", "单价")


def _detect_category(toks, joined, has_phone, small_nums):
    """检测本行是否是类别切换行；返回新类别或 None"""
    # 数据行（有手机或多数字）不是类别切换
    if has_phone or small_nums >= 2:
        return None
    if any(k in joined for k in INTERFACE_KWS):
        return "接口人"
    if any(k in joined for k in PARTTIME_KWS):
        return "兼职"
    if any(k in joined for k in PLAYER_KWS):
        return "玩家"
    return None


def _extract_name_phone(toks, allow_english=False):
    """从 token 列表提取姓名和电话（可能粘在一起）
    allow_english=True 时允许纯英文名（接口人/兼职段用）
    """
    name, phone = None, None
    for t in toks:
        # 姓名+电话粘一起：中文姓名 + 11位手机
        m = re.match(r"^([\u4e00-\u9fa5·]{1,8})\s*(1\d{10})$", t)
        if m:
            name = m.group(1)
            phone = m.group(2)
            return name, phone
    # 分开找
    for t in toks:
        if re.fullmatch(r"1\d{10}", t):
            phone = t
            break
    for t in toks:
        # 排除类别/表头/稀缺/测试形式
        if any(k in t for k in PLAYER_KWS + INTERFACE_KWS + PARTTIME_KWS):
            continue
        if t in SCARCITY or t in TEST_FORMS:
            continue
        if t in ("单日", "多日", "非单日"):
            continue
        # 项目号形如 G26070009C
        if re.fullmatch(r"[A-Za-z]{1,4}\d{5,}[A-Za-z0-9]*", t):
            continue
        # 时长 2h 4H
        if re.fullmatch(r"\d+(?:\.\d+)?[Hh]", t):
            continue
        if any(h in t for h in HEADER_KWS) and len(t) <= 6:
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?", t):
            continue
        # 含至少 1 汉字 → 中文名
        if re.search(r"[\u4e00-\u9fa5]", t) and re.fullmatch(r"[\u4e00-\u9fa5A-Za-z·/\-]{1,15}", t):
            name = t
            break
        # 纯英文名（接口人/兼职允许）
        if allow_english and re.fullmatch(r"[A-Za-z][A-Za-z\-·]{0,14}", t):
            name = t
            break
    return name, phone


def _extract_amounts(toks, phone):
    """从 token 提取所有小于 1e5 的数字（金额候选）"""
    nums = []
    for t in toks:
        if t == phone:
            continue
        # 单 token 内部找数字（防止"苏雨生18074169517"漏取）
        # 但要跳过手机号那部分
        remain = t
        if phone and phone in t:
            remain = t.replace(phone, "")
        for m in re.finditer(r"\b(\d+(?:\.\d+)?)\b", remain):
            v = float(m.group(1))
            iv = int(v) if v.is_integer() else v
            if 0 < v <= 99999:
                nums.append(iv)
    return nums


def _pick_amount(nums, category):
    """按类别选金额：
    玩家：若有 max==其余之和 → 取max；否则求和
    接口人/兼职：取最大
    """
    if not nums:
        return None
    if len(nums) == 1:
        return nums[0]
    if category == "玩家":
        mx = max(nums)
        rest = sum(nums) - mx
        if abs(mx - rest) < 0.01:  # 250+50=300 情形
            return mx
        return sum(nums)
    else:
        return max(nums)


def parse_checkin(text: str) -> dict:
    """智能解析签到表：兼容多子表、姓名电话粘连、多种分隔符"""
    meta = {}
    players, interfaces, parttime = [], [], []
    if not text.strip():
        return {"meta": meta, "players": players, "interfaces": interfaces, "parttime": parttime}

    _extract_meta(text, meta)

    current = "玩家"  # 默认

    for ln in text.splitlines():
        line = ln.strip()
        if not line:
            continue
        # 分隔符：tab / 竖线 / 分号 / 中英逗号 / 顿号 / 2+空格
        toks = re.split(r"[\t|；;，,、]+|\s{2,}", line)
        toks = [t.strip() for t in toks if t.strip()]
        if not toks:
            continue

        joined = " ".join(toks)

        # 找电话
        phone = None
        for t in toks:
            m = re.search(r"1\d{10}", t)
            if m:
                phone = m.group(0)
                break
        has_phone = phone is not None

        # 找小数字（≤99999，非电话）
        small_nums = _extract_amounts(toks, phone)

        # 精确类别切换：首格恰好是类别名（如"兼职  场次(200)  餐补(30)  金额"这种
        # 类别名+表头合并行），不管后面是否带数字，直接切类别并跳过本行（它是表头/声明行不是数据）
        CATEGORY_EXACT = {
            "玩家": "玩家", "接口人": "接口人", "渠道接口人": "接口人",
            "兼职": "兼职", "甄别": "兼职", "电访": "兼职", "邀约": "兼职",
            "海捞": "兼职", "实验室执行": "兼职", "街访执行": "兼职", "测试执行": "兼职",
        }
        if not has_phone and toks[0] in CATEGORY_EXACT:
            current = CATEGORY_EXACT[toks[0]]
            continue

        # 类别切换检测（无手机、数字≤1）
        new_cat = _detect_category(toks, joined, has_phone, len(small_nums))
        if new_cat and not has_phone:
            current = new_cat
            has_name = any(re.search(r"[\u4e00-\u9fa5]", t) and t not in ("玩家","接口人","兼职","渠道","甄别","电访","邀约","实验室执行") for t in toks)
            if len(small_nums) == 0 or not has_name:
                continue

        # 手机号触发：有手机意味着是真实人员，且通常是玩家
        # 若 current 不是玩家且没有明确类别信号，自动切回玩家
        if has_phone and current in ("兼职",):
            current = "玩家"

        # 判断是否表头行（含 2+ 表头关键字，且无手机）
        header_hits = sum(1 for t in toks if any(h in t for h in HEADER_KWS) and len(t) <= 6)
        if header_hits >= 2 and not has_phone:
            continue

        # 元信息行：不含手机号，且所有 token 都是稀缺/形式/项目号/时长/周期
        if not has_phone:
            all_meta = all(
                t in SCARCITY or t in TEST_FORMS or t in ("单日","多日","非单日")
                or re.fullmatch(r"[A-Za-z]{1,4}\d{5,}[A-Za-z0-9]*", t)
                or re.fullmatch(r"\d+(?:\.\d+)?[Hh]", t)
                or re.fullmatch(r"\d+(?:\.\d+)?", t)
                for t in toks
            )
            if all_meta:
                continue

        # 提取姓名+电话（接口人/兼职段允许英文名）
        allow_eng = current in ("接口人", "兼职")
        name, phone2 = _extract_name_phone(toks, allow_english=allow_eng)
        if phone2:
            phone = phone2

        if not name:
            continue

        amount = _pick_amount(small_nums, current)

        row = {"name": name, "phone": phone or "", "amount": amount}
        if current == "接口人":
            interfaces.append(row)
        elif current == "兼职":
            parttime.append(row)
        else:
            players.append(row)

    # 玩家默认金额+反查稀缺
    if players:
        amounts = [p["amount"] for p in players if p["amount"]]
        if amounts:
            # 众数当默认
            from collections import Counter
            most = Counter(amounts).most_common(1)[0][0]
            meta["player_amount"] = most
            if "transport" not in meta:
                meta["transport"] = DEFAULT_TRANSPORT
            meta["base"] = most - meta["transport"]

    period = meta.get("连续周期", "单日")
    tform = meta.get("测试形式", "实验室测试/座谈会")
    matches = infer_scarcity(period, tform, meta.get("测试时长(小时)"), meta.get("base"))
    if "样本稀缺性" not in meta:
        if len(matches) == 1:
            meta["样本稀缺性"] = matches[0]
        elif len(matches) > 1:
            meta["样本稀缺性_候选"] = matches

    return {"meta": meta, "players": players, "interfaces": interfaces, "parttime": parttime}


def _as_number(value, default=0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _reconcile_parttime_allowances(data: dict) -> bool:
    """把明确属于兼职场次的餐补/交补分摊进兼职收款人的实际应收金额。"""
    payee_rules = data.get("payee_rules") or {}
    parttime_payees = [item for item in (payee_rules.get("specific") or []) if item.get("type") == "兼职"]
    parttime_rows = ((data.get("parttime") or {}).get("rows") or [])
    other_rows = ((data.get("other") or {}).get("rows") or [])
    if not parttime_payees or not parttime_rows or not other_rows:
        return False

    allowance_rows = []
    for row in other_rows:
        content = str(row.get("发包内容") or "").replace(" ", "")
        if not any(token in content for token in ("餐补", "餐饮费", "餐费", "交通补", "交通费", "交补")):
            continue
        quantity = _as_number(row.get("数量"))
        total = _as_number(row.get("总金额(元)"))
        if quantity > 0 and total > 0:
            allowance_rows.append((quantity, total))
    if not allowance_rows:
        return False

    parttime_unit_count = sum(_as_number(row.get("测试场次/样本量")) for row in parttime_rows)
    parttime_base_total = sum(_as_number(row.get("总金额(元)")) for row in parttime_rows)
    payee_base_total = sum(_as_number(item.get("amount")) for item in parttime_payees)
    allowance_quantity = sum(quantity for quantity, _ in allowance_rows)
    allowance_total = sum(total for _, total in allowance_rows)
    if parttime_unit_count <= 0 or abs(allowance_quantity - parttime_unit_count) > 0.01:
        return False
    if abs(payee_base_total - parttime_base_total) > 0.01:
        # 当前收款人已含补贴，或无法证明补贴属于这些兼职人员时，绝不重复加。
        return False

    marker_key = f"{parttime_base_total:.2f}|{allowance_total:.2f}|{parttime_unit_count:.2f}"
    if (payee_rules.get("_parttime_allowance_reconciled") or {}).get("key") == marker_key:
        return False

    allocated = 0.0
    for index, item in enumerate(parttime_payees):
        base_amount = _as_number(item.get("amount"))
        if index == len(parttime_payees) - 1:
            allowance = round(allowance_total - allocated, 2)
        else:
            allowance = round(allowance_total * base_amount / payee_base_total, 2)
            allocated += allowance
        item["amount"] = round(base_amount + allowance, 2)

    payee_rules["_parttime_allowance_reconciled"] = {
        "key": marker_key,
        "allowance_total": allowance_total,
        "allowance_quantity": allowance_quantity,
        "note": "兼职餐补/交补已按场次费比例计入收款人实际应收金额",
    }
    data["payee_rules"] = payee_rules
    return True


def build_order(meta: dict, players: list, interfaces: list, parttime_rows: list = None, proxies: list = None) -> dict:
    """根据表单输入构建完整 EPC JSON"""
    project_id = meta.get("project_id", "")
    cost_center = meta.get("cost_center", "")
    series = meta.get("fanbao_series", "研究")
    ftype = meta.get("fanbao_type", "用户调研")
    test_form = meta.get("测试形式", "实验室测试/座谈会")
    cycle = meta.get("连续周期", "单日")
    scarcity = meta.get("样本稀缺性", "百万级")
    hours = meta.get("测试时长(小时)")
    base = meta.get("base")
    transport = meta.get("transport")
    player_amount = meta.get("player_amount")

    n = len(players)
    rows = []
    if base and n:
        rows.append({
            "测试形式": test_form, "连续周期": cycle, "礼金小类": "基础礼金",
            "样本稀缺性": scarcity, "测试时长(小时)": hours, "样本量": n,
            "总金额(元)": base * n,
        })
    if transport and n:
        rows.append({
            "测试形式": test_form, "连续周期": cycle, "礼金小类": "交通补贴",
            "样本量": n, "总金额(元)": transport * n,
        })

    # 转介费（人数 + 单价）
    ref_count = meta.get("referral_count") or 0
    ref_unit  = meta.get("referral_unit") or 50  # 默认单价 50
    ref_total_override = 0
    ref_is_auto = False  # 转介费是否是从接口人金额自动推断出来的（同一笔钱，不能再单独计入总额）
    # 优先使用接口人行中显式解析出的转介人数，例如“黄蔚 2 100”
    if not ref_count and interfaces:
        explicit_ref_count = sum((p.get("referral_count") or 0) for p in interfaces)
        if explicit_ref_count:
            ref_count = explicit_ref_count
            ref_total_override = sum((p.get("amount") or 0) for p in interfaces)
            ref_is_auto = True

    # 自动推断：未填转介人数时，用接口人应收款总和 / 单价
    if not ref_count and interfaces:
        total_iface_amt = sum((p.get("amount") or 0) for p in interfaces)
        if total_iface_amt:
            ref_count = round(total_iface_amt / ref_unit)
            ref_total_override = total_iface_amt  # 保住原始总额，避免四舍五入丢钱
            ref_is_auto = True
    if ref_count and ref_unit:
        row_total = ref_total_override or (ref_count * ref_unit)
        rows.append({
            "测试形式": test_form, "连续周期": cycle, "礼金小类": "转介费",
            "样本稀缺性": scarcity, "样本量": ref_count, "单价(元)": ref_unit,
            "总金额(元)": row_total,
        })

    specific = [
        {"name": p["name"], "type": "渠道接口人", "amount": p["amount"] or 0,
         "referral_count": p.get("referral_count")}
        for p in interfaces
    ]

    # 兼职人员 → 加入 payee 的 specific（类型=兼职）+ 兼职子表
    parttime_rows = parttime_rows or []
    pt_specific = [
        {"name": p["name"], "type": "兼职", "amount": p["amount"] or 0}
        for p in parttime_rows if p.get("name")
    ]
    pt_total = sum(x["amount"] for x in pt_specific)

    # 姓名→手机号映射（玩家/接口人/兼职都收进来），供自动化在平台上按手机号兜底匹配同一人
    known_phones = {}
    for p in list(players) + list(interfaces) + list(parttime_rows):
        nm, ph = p.get("name"), p.get("phone")
        if nm and ph:
            known_phones[nm] = ph

    total = (base * n if base else 0) + (transport * n if transport else 0)
    referral_total = (ref_total_override or (ref_count * ref_unit)) if (ref_count and ref_unit) else 0
    iface_total = sum(s["amount"] for s in specific)
    # 转介费若是从接口人金额自动推断而来，两者是同一笔钱，总额只能算一次
    grand = total + pt_total + (iface_total if ref_is_auto else referral_total + iface_total)

    # reimbursement_types
    rtypes = []
    if rows:
        rtypes.append("礼金(常规)")
    if parttime_rows:
        rtypes.append("兼职")

    # 兼职子表：合并同工作类型；简单起见每人一行、工作类型默认「测试执行」
    parttime_data = None
    if parttime_rows:
        parttime_data = {
            "rows": [{
                "工作类型": p.get("工作类型") or p.get("work_type") or p.get("type") or "其他测试支持",
                "工作难度": p.get("工作难度") or p.get("work_difficulty") or "",
                "测试场次/样本量": p.get("测试场次/样本量") or p.get("session_count") or 1,
                "总金额(元)": p.get("amount") or 0,
            } for p in parttime_rows if p.get("amount")]
        }

    result = {
        "project_id": project_id,
        "fanbao_series": series,
        "fanbao_type": ftype,
        "cost_center": cost_center,
        "screenshot_required": meta.get("screenshot_required", False),
        "screenshot_path": meta.get("screenshot_path"),
        "reimbursement_types": rtypes or ["礼金(常规)"],
        "gift_common": {"total_sample": n, "rows": rows} if rows else None,
        "gift_special": None, "questionnaire": None,
        "parttime": parttime_data,
        "other": None,
        "payee_rules": {
            "expected_total": n + len(interfaces) + len(pt_specific),
            "expected_breakdown": {"玩家": n, "渠道接口人": len(interfaces), "兼职": len(pt_specific)},
            "default_player_amount": player_amount,
            "known_players": [p["name"] for p in players if p.get("name")],
            "known_phones": known_phones,
            "specific": specific + pt_specific,
            "proxies": proxies or [],
        },
        "expense_note": "",
        "grand_total": grand,
    }
    result["expense_note"] = build_standard_expense_note(result)
    return result


# ─────────────────────────────────────────────
# 自动化运行管理
# ─────────────────────────────────────────────

class RunState:
    def __init__(self):
        self.log = []           # [(kind, msg)]
        self.waiting = None     # 当前等待输入的 prompt
        self.queue = queue.Queue()
        self.screenshots = []   # 文件名列表
        self.payee_review = None
        self.done = False
        self.error = None


runs: dict = {}  # run_id -> RunState


def _step_from_prompt(prompt: str):
    """按确认文案粗判单据当前阶段（供批次状态展示）"""
    if "第3页" in prompt:
        return "READY_TO_SUBMIT"
    if "第2页" in prompt:
        return "WAITING_PAGE1_APPROVAL"
    return "WAITING_CONFIRMATION"


def _make_web_input(state: RunState, order_id: str = None):
    def web_input(prompt=""):
        state.log.append(("prompt", prompt))
        state.waiting = prompt
        if order_id:
            store.append_event(order_id, "prompt", prompt)
            step = _step_from_prompt(prompt)
            store.update_order(order_id, status=step, current_step=prompt[:120])
        resp = state.queue.get()  # 无超时，一直等你在网页确认
        state.waiting = None
        state.log.append(("input", f">>> {resp}"))
        if order_id:
            store.append_event(order_id, "input", f">>> {resp}")
        return resp
    return web_input


def _make_web_print(state: RunState, order_id: str = None):
    real_print = builtins.print
    def web_print(*args, **kwargs):
        msg = " ".join(str(a) for a in args)
        if msg.startswith("__PAYEE_REVIEW__"):
            try:
                state.payee_review = json.loads(msg[len("__PAYEE_REVIEW__"):])
            except Exception:
                state.payee_review = None
            if order_id:
                store.append_event(order_id, "payee_review", msg[:2000])
            return
        state.log.append(("log", msg))
        # 检测截图
        m = re.search(r"截图[:：]\s*(\S+\.png)", msg)
        if m:
            state.screenshots.append(m.group(1))
        if order_id:
            store.append_event(order_id, "log", msg[:1000])
        real_print(*args, **kwargs)
    return web_print


def _run_thread(run_id: str, data: dict, order_id: str = None):
    state = runs[run_id]
    if order_id:
        store.update_order(order_id, status="RUNNING", error="")
        store.append_event(order_id, "status", "RUNNING")
    if AUTOMATION_LOCK.locked():
        msg = "⏳ 已有其他单据正在自动填写，本单排队等待…"
        state.log.append(("log", msg))
        if order_id:
            store.append_event(order_id, "log", msg)
    AUTOMATION_LOCK.acquire()  # EPC 写入保持单线程，避免串单
    real_input = builtins.input
    real_print = builtins.print
    builtins.input = _make_web_input(state, order_id)
    builtins.print = _make_web_print(state, order_id)
    try:
        import importlib
        import automation
        importlib.reload(automation)  # 每次运行重新加载，代码改动实时生效
        asyncio.run(automation.run_single_order(data))
        if order_id:
            store.update_order(order_id, status="COMPLETED", current_step="")
            store.append_event(order_id, "status", "COMPLETED")
    except Exception as e:
        state.error = f"{type(e).__name__}: {e}"
        state.log.append(("error", state.error))
        if order_id:
            store.update_order(order_id, status="FAILED", error=state.error)
            store.append_event(order_id, "error", state.error)
    finally:
        builtins.input = real_input
        builtins.print = real_print
        state.done = True
        AUTOMATION_LOCK.release()


# ─────────────────────────────────────────────
# 路由
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", mode="home", order_id="")


@app.route("/single")
def single_workspace():
    """单笔工作台：保留原有录入、草稿确认和自动填写流程。"""
    return render_template(
        "index.html",
        mode="single",
        order_id=(request.args.get("order_id") or "").strip(),
    )


@app.route("/batch")
def batch_workspace():
    """批次工作台：集中查看队列、运行状态和待确认操作。"""
    return render_template("index.html", mode="batch", order_id="")


@app.route("/parse", methods=["POST"])
def parse():
    text = (request.json or {}).get("text", "")
    return jsonify(parse_checkin(text))


@app.route("/infer", methods=["POST"])
def infer():
    """前端改字段时用：给 (period, test_form, hours, total, transport) 反推 base + scarcity"""
    b = request.json or {}
    period = b.get("period", "单日")
    tform = b.get("test_form", "实验室测试/座谈会")
    hours = b.get("hours")
    total = b.get("total")
    transport = b.get("transport") if b.get("transport") is not None else DEFAULT_TRANSPORT
    base = None
    if total is not None:
        base = total - transport
    matches = infer_scarcity(period, tform, hours, base) if base is not None else []
    return jsonify({"base": base, "transport": transport, "scarcity_candidates": matches})


@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "无文件"}), 400
    dst_dir = BASE_DIR / "screenshots"
    dst_dir.mkdir(exist_ok=True)
    # 保留原文件名，避免冲突加时间戳
    import time
    stem = Path(f.filename).stem or "screenshot"
    ext = Path(f.filename).suffix or ".png"
    fname = f"{stem}_{int(time.time())}{ext}"
    dst = dst_dir / fname
    f.save(str(dst))
    return jsonify({"path": str(dst.resolve())})


@app.route("/ratetable")
def ratetable():
    return jsonify({"rows": RATE_TABLE, "default_transport": DEFAULT_TRANSPORT})


@app.route("/generate", methods=["POST"])
def generate():
    body = request.json or {}
    data = build_order(body.get("meta", {}), body.get("players", []),
                       body.get("interfaces", []), body.get("parttime", []), body.get("proxies", []))
    return jsonify(data)


@app.route("/ai_draft", methods=["POST"])
def ai_draft():
    """Use the local AI agent as the reasoning layer, then return normalized EPC JSON."""
    body = request.json or {}
    text = body.get("text", "")
    overrides = body.get("meta", {}) or {}
    try:
        import model_parser

        parsed = model_parser.parse_checkin_stateless(text)
        meta = {}
        _extract_meta(text, meta)
        meta.update(parsed.get("meta") or {})

        def put(src_key, dst_key=None, numeric=False, boolean=False):
            dst_key = dst_key or src_key
            value = overrides.get(src_key)
            if value in (None, ""):
                return
            if boolean:
                meta[dst_key] = bool(value)
            elif numeric:
                try:
                    meta[dst_key] = int(value)
                except (TypeError, ValueError):
                    meta[dst_key] = value
            else:
                meta[dst_key] = value

        put("project_id")
        put("cost_center")
        put("fanbao_series")
        put("fanbao_type")
        put("test_form", "测试形式")
        put("cycle", "连续周期")
        put("测试时长(小时)", numeric=True)
        put("player_amount", numeric=True)
        put("base", numeric=True)
        put("transport", numeric=True)
        put("referral_count", numeric=True)
        put("referral_unit", numeric=True)
        put("screenshot_required", boolean=True)
        put("screenshot_path")

        data = build_order(
            meta,
            parsed.get("players", []),
            parsed.get("interfaces", []),
            parsed.get("parttime", []),
            parsed.get("proxies", []),
        )
        if overrides.get("budget_owner"):
            data["budget_owner"] = overrides.get("budget_owner")
        data["ai_parse_source"] = "model_parser.parse_checkin_stateless"
        return jsonify({"ok": True, "data": data, "parsed": parsed})
    except Exception as e:
        return jsonify({
            "ok": False,
            "error": f"AI 标准化解析失败: {type(e).__name__}: {e}",
            "data": None,
        }), 500

@app.route("/draft")
def draft():
    """Return the latest backend-normalized draft order for frontend confirmation."""
    p = BASE_DIR / "draft_order.json"
    if not p.exists():
        return jsonify({"ok": False, "data": None})
    with open(p, encoding="utf-8-sig") as f:
        return jsonify({"ok": True, "data": json.load(f)})


@app.route("/pending", methods=["POST"])
def pending():
    """Save raw sign-in text for Codex to parse in the current conversation."""
    body = request.json or {}
    text = body.get("text", "")
    meta = body.get("meta", {}) or {}
    if not text.strip():
        return jsonify({"ok": False, "error": "原始输入为空"}), 400
    payload = {
        "text": text,
        "meta": meta,
        "status": "waiting_for_codex",
    }
    with open(BASE_DIR / "pending_input.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return jsonify({
        "ok": True,
        "file": "pending_input.json",
        "message": "已保存。请回到 Codex 输入：解析刚保存的这单",
    })


@app.route("/pending")
def pending_status():
    p = BASE_DIR / "pending_input.json"
    if not p.exists():
        return jsonify({"ok": False, "data": None})
    with open(p, encoding="utf-8-sig") as f:
        return jsonify({"ok": True, "data": json.load(f)})


@app.route("/run", methods=["POST"])
def run():
    data = request.json or {}
    order_id = data.get("order_id")
    run_id = order_id or uuid.uuid4().hex[:8]
    runs[run_id] = RunState()
    t = threading.Thread(target=_run_thread, args=(run_id, data, order_id), daemon=True)
    t.start()
    return jsonify({"run_id": run_id})


@app.route("/status/<run_id>")
def status(run_id):
    st = runs.get(run_id)
    if not st:
        return jsonify({"error": "未知 run_id"}), 404
    return jsonify({
        "log": st.log[-200:],
        "waiting": st.waiting,
        "payee_review": st.payee_review,
        "screenshots": st.screenshots,
        "done": st.done,
        "error": st.error,
    })


@app.route("/respond/<run_id>", methods=["POST"])
def respond(run_id):
    st = runs.get(run_id)
    if not st:
        return jsonify({"error": "未知 run_id"}), 404
    val = (request.json or {}).get("value", "")
    if isinstance(val, (dict, list)):
        st.payee_review = None
        val = json.dumps(val, ensure_ascii=False)
    st.queue.put(val)
    return jsonify({"ok": True})


@app.route("/screenshot/<path:fname>")
def screenshot(fname):
    p = BASE_DIR / fname
    if not p.exists():
        return ("未找到", 404)
    return send_file(str(p), mimetype="image/png")


@app.route("/save", methods=["POST"])
def save():
    data = request.json or {}
    fname = f"order_{data.get('project_id','unknown')}.json"
    with open(BASE_DIR / fname, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return jsonify({"ok": True, "file": fname})



# ─────────────────────────────────────────────
# 批次接口（多单并行，第 1/2 阶段）
# ─────────────────────────────────────────────

PROJECT_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]\d{8}[A-Za-z]?)(?![A-Za-z0-9])")


def _is_project_id(value: str) -> bool:
    return bool(PROJECT_ID_PATTERN.fullmatch((value or "").strip()))


def _split_orders_by_project(text: str) -> list:
    """仅按 EPC 项目号切分：1 个字母 + 8 位数字，末尾可带 1 个字母。"""
    spans = [(match.start(), match.group(1)) for match in PROJECT_ID_PATTERN.finditer(text)]
    if not spans:
        return []

    orders, seen = [], set()
    for index, (start, project_id) in enumerate(spans):
        if project_id in seen:
            continue
        seen.add(project_id)
        end = len(text)
        for next_start, next_project_id in spans[index + 1:]:
            if next_project_id != project_id:
                end = next_start
                break
        orders.append({"project_id": project_id, "source_text": text[start:end].strip()})
    return orders


@app.route("/api/batches", methods=["GET"])
def api_batches():
    batches = store.list_batches()
    for b in batches:
        b["orders_count"] = len(store.list_orders(b["batch_id"]))
    return jsonify({"ok": True, "batches": batches})


@app.route("/api/batches", methods=["POST"])
def api_create_batch():
    body = request.json or {}
    raw = body.get("text", "")
    orders = body.get("orders")
    if orders is None and raw.strip():
        orders = _split_orders_by_project(raw)
    orders = orders or []
    if not orders:
        return jsonify({"ok": False, "error": "未识别到项目号。单号仅支持 1 个字母 + 8 位数字，末尾可带 1 个字母，例如 G26060703、S26070001 或 G26070035C。"}), 400
    invalid = [str(order.get("project_id") or "") for order in orders if not _is_project_id(order.get("project_id"))]
    if invalid:
        return jsonify({"ok": False, "error": "存在不符合项目号规则的单据：" + "、".join(invalid)}), 400
    batch = store.create_batch(title=body.get("title", ""), orders=orders)
    return jsonify({"ok": True, "batch": batch})


@app.route("/api/batches/<batch_id>", methods=["GET"])
def api_get_batch(batch_id: str):
    b = store.get_batch(batch_id)
    if not b:
        return jsonify({"ok": False, "error": "批次不存在"}), 404
    return jsonify({"ok": True, "batch": b})


@app.route("/api/batches/<batch_id>", methods=["DELETE"])
def api_delete_batch(batch_id: str):
    try:
        result = store.delete_batch(batch_id)
    except KeyError:
        return jsonify({"ok": False, "error": "批次不存在"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    return jsonify({"ok": True, **result})


@app.route("/api/batches/<batch_id>/orders", methods=["POST"])
def api_add_order(batch_id: str):
    if not store.get_batch(batch_id):
        return jsonify({"ok": False, "error": "批次不存在"}), 404
    body = request.json or {}
    if not _is_project_id(body.get("project_id")):
        return jsonify({"ok": False, "error": "项目号仅支持 1 个字母 + 8 位数字，末尾可带 1 个字母，例如 G26060703、S26070001 或 G26070035C。"}), 400
    o = store.add_order(batch_id, body)
    return jsonify({"ok": True, "order": o})


@app.route("/api/batches/<batch_id>/save-input", methods=["POST"])
def api_save_batch_input(batch_id: str):
    if not store.get_batch(batch_id):
        return jsonify({"ok": False, "error": "批次不存在"}), 404
    p = store.save_batch_input(batch_id)
    return jsonify({
        "ok": True,
        "file": str(p),
        "message": f"已保存批次 {batch_id} 的原始资料。请回到 Codex 输入：解析批次 {batch_id}，并写入该批次草稿",
    })


@app.route("/api/batches/<batch_id>/draft", methods=["GET"])
def api_load_batch_draft(batch_id: str):
    if not store.get_batch(batch_id):
        return jsonify({"ok": False, "error": "批次不存在"}), 404
    codex = store.load_codex_draft(batch_id)
    orders = store.list_orders(batch_id)
    if not codex:
        return jsonify({"ok": True, "batch_id": batch_id, "draft_found": False, "orders": orders})
    for order in orders:
        entry = codex.get(order["order_id"])
        if not entry or not isinstance(entry, dict):
            continue
        data = entry.get("data") if "data" in entry else entry
        if not isinstance(data, dict):
            continue
        allowance_reconciled = _reconcile_parttime_allowances(data)
        data["expense_note"] = build_standard_expense_note(data)
        warnings = list(entry.get("warnings", []) if isinstance(entry, dict) else [])
        missing = list(entry.get("missing", []) if isinstance(entry, dict) else [])
        for field in missing:
            message = f"待补字段：{field}"
            if message not in warnings:
                warnings.append(message)
        ready_for_review = bool(entry.get("ready_for_review", True))
        ready_for_epc = bool(entry.get("ready_for_epc", entry.get("ready", False)))
        parse_issue_lines = []
        if entry.get("error"):
            parse_issue_lines.append(f"Codex 解析错误：{entry['error']}")
        parse_issue_lines.extend(str(item) for item in warnings)
        parse_issue_lines.extend(f"待补字段：{field}" for field in missing)
        requires_human_review = bool(parse_issue_lines)
        if requires_human_review:
            ready_for_epc = False
        parse_meta = dict(order.get("meta") or {})
        parse_meta.update({
            "codex_parse_status": entry.get("parse_status", "completed"),
            "ready_for_review": ready_for_review,
            "ready_for_epc": ready_for_epc,
            "codex_missing": missing,
            "codex_assumptions": entry.get("assumptions", []),
            "codex_error": entry.get("error"),
        })
        status = "READY" if ready_for_epc else "WAITING_CONFIRMATION"
        current_step = (
            "Codex 解析待人工核验：" + "；".join(parse_issue_lines)
            if requires_human_review else ""
        )
        store.update_order(
            order["order_id"],
            draft=data,
            warnings=warnings,
            meta=parse_meta,
            status=status,
            current_step=current_step,
            project_id=data.get("project_id") or order["project_id"],
        )
        for w in warnings:
            store.append_event(order["order_id"], "warning", str(w))
        if allowance_reconciled:
            store.append_event(order["order_id"], "draft", "已将兼职餐补/交补按场次比例计入兼职收款人实际应收金额")
    orders = store.list_orders(batch_id)
    has_ready = any(o["status"] == "READY" for o in orders)
    store.update_batch_status(batch_id, "READY" if has_ready else "WAITING_CODEX")
    return jsonify({"ok": True, "batch_id": batch_id, "draft_found": True, "orders": orders})


@app.route("/api/orders/<order_id>", methods=["GET"])
def api_get_order(order_id: str):
    o = store.get_order(order_id)
    if not o:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    return jsonify({"ok": True, "order": o})


@app.route("/api/batches/<batch_id>/orders/<order_id>", methods=["DELETE"])
def api_delete_batch_order(batch_id: str, order_id: str):
    order = store.get_order(order_id)
    if not order or order["batch_id"] != batch_id:
        return jsonify({"ok": False, "error": "该单据不属于当前批次或已删除"}), 404
    try:
        result = store.delete_order(batch_id, order_id)
    except ValueError as error:
        return jsonify({"ok": False, "error": str(error)}), 409
    return jsonify({"ok": True, **result})


@app.route("/api/batches/<batch_id>/reset-execution", methods=["POST"])
def api_reset_batch_execution(batch_id: str):
    batch = store.get_batch(batch_id)
    if not batch:
        return jsonify({"ok": False, "error": "批次不存在"}), 404
    order_ids = [order['order_id'] for order in batch['orders']]
    # 用户明确要求重置：先终止本地自动化任务和标签页，再清理本地状态。
    automation_service.cancel_orders(order_ids)
    try:
        result = store.reset_batch_execution(batch_id)
    except KeyError as error:
        return jsonify({"ok": False, "error": str(error)}), 404
    screenshot_dir = BASE_DIR / 'screenshots'
    deleted_screenshots = 0
    if screenshot_dir.exists():
        for order_id in order_ids:
            safe_order = re.sub(r"[^A-Za-z0-9_-]", "_", order_id)
            for screenshot in screenshot_dir.glob(f"{safe_order}_*.png"):
                try:
                    screenshot.unlink()
                    deleted_screenshots += 1
                except OSError:
                    pass
    return jsonify({"ok": True, **result, "deleted_screenshots": deleted_screenshots})


@app.route("/api/orders/<order_id>/draft", methods=["POST"])
def api_save_order_draft(order_id: str):
    o = store.get_order(order_id)
    if not o:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    body = request.json or {}
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    _reconcile_parttime_allowances(data)
    data["expense_note"] = build_standard_expense_note(data)
    store.update_order(order_id, draft=data, status="WAITING_CONFIRMATION",
                       project_id=data.get("project_id") or o["project_id"])
    store.append_event(order_id, "draft", f"前端保存/修改草稿，总额 {data.get('grand_total', '?')} 元")
    return jsonify({"ok": True, "order": store.get_order(order_id)})


@app.route("/api/batches/<batch_id>/orders/<order_id>/draft", methods=["POST"])
def api_save_batch_order_draft(batch_id: str, order_id: str):
    """保存单据草稿：更新数据库，并写回覆盖批次 codex_draft.json。"""
    if not store.get_batch(batch_id):
        return jsonify({"ok": False, "error": "批次不存在"}), 404
    o = store.get_order(order_id)
    if not o:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    body = request.json or {}
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    if not isinstance(data, dict) or not (data.get("project_id") or "").strip():
        return jsonify({"ok": False, "error": "草稿缺少 project_id"}), 400
    allowance_reconciled = _reconcile_parttime_allowances(data)
    data["expense_note"] = build_standard_expense_note(data)
    store.update_order(order_id, draft=data, status="WAITING_CONFIRMATION",
                       project_id=data.get("project_id") or o["project_id"])
    store.append_event(order_id, "draft",
                       "前端保存/修改草稿（覆盖批次文件），总额 " + str(data.get('grand_total', '?')) + " 元")
    if allowance_reconciled:
        store.append_event(order_id, "draft", "已将兼职餐补/交补按场次比例计入兼职收款人实际应收金额")
    codex = store.load_codex_draft(batch_id)
    entry = codex.get(order_id) or {}
    entry["order_id"] = order_id
    entry["project_id"] = data.get("project_id") or entry.get("project_id") or o["project_id"]
    entry["data"] = data
    entry["parse_status"] = "needs_review"
    entry["ready_for_review"] = True
    entry["ready_for_epc"] = False
    entry["ready"] = False
    entry.setdefault("warnings", [])
    entry.setdefault("missing", [])
    entry.setdefault("assumptions", [])
    entry.setdefault("error", None)
    codex[order_id] = entry
    p = store.codex_draft_path(batch_id)
    payload = {"batch_id": batch_id, "schema_version": 2, "orders": [codex[k] for k in codex]}
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "file": str(p), "order": store.get_order(order_id)})

def _validate_draft(data: dict) -> list:
    """草稿校验：返回 [{level, text}]；level=err 时不允许进入队列"""
    issues = []
    if not (data.get("project_id") or "").strip():
        issues.append({"level": "err", "text": "缺少项目号"})
    if not (data.get("cost_center") or "").strip():
        issues.append({"level": "err", "text": "缺少成本归属，不能开始 EPC 自动填写"})
    if data.get("screenshot_required") and not data.get("screenshot_path"):
        issues.append({"level": "err", "text": "需要产品确认截图，但尚未提供截图文件"})
    gc = data.get("gift_common") or {}
    rows = gc.get("rows") or []
    pt = data.get("parttime") or {}
    ot = data.get("other") or {}
    detail_total = sum(float(r.get("总金额(元)") or 0) for r in rows)
    detail_total += sum(float(r.get("总金额(元)") or 0) for r in (pt.get("rows") or []))
    detail_total += sum(float(r.get("总金额(元)") or 0) for r in (ot.get("rows") or []))
    grand = data.get("grand_total")
    if grand is not None:
        try:
            grand = float(grand)
            if abs(grand - detail_total) > 0.01:
                issues.append({"level": "warn", "text": f"报销明细合计 {detail_total:g} 与总额 {grand:g} 不一致"})
        except (TypeError, ValueError):
            issues.append({"level": "err", "text": "报销总额不是数字"})
    pr = data.get("payee_rules") or {}
    for proxy in pr.get("proxies") or []:
        source_name = proxy.get("source_name") or proxy.get("source") or ""
        proxy_name = proxy.get("proxy_name") or proxy.get("proxy") or ""
        if not source_name or not proxy_name:
            issues.append({"level": "err", "text": "存在不完整的代收关系（需来源人和代收人）"})
        elif source_name == proxy_name:
            issues.append({"level": "err", "text": f"代收关系 {source_name} 的来源人与代收人不能相同"})
    specific = pr.get("specific") or []
    iface = [s for s in specific if s.get("type") == "渠道接口人"]
    pt_payees = [s for s in specific if s.get("type") == "兼职"]
    ref = [r for r in rows if r.get("礼金小类") == "转介费"]
    if iface and not ref:
        issues.append({"level": "err", "text": "有接口人但缺少转介费明细"})
    if pt_payees and not (pt.get("rows") or []):
        issues.append({"level": "err", "text": "有兼职收款人但缺少国内兼职明细"})
    players = pr.get("known_players") or []
    known_phones = pr.get("known_phones") or {}
    no_phone = [x for x in players if not (known_phones.get(x) or "").strip()]
    if no_phone:
        issues.append({"level": "warn", "text": f"{len(no_phone)} 名玩家缺手机号，需人工确认匹配"})
    for s in specific:
        if not (s.get("name") or "").strip():
            issues.append({"level": "err", "text": "存在无姓名的收款人"})
        if s.get("amount") in (None, ""):
            issues.append({"level": "err", "text": f"收款人 {s.get('name')} 缺少应填金额"})
    payee_total = len(players) * float(pr.get("default_player_amount") or 0)
    payee_total += sum(float(s.get("amount") or 0) for s in specific)
    if grand is not None:
        if abs(grand - payee_total) > 0.01:
            issues.append({"level": "warn", "text": f"收款人合计 {payee_total:g} 与报销总额 {grand:g} 不一致"})
    return issues


@app.route("/api/orders/<order_id>/confirm", methods=["POST"])
def api_confirm_order(order_id: str):
    o = store.get_order(order_id)
    if not o:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    if not o["draft"]:
        return jsonify({"ok": False, "error": "该单据还没有草稿，请先加载 Codex 批次草稿或手动编辑"}), 400
    issues = _validate_draft(o["draft"])
    errs = [i for i in issues if i["level"] == "err"]
    if errs:
        return jsonify({"ok": False, "error": "存在必须修正的问题", "issues": issues}), 400
    meta = dict(o.get("meta") or {})
    meta["ready_for_review"] = True
    meta["ready_for_epc"] = True
    meta["codex_parse_status"] = "completed"
    store.update_order(order_id, status="READY", current_step="", error="", meta=meta)
    if o.get("batch_id"):
        codex = store.load_codex_draft(o["batch_id"])
        entry = codex.get(order_id)
        if entry:
            entry["data"] = o["draft"]
            entry["parse_status"] = "completed"
            entry["ready_for_review"] = True
            entry["ready_for_epc"] = True
            entry["ready"] = True
            entry["missing"] = []
            entry["error"] = None
            codex[order_id] = entry
            payload = {"batch_id": o["batch_id"], "schema_version": 2, "orders": [codex[key] for key in codex]}
            store.codex_draft_path(o["batch_id"]).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    store.append_event(order_id, "status", "READY 已确认可执行")
    return jsonify({"ok": True, "order": store.get_order(order_id), "issues": issues})


@app.route("/api/orders/<order_id>/start", methods=["POST"])
def api_start_order(order_id: str):
    o = store.get_order(order_id)
    if not o:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    if o["status"] not in ("READY", "FAILED"):
        return jsonify({"ok": False, "error": f"当前状态 {o['status']} 不可启动，请先确认可执行"}), 400
    # 每次从已结束/失败状态重新启动都视为新一轮填报：不沿用上一轮人工覆盖。
    draft, overrides_cleared = clear_execution_overrides(o["draft"])
    store.update_order(order_id, draft=draft, status="READY", current_step="", error="")
    if overrides_cleared:
        store.append_event(order_id, "status", "新一轮填报：已清除上一轮人工确认、保留、同人关联和忽略项")
    run = automation_service.submit(order_id, draft)
    return jsonify({"ok": True, "run_id": order_id})


@app.route("/api/orders/<order_id>/retry", methods=["POST"])
def api_retry_order(order_id: str):
    o = store.get_order(order_id)
    if not o:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    if o["status"] in (
        "QUEUED", "RUNNING", "WAITING_PRECHECK_APPROVAL", "WAITING_PAGE1_APPROVAL",
        "FILLING_PAYEES", "WAITING_PAGE2_APPROVAL", "VERIFYING_PAYEES",
        "READY_TO_SUBMIT", "PAUSE_REQUESTED", "PAUSED",
    ):
        return jsonify({"ok": False, "error": "单据正在执行或等待确认中，不能重试"}), 400
    draft, overrides_cleared = clear_execution_overrides(o["draft"])
    store.update_order(
        order_id,
        draft=draft,
        status="READY" if draft else "WAITING_CODEX",
        current_step="",
        error="",
    )
    message = "已重置，等待重新开始"
    if overrides_cleared:
        message += "；已清除上一轮人工运行期操作"
    store.append_event(order_id, "status", message)
    return jsonify({"ok": True, "order": store.get_order(order_id)})


@app.route("/api/orders/<order_id>/status", methods=["GET"])
def api_order_status(order_id: str):
    o = store.get_order(order_id)
    if not o:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    worker_state = automation_service.snapshot(order_id)
    st = runs.get(order_id)
    payload = {
        "order": o,
        "events": store.list_events(order_id, 300),
        "verification": store.list_payee_verifications(order_id),
        "running": bool(worker_state and worker_state.get("running")) or bool(st and not st.done),
        "interrupted": (
            not st
            and o["status"] == "REQUIRES_ATTENTION"
            and "服务已重启" in (o["error"] or "")
        ),
    }
    if worker_state:
        payload.update(worker_state)
    elif st:
        payload.update({
            "log": st.log[-200:],
            "waiting": st.waiting,
            "payee_review": st.payee_review,
            "screenshots": st.screenshots,
            "done": st.done,
            "error": st.error,
        })
    return jsonify({"ok": True, **payload})


@app.route("/api/orders/<order_id>/history", methods=["DELETE"])
def api_clear_order_history(order_id: str):
    order = store.get_order(order_id)
    if not order:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    if order["status"] in ACTIVE_ORDER_STATUSES or not automation_service.clear_history(order_id):
        return jsonify({"ok": False, "error": "单据正在执行、暂停或等待确认中，不能清除记录"}), 409
    store.clear_order_history(order_id)
    safe_order = re.sub(r"[^A-Za-z0-9_-]", "_", order_id)
    deleted_screenshots = 0
    screenshot_dir = BASE_DIR / "screenshots"
    if screenshot_dir.exists():
        for screenshot in screenshot_dir.glob(f"{safe_order}_*.png"):
            try:
                screenshot.unlink()
                deleted_screenshots += 1
            except OSError:
                pass
    store.update_order(order_id, current_step="", error="")
    return jsonify({"ok": True, "deleted_screenshots": deleted_screenshots})


@app.route("/api/orders/<order_id>/respond", methods=["POST"])
def api_respond_order(order_id: str):
    val = (request.json or {}).get("value", "")
    if automation_service.respond(order_id, val):
        if isinstance(val, (dict, list)):
            val = json.dumps(val, ensure_ascii=False)
        store.append_event(order_id, "input", f">>> {str(val)[:200]}")
        return jsonify({"ok": True})

    st = runs.get(order_id)
    if not st:
        return jsonify({"ok": False, "error": "该单据没有正在运行的任务"}), 404
    if isinstance(val, (dict, list)):
        st.payee_review = None
        val = json.dumps(val, ensure_ascii=False)
    st.queue.put(val)
    store.append_event(order_id, "input", f">>> {val[:200]}")
    return jsonify({"ok": True})


@app.route("/api/orders/<order_id>/pause", methods=["POST"])
def api_pause_order(order_id: str):
    if not store.get_order(order_id):
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    if not automation_service.request_pause(order_id):
        return jsonify({"ok": False, "error": "该单据当前没有可暂停的自动化任务"}), 409
    return jsonify({"ok": True, "order": store.get_order(order_id)})


@app.route("/api/orders/<order_id>/resume", methods=["POST"])
def api_resume_order(order_id: str):
    if not store.get_order(order_id):
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    if not automation_service.resume(order_id):
        return jsonify({"ok": False, "error": "该单据当前没有可继续的自动化任务"}), 409
    return jsonify({"ok": True, "order": store.get_order(order_id)})


@app.route("/api/orders/<order_id>/restart", methods=["POST"])
def api_restart_order(order_id: str):
    o = store.get_order(order_id)
    if not o:
        return jsonify({"ok": False, "error": "单据不存在"}), 404
    if o["status"] in (
        "QUEUED", "RUNNING", "WAITING_PRECHECK_APPROVAL", "WAITING_PAGE1_APPROVAL",
        "FILLING_PAYEES", "WAITING_PAGE2_APPROVAL", "VERIFYING_PAYEES",
        "READY_TO_SUBMIT", "PAUSE_REQUESTED", "PAUSED",
    ):
        return jsonify({"ok": False, "error": "单据仍在运行、暂停或等待确认中，请先处理当前任务"}), 409
    if not o["draft"]:
        return jsonify({"ok": False, "error": "该单据没有可重新填报的草稿"}), 400
    draft, overrides_cleared = clear_execution_overrides(o["draft"])
    store.save_payee_verifications(order_id, [])
    store.update_order(order_id, draft=draft, status="READY", current_step="", error="")
    message = "重新填报：已清除旧核验结果并重新进入队列"
    if overrides_cleared:
        message += "；已清除上一轮人工确认、保留、同人关联和忽略项"
    store.append_event(order_id, "status", message)
    automation_service.submit(order_id, draft)
    return jsonify({"ok": True, "run_id": order_id, "order": store.get_order(order_id)})




if __name__ == "__main__":
    # 不开 debug（多线程会重复）
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
