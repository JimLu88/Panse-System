"""
AI 陪伴（Companion）：异步写入运行与健康事件，不占用 SequentialExecutor 线程。

UI 开关通过 ``set_companion_ui_enabled`` 与 SQLite ``companion_settings`` 同步；
仅在开启时写入 ``system_health_logs``。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from apps.core.crm.db import connect, init_db
from apps.core.crm.events import now_iso
from apps.core.runtime_paths import default_sqlite_db_path

_health_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="companion_health")
_companion_ui_enabled: bool = False


def set_companion_ui_enabled(flag: bool) -> None:
    """由工作台勾选即时同步；用于高频路径快速跳过写入。"""
    global _companion_ui_enabled
    _companion_ui_enabled = bool(flag)


def is_companion_ui_enabled() -> bool:
    return _companion_ui_enabled


def _db_path() -> Path:
    return default_sqlite_db_path()


@dataclass(frozen=True, slots=True)
class CompanionSettingsRow:
    enabled: int
    anchor_started_at: str | None
    last_bug_report_date: str | None
    last_optimization_report_date: str | None


def load_companion_settings(conn: sqlite3.Connection | None = None) -> CompanionSettingsRow:
    own = conn is None
    if own:
        conn = connect(_db_path())
        init_db(conn)
    try:
        row = conn.execute(
            "SELECT enabled, anchor_started_at, last_bug_report_date, last_optimization_report_date "
            "FROM companion_settings WHERE singleton = 1 LIMIT 1"
        ).fetchone()
        if not row:
            return CompanionSettingsRow(0, None, None, None)
        return CompanionSettingsRow(
            int(row[0]),
            str(row[1]) if row[1] else None,
            str(row[2]) if row[2] else None,
            str(row[3]) if row[3] else None,
        )
    finally:
        if own:
            conn.close()


def save_companion_enabled(enabled: bool) -> None:
    """持久化勾选；首次开启写入磨合起点时间。"""
    conn = connect(_db_path())
    init_db(conn)
    try:
        cur = conn.execute(
            "SELECT anchor_started_at FROM companion_settings WHERE singleton = 1 LIMIT 1"
        ).fetchone()
        anchor = cur[0] if cur else None
        if enabled:
            anchor_val = anchor if anchor else now_iso()
        else:
            anchor_val = None
        conn.execute(
            """
            UPDATE companion_settings SET
              enabled = ?,
              anchor_started_at = COALESCE(?, anchor_started_at)
            WHERE singleton = 1
            """,
            (1 if enabled else 0, anchor_val),
        )
        conn.commit()
    finally:
        conn.close()
    set_companion_ui_enabled(enabled)


def sync_companion_enabled_from_db() -> bool:
    """启动时加载数据库开关到内存缓存。"""
    row = load_companion_settings()
    set_companion_ui_enabled(bool(row.enabled))
    return bool(row.enabled)


def update_report_dates(*, bug_report_date: str | None = None, optimization_date: str | None = None) -> None:
    conn = connect(_db_path())
    init_db(conn)
    try:
        sets = []
        vals: list = []
        if bug_report_date is not None:
            sets.append("last_bug_report_date = ?")
            vals.append(bug_report_date)
        if optimization_date is not None:
            sets.append("last_optimization_report_date = ?")
            vals.append(optimization_date)
        if sets:
            vals.append(1)
            conn.execute(
                f"UPDATE companion_settings SET {', '.join(sets)} WHERE singleton = ?",
                vals,
            )
            conn.commit()
    finally:
        conn.close()


def insert_companion_report(report_kind: str, body_md: str) -> str:
    rid = str(uuid.uuid4())
    conn = connect(_db_path())
    init_db(conn)
    try:
        conn.execute(
            """
            INSERT INTO companion_reports(report_id, created_at, report_kind, body_md)
            VALUES (?,?,?,?)
            """,
            (rid, now_iso(), report_kind, body_md),
        )
        conn.commit()
    finally:
        conn.close()
    return rid


def record_health_event(
    event_type: str,
    payload: dict | None = None,
    *,
    brand_id: str | None = None,
    shop_id: str | None = None,
) -> None:
    """异步健康路径：单独连接 + 锁；勿在 SequentialExecutor 内长时间持有。"""
    if not _companion_ui_enabled:
        return
    payload = payload or {}
    log_id = str(uuid.uuid4())
    ts = now_iso()
    pj = json.dumps(payload, ensure_ascii=False)

    def _write() -> None:
        c = connect(_db_path())
        init_db(c)
        try:
            c.execute(
                """
                INSERT INTO system_health_logs(log_id, created_at, event_type, payload_json, brand_id, shop_id)
                VALUES (?,?,?,?,?,?)
                """,
                (log_id, ts, event_type, pj, brand_id, shop_id),
            )
            c.commit()
        finally:
            c.close()

    _health_pool.submit(_write)


def record_executor_snapshot(*, queue_depth: int, executor_busy: bool) -> None:
    record_health_event(
        "executor_snapshot",
        {"queue_depth": queue_depth, "busy": executor_busy},
    )


def days_since_anchor(anchor_started_at: str | None) -> int | None:
    if not anchor_started_at:
        return None
    try:
        from datetime import date, datetime

        d0 = datetime.strptime(str(anchor_started_at)[:10], "%Y-%m-%d").date()
        return (date.today() - d0).days
    except Exception:
        return None
