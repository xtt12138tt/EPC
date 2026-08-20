'''批次持久化存储：SQLite 状态 + data/batches/{batch_id}/ 文件交接。

职责：
  - 批次/单据/事件/核验记录全部落 SQLite，服务重启可恢复；
  - 与 Codex 的交接文件（input.json / codex_draft.json）按批次目录存放；
  - 保留旧单文件交接方式（pending_input.json / draft_order.json）作为兼容入口。
'''
import json
import re
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / 'data'
BATCHES_DIR = DATA_DIR / 'batches'
DB_PATH = DATA_DIR / 'epc_agent.db'

# 单据状态（与方案一致）
ORDER_STATUSES = [
    'DRAFT', 'WAITING_CODEX', 'WAITING_CONFIRMATION', 'READY',
    'QUEUED', 'RUNNING', 'WAITING_PRECHECK_APPROVAL',
    'WAITING_PAGE1_APPROVAL', 'FILLING_PAYEES', 'WAITING_PAGE2_APPROVAL',
    'VERIFYING_PAYEES', 'REQUIRES_ATTENTION', 'READY_TO_SUBMIT',
    'PAUSE_REQUESTED', 'PAUSED', 'COMPLETED', 'FAILED',
]

BATCH_STATUSES = ['WAITING_CODEX', 'DRAFTING', 'READY', 'PROCESSING', 'DONE']

ACTIVE_ORDER_STATUSES = {
    'QUEUED', 'RUNNING', 'WAITING_PRECHECK_APPROVAL',
    'WAITING_PAGE1_APPROVAL', 'FILLING_PAYEES', 'WAITING_PAGE2_APPROVAL',
    'VERIFYING_PAYEES', 'READY_TO_SUBMIT', 'PAUSE_REQUESTED', 'PAUSED',
}


def _now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _json_dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _json_loads(text: str, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


EXECUTION_OVERRIDE_KEYS = {
    '_ignored_verification_issues',
    '_manual_total_accepted',
}


def clear_execution_overrides(draft: dict) -> tuple[dict, bool]:
    """清除仅对一次自动填报有效的人工运行期决定。"""
    if not isinstance(draft, dict):
        return draft, False
    rules = draft.get('payee_rules')
    if not isinstance(rules, dict):
        return draft, False
    removed = False
    for key in list(rules):
        if key.startswith('_manual_') or key in EXECUTION_OVERRIDE_KEYS:
            rules.pop(key, None)
            removed = True
    return draft, removed

class Store:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript('''
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id   TEXT PRIMARY KEY,
                    status     TEXT NOT NULL DEFAULT 'WAITING_CODEX',
                    title      TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS orders (
                    order_id     TEXT PRIMARY KEY,
                    batch_id     TEXT NOT NULL,
                    project_id   TEXT DEFAULT '',
                    status       TEXT NOT NULL DEFAULT 'DRAFT',
                    source_text  TEXT DEFAULT '',
                    meta_json    TEXT DEFAULT '{}',
                    draft_json   TEXT DEFAULT '{}',
                    current_step TEXT DEFAULT '',
                    warnings_json TEXT DEFAULT '[]',
                    error        TEXT DEFAULT '',
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_orders_batch ON orders(batch_id);
                CREATE TABLE IF NOT EXISTS order_events (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id   TEXT NOT NULL,
                    kind       TEXT NOT NULL,
                    message    TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_order ON order_events(order_id);
                CREATE TABLE IF NOT EXISTS payee_verifications (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id        TEXT NOT NULL,
                    payee_id        TEXT DEFAULT '',
                    name            TEXT DEFAULT '',
                    phone           TEXT DEFAULT '',
                    payee_type      TEXT DEFAULT '',
                    expected_amount REAL,
                    actual_amount   REAL,
                    result          TEXT DEFAULT '',
                    created_at      TEXT NOT NULL
                );
                ''')
                conn.commit()
            finally:
                conn.close()

    # ── 批次 ──────────────────────────────────────────────
    def create_batch(self, title: str = '', orders: list = None) -> dict:
        '''创建批次；orders 为 [{project_id, source_text, meta}] 列表'''
        batch_id = self._new_batch_id()
        now = _now()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    'INSERT INTO batches (batch_id, status, title, created_at, updated_at) VALUES (?,?,?,?,?)',
                    (batch_id, 'WAITING_CODEX', title, now, now),
                )
                for i, o in enumerate(orders or [], 1):
                    self._insert_order(conn, f'{batch_id}-{i:02d}', batch_id, o)
                conn.commit()
            finally:
                conn.close()
        return self.get_batch(batch_id)

    def _insert_order(self, conn, order_id, batch_id, o: dict):
        now = _now()
        conn.execute(
            '''INSERT INTO orders
               (order_id, batch_id, project_id, status, source_text, meta_json,
                draft_json, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (order_id, batch_id, (o.get('project_id') or '').strip(),
             'WAITING_CODEX' if (o.get('source_text') or '').strip() else 'DRAFT',
             o.get('source_text') or '',
             _json_dumps(o.get('meta') or {}),
             _json_dumps(o.get('draft') or {}),
             now, now),
        )

    def _new_batch_id(self) -> str:
        prefix = 'B' + datetime.now().strftime('%Y%m%d')
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    'SELECT batch_id FROM batches WHERE batch_id LIKE ? ORDER BY batch_id DESC LIMIT 1',
                    (prefix + '-%',),
                ).fetchone()
            finally:
                conn.close()
        seq = 1
        if row:
            m = re.search(r'-(\d+)$', row['batch_id'])
            if m:
                seq = int(m.group(1)) + 1
        return f'{prefix}-{seq:03d}'

    def list_batches(self) -> list:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute('SELECT * FROM batches ORDER BY created_at DESC').fetchall()
                return [self._batch_row(r) for r in rows]
            finally:
                conn.close()

    def get_batch(self, batch_id: str) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute('SELECT * FROM batches WHERE batch_id=?', (batch_id,)).fetchone()
                if not row:
                    return None
                batch = self._batch_row(row)
                batch['orders'] = self._list_orders(conn, batch_id)
                return batch
            finally:
                conn.close()

    def _batch_row(self, row) -> dict:
        return {
            'batch_id': row['batch_id'],
            'status': row['status'],
            'title': row['title'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    def update_batch_status(self, batch_id: str, status: str):
        now = _now()
        with self._lock:
            conn = self._conn()
            try:
                conn.execute('UPDATE batches SET status=?, updated_at=? WHERE batch_id=?',
                             (status, now, batch_id))
                conn.commit()
            finally:
                conn.close()

    # ── 单据 ──────────────────────────────────────────────
    def add_order(self, batch_id: str, o: dict) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute('SELECT COUNT(*) AS c FROM orders WHERE batch_id=?',
                                   (batch_id,)).fetchone()
                seq = row['c'] + 1
                order_id = f'{batch_id}-{seq:02d}'
                self._insert_order(conn, order_id, batch_id, o)
                conn.execute('UPDATE batches SET updated_at=? WHERE batch_id=?', (_now(), batch_id))
                conn.commit()
            finally:
                conn.close()

    def recover_interrupted_orders(self) -> list:
        """把服务重启前未结束的自动化任务标记为需要人工处理。

        Playwright 页面、网页确认队列都只存在于原服务进程的内存中；服务
        重启后无法安全地接回原任务，因此不能继续显示为“第 1 页待确认”
        或“填写中”。保留草稿和历史事件，等待用户检查 EPC 后重新开始。
        """
        active = tuple(ACTIVE_ORDER_STATUSES)
        placeholders = ','.join('?' for _ in active)
        message = '自动填写服务已重启，原任务未继续执行；请检查 EPC 页面后重新开始该单。'
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    f'SELECT order_id FROM orders WHERE status IN ({placeholders})', active,
                ).fetchall()
                order_ids = [row['order_id'] for row in rows]
                if order_ids:
                    conn.execute(
                        f'''UPDATE orders
                            SET status='REQUIRES_ATTENTION', error=?, updated_at=?
                            WHERE status IN ({placeholders})''',
                        (message, _now(), *active),
                    )
                    for order_id in order_ids:
                        conn.execute(
                            'INSERT INTO order_events (order_id, kind, message, created_at) VALUES (?,?,?,?)',
                            (order_id, 'interrupted', message, _now()),
                        )
                conn.commit()
                return order_ids
            finally:
                conn.close()

    def delete_batch(self, batch_id: str) -> dict:
        """删除一个已停止的批次及其本地草稿、日志和核验记录。

        EPC 页面正在填写或等待用户确认的批次不允许删除，避免本地状态
        被清掉后仍有浏览器自动化在继续执行。
        """
        with self._lock:
            conn = self._conn()
            try:
                batch = conn.execute(
                    'SELECT batch_id FROM batches WHERE batch_id=?', (batch_id,),
                ).fetchone()
                if not batch:
                    raise KeyError('批次不存在')

                rows = conn.execute(
                    'SELECT order_id, status FROM orders WHERE batch_id=?', (batch_id,),
                ).fetchall()
                active = [r['order_id'] for r in rows if r['status'] in ACTIVE_ORDER_STATUSES]
                if active:
                    raise ValueError('批次中仍有正在执行或等待确认的单据：' + '、'.join(active))

                order_ids = [r['order_id'] for r in rows]
                if order_ids:
                    placeholders = ','.join('?' for _ in order_ids)
                    conn.execute(
                        f'DELETE FROM payee_verifications WHERE order_id IN ({placeholders})',
                        order_ids,
                    )
                    conn.execute(
                        f'DELETE FROM order_events WHERE order_id IN ({placeholders})',
                        order_ids,
                    )
                conn.execute('DELETE FROM orders WHERE batch_id=?', (batch_id,))
                conn.execute('DELETE FROM batches WHERE batch_id=?', (batch_id,))
                conn.commit()
            finally:
                conn.close()

        batch_path = (BATCHES_DIR / batch_id).resolve()
        batches_root = BATCHES_DIR.resolve()
        if batch_path.parent == batches_root and batch_path.exists():
            shutil.rmtree(batch_path)
        return {'batch_id': batch_id, 'deleted_orders': len(order_ids)}
        return self.get_order(order_id)

    def delete_order(self, batch_id: str, order_id: str) -> dict:
        """删除未运行的单据，并同步清理本地草稿、日志、核验和交接文件。"""
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    'SELECT order_id, status FROM orders WHERE order_id=? AND batch_id=?',
                    (order_id, batch_id),
                ).fetchone()
                if not row:
                    raise KeyError('单据不存在或不属于该批次')
                if row['status'] in ACTIVE_ORDER_STATUSES:
                    raise ValueError('该单据正在执行、暂停或等待确认，不能删除；请先完成或重新开始后再处理。')

                conn.execute('DELETE FROM payee_verifications WHERE order_id=?', (order_id,))
                conn.execute('DELETE FROM order_events WHERE order_id=?', (order_id,))
                conn.execute('DELETE FROM orders WHERE order_id=? AND batch_id=?', (order_id, batch_id))
                conn.execute('UPDATE batches SET updated_at=? WHERE batch_id=?', (_now(), batch_id))
                conn.commit()
            finally:
                conn.close()

        self._remove_order_from_batch_files(batch_id, order_id)
        return {'batch_id': batch_id, 'order_id': order_id, 'remaining_orders': len(self.list_orders(batch_id))}

    def _remove_order_from_batch_files(self, batch_id: str, order_id: str):
        """从 input.json / codex_draft.json 移除已删除单据，保留其余批次内容。"""
        batch_dir = BATCHES_DIR / batch_id
        for filename in ('input.json', 'codex_draft.json'):
            path = batch_dir / filename
            if not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding='utf-8-sig'))
            except Exception:
                continue

            changed = False
            if isinstance(raw, dict) and isinstance(raw.get('orders'), list):
                original = raw['orders']
                raw['orders'] = [item for item in original if not isinstance(item, dict) or item.get('order_id') != order_id]
                changed = len(raw['orders']) != len(original)
            elif isinstance(raw, list):
                original = raw
                raw = [item for item in original if not isinstance(item, dict) or item.get('order_id') != order_id]
                changed = len(raw) != len(original)
            elif isinstance(raw, dict) and order_id in raw:
                del raw[order_id]
                changed = True

            if changed:
                path.write_text(_json_dumps(raw), encoding='utf-8')

    def get_order(self, order_id: str) -> dict:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute('SELECT * FROM orders WHERE order_id=?', (order_id,)).fetchone()
                return self._order_row(row) if row else None
            finally:
                conn.close()

    def list_orders(self, batch_id: str) -> list:
        with self._lock:
            conn = self._conn()
            try:
                return self._list_orders(conn, batch_id)
            finally:
                conn.close()

    def _list_orders(self, conn, batch_id: str) -> list:
        rows = conn.execute('SELECT * FROM orders WHERE batch_id=? ORDER BY order_id', (batch_id,)).fetchall()
        return [self._order_row(r) for r in rows]

    def _order_row(self, row) -> dict:
        return {
            'order_id': row['order_id'],
            'batch_id': row['batch_id'],
            'project_id': row['project_id'],
            'status': row['status'],
            'source_text': row['source_text'],
            'meta': _json_loads(row['meta_json'], {}),
            'draft': _json_loads(row['draft_json'], {}),
            'current_step': row['current_step'],
            'warnings': _json_loads(row['warnings_json'], []),
            'error': row['error'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }

    def update_order(self, order_id: str, **fields) -> dict:
        allowed = {'project_id', 'status', 'source_text', 'meta', 'draft',
                   'current_step', 'warnings', 'error'}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            col = k if k in ('project_id', 'status', 'source_text', 'current_step', 'error') else k + '_json'
            val = v
            if k in ('meta', 'draft', 'warnings'):
                val = _json_dumps(v)
            sets.append(f'{col}=?')
            vals.append(val)
        if not sets:
            return self.get_order(order_id)
        vals.append(_now())
        sets.append('updated_at=?')
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(f'UPDATE orders SET {", ".join(sets)} WHERE order_id=?', (*vals, order_id))
                conn.commit()
            finally:
                conn.close()
        return self.get_order(order_id)

    # ── 事件 ──────────────────────────────────────────────
    def append_event(self, order_id: str, kind: str, message: str):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    'INSERT INTO order_events (order_id, kind, message, created_at) VALUES (?,?,?,?)',
                    (order_id, kind, message, _now()),
                )
                conn.commit()
            finally:
                conn.close()

    def list_events(self, order_id: str, limit: int = 500) -> list:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    'SELECT kind, message, created_at FROM order_events WHERE order_id=? '
                    'ORDER BY id DESC LIMIT ?', (order_id, limit),
                ).fetchall()
                return [{'kind': r['kind'], 'message': r['message'], 'created_at': r['created_at']}
                        for r in reversed(rows)]
            finally:
                conn.close()

    def clear_order_history(self, order_id: str) -> None:
        """清空本地执行日志和核验记录，不影响草稿或单据状态。"""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute('DELETE FROM order_events WHERE order_id=?', (order_id,))
                conn.execute('DELETE FROM payee_verifications WHERE order_id=?', (order_id,))
                conn.commit()
            finally:
                conn.close()

    def reset_batch_execution(self, batch_id: str) -> dict:
        """清空一批次的本地执行痕迹，并将有草稿的单据恢复为可执行。"""
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    'SELECT order_id, draft_json FROM orders WHERE batch_id=?', (batch_id,),
                ).fetchall()
                if not rows:
                    raise KeyError('批次不存在或没有单据')
                order_ids = [row['order_id'] for row in rows]
                placeholders = ','.join('?' for _ in order_ids)
                conn.execute(f'DELETE FROM order_events WHERE order_id IN ({placeholders})', order_ids)
                conn.execute(f'DELETE FROM payee_verifications WHERE order_id IN ({placeholders})', order_ids)
                ready_count = 0
                for row in rows:
                    draft, overrides_cleared = clear_execution_overrides(
                        _json_loads(row['draft_json'], {}),
                    )
                    has_draft = bool(draft)
                    status = 'READY' if has_draft else 'WAITING_CODEX'
                    ready_count += int(has_draft)
                    conn.execute(
                        'UPDATE orders SET draft_json=?, status=?, current_step=?, error=?, updated_at=? WHERE order_id=?',
                        (_json_dumps(draft), status, '', '', _now(), row['order_id']),
                    )
                conn.execute(
                    'UPDATE batches SET status=?, updated_at=? WHERE batch_id=?',
                    ('READY' if ready_count else 'WAITING_CODEX', _now(), batch_id),
                )
                conn.commit()
                return {'batch_id': batch_id, 'orders': len(rows), 'ready_orders': ready_count}
            finally:
                conn.close()

    # ── 收款人核验记录（第 4 阶段使用） ────────────────────
    def save_payee_verifications(self, order_id: str, items: list):
        with self._lock:
            conn = self._conn()
            try:
                conn.execute('DELETE FROM payee_verifications WHERE order_id=?', (order_id,))
                for it in items:
                    conn.execute(
                        '''INSERT INTO payee_verifications
                           (order_id, payee_id, name, phone, payee_type,
                            expected_amount, actual_amount, result, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?)''',
                        (order_id, it.get('payee_id') or '', it.get('name') or '',
                         it.get('phone') or '', it.get('type') or '',
                         it.get('expected_amount'), it.get('actual_amount'),
                         it.get('result') or '', _now()),
                    )
                conn.commit()
            finally:
                conn.close()

    def list_payee_verifications(self, order_id: str) -> list:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute(
                    'SELECT * FROM payee_verifications WHERE order_id=? ORDER BY id', (order_id,),
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    # ── 批次文件交接（Codex） ──────────────────────────────
    def batch_dir(self, batch_id: str) -> Path:
        d = BATCHES_DIR / batch_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_batch_input(self, batch_id: str) -> Path:
        '''把所有单据原始资料写入 data/batches/{batch_id}/input.json'''
        orders = self.list_orders(batch_id)
        payload = {
            'batch_id': batch_id,
            'status': 'waiting_codex',
            'orders': [
                {
                    'order_id': o['order_id'],
                    'project_id': o['project_id'],
                    'source_text': o['source_text'],
                    'meta': o['meta'],
                }
                for o in orders
            ],
        }
        p = self.batch_dir(batch_id) / 'input.json'
        p.write_text(_json_dumps(payload), encoding='utf-8')
        self.update_batch_status(batch_id, 'WAITING_CODEX')
        return p

    def load_codex_draft(self, batch_id: str) -> dict:
        '''读取 data/batches/{batch_id}/codex_draft.json，返回 {order_id: draft_entry}'''
        p = self.batch_dir(batch_id) / 'codex_draft.json'
        if not p.exists():
            return {}
        try:
            raw = json.loads(p.read_text(encoding='utf-8-sig'))
        except Exception:
            return {}
        result = {}
        if isinstance(raw, dict):
            orders = raw.get('orders') or []
            if isinstance(orders, list):
                for entry in orders:
                    if isinstance(entry, dict) and entry.get('order_id'):
                        result[entry['order_id']] = entry
            else:
                # 形如 {order_id: draft}
                for k, v in raw.items():
                    if k != 'batch_id' and isinstance(v, dict):
                        result[k] = v
        elif isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict) and entry.get('order_id'):
                    result[entry['order_id']] = entry
        return result

    def codex_draft_path(self, batch_id: str) -> Path:
        return self.batch_dir(batch_id) / 'codex_draft.json'
