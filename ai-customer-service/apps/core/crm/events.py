from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any


def now_iso() -> str:
    # ISO-ish without timezone handling for MVP
    return time.strftime("%Y-%m-%d %H:%M:%S")


def now_epoch_s() -> int:
    return int(time.time())


@dataclass(frozen=True, slots=True)
class SessionEvent:
    event_id: str
    brand_id: str
    shop_id: str
    session_id: str
    source_id: str
    event_type: str
    payload: dict[str, Any]
    evidence_confidence: float = 1.0
    created_at: str = ""

    def with_defaults(self) -> "SessionEvent":
        return SessionEvent(
            event_id=self.event_id or str(uuid.uuid4()),
            brand_id=self.brand_id,
            shop_id=self.shop_id,
            session_id=self.session_id,
            source_id=self.source_id,
            event_type=self.event_type,
            payload=self.payload,
            evidence_confidence=float(self.evidence_confidence),
            created_at=self.created_at or now_iso(),
        )


def insert_session_event(conn: sqlite3.Connection, ev: SessionEvent, *, force: bool = False) -> None:
    e = ev.with_defaults()
    # Dedup: do not spam same event_type for same session within 30 seconds.
    if not force and _dedup_exists(conn, e.session_id, e.event_type, window_s=30, payload=e.payload):
        return
    conn.execute(
        """
        INSERT INTO session_events(
          event_id, brand_id, shop_id, session_id, source_id,
          event_type, payload_json, evidence_confidence, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            e.event_id,
            e.brand_id,
            e.shop_id,
            e.session_id,
            e.source_id,
            e.event_type,
            json.dumps(e.payload, ensure_ascii=False, separators=(",", ":")),
            float(e.evidence_confidence),
            e.created_at,
        ),
    )
    conn.commit()


def _dedup_exists(
    conn: sqlite3.Connection, session_id: str, event_type: str, *, window_s: int, payload: dict[str, Any]
) -> bool:
    cur = conn.execute(
        """
        SELECT created_at, payload_json
        FROM session_events
        WHERE session_id = ? AND event_type = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (session_id, event_type),
    )
    row = cur.fetchone()
    if not row:
        return False
    # Stronger dedup for right_panel_changed: same sig means no new info.
    if event_type == "right_panel_changed":
        try:
            last_payload = json.loads(str(row["payload_json"]) or "{}")
            if str(last_payload.get("sig") or "") == str(payload.get("sig") or ""):
                return True
        except Exception:
            pass
    # created_at format: %Y-%m-%d %H:%M:%S
    try:
        last_s = int(time.mktime(time.strptime(str(row["created_at"]), "%Y-%m-%d %H:%M:%S")))
    except Exception:
        return False
    return (now_epoch_s() - last_s) <= int(window_s)


def ensure_brand_row(conn: sqlite3.Connection, *, brand_id: str) -> None:
    cur = conn.execute("SELECT brand_id FROM brands WHERE brand_id = ? LIMIT 1", (brand_id,))
    if cur.fetchone():
        return
    conn.execute(
        "INSERT INTO brands(brand_id, name, created_at) VALUES (?,?,?)",
        (brand_id, brand_id, now_iso()),
    )
    conn.commit()


def ensure_shop_row(conn: sqlite3.Connection, *, brand_id: str, shop_id: str, shop_code: str, display_name: str) -> None:
    cur = conn.execute("SELECT shop_id FROM shops WHERE shop_id = ? LIMIT 1", (shop_id,))
    if cur.fetchone():
        return
    ensure_brand_row(conn, brand_id=brand_id)
    conn.execute(
        "INSERT INTO shops(shop_id, brand_id, shop_code, display_name, created_at) VALUES (?,?,?,?,?)",
        (shop_id, brand_id, shop_code, display_name, now_iso()),
    )
    conn.commit()
    try:
        from apps.core.configs.shop_yaml_bootstrap import ensure_shop_config_yaml

        ensure_shop_config_yaml(
            brand_id=brand_id,
            shop_code=shop_code,
            display_name=display_name,
            shop_id=shop_id,
        )
    except Exception:
        pass


def update_shop_display_name(conn: sqlite3.Connection, *, shop_id: str, display_name: str) -> None:
    """更新店铺在下拉框等处展示的 display_name（不改 shop_id / brand_id / shop_code）。"""
    dn = (display_name or "").strip()
    if not dn:
        raise ValueError("显示名称不能为空")
    cur = conn.execute("SELECT 1 FROM shops WHERE shop_id = ? LIMIT 1", (shop_id,))
    if not cur.fetchone():
        raise ValueError("店铺不存在")
    conn.execute("UPDATE shops SET display_name = ? WHERE shop_id = ?", (dn, shop_id))
    conn.commit()
    try:
        from apps.core.configs.shop_yaml_bootstrap import sync_shop_display_name_in_yaml

        sync_shop_display_name_in_yaml(shop_id=shop_id, display_name=dn)
    except Exception:
        pass


def register_shop_manual(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_code: str,
    display_name: str,
) -> str:
    """
    手动登记新店铺：内部 ID 固定为「brand_id:shop_code」（与工作台 YAML 约定一致）。
    若该 ID 或同一品牌下 shop_code 已存在则报错。
    """
    brand_id = brand_id.strip()
    shop_code = shop_code.strip()
    if not brand_id or not shop_code:
        raise ValueError("品牌 ID 与店铺编码不能为空")
    if ":" in shop_code:
        raise ValueError("店铺编码中不要包含冒号「:」")
    sid = f"{brand_id}:{shop_code}"
    ensure_brand_row(conn, brand_id=brand_id)
    cur = conn.execute(
        "SELECT shop_id FROM shops WHERE shop_id = ? OR (brand_id = ? AND shop_code = ?) LIMIT 1",
        (sid, brand_id, shop_code),
    )
    if cur.fetchone():
        raise ValueError(f"店铺已存在：{sid}")
    dn = (display_name or "").strip() or shop_code
    conn.execute(
        "INSERT INTO shops(shop_id, brand_id, shop_code, display_name, created_at) VALUES (?,?,?,?,?)",
        (sid, brand_id, shop_code, dn, now_iso()),
    )
    conn.commit()
    try:
        from apps.core.configs.shop_yaml_bootstrap import ensure_shop_config_yaml

        ensure_shop_config_yaml(
            brand_id=brand_id,
            shop_code=shop_code,
            display_name=dn,
            shop_id=sid,
        )
    except Exception:
        pass
    return sid


def ensure_channel_row(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    platform: str,
    account_id: str,
    brand_id: str,
    shop_id: str,
) -> None:
    cur = conn.execute("SELECT source_id FROM channels WHERE source_id = ? LIMIT 1", (source_id,))
    if cur.fetchone():
        return
    conn.execute(
        """
        INSERT INTO channels(
          source_id, platform, account_id, brand_id, shop_id,
          window_locator_json, roi_json, enabled, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            source_id,
            platform,
            account_id,
            brand_id,
            shop_id,
            "{}",
            "{}",
            1,
            now_iso(),
            now_iso(),
        ),
    )
    conn.commit()


def ensure_session_row(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    brand_id: str,
    shop_id: str,
    source_id: str,
    shop_code: str = "",
    shop_display_name: str = "",
) -> None:
    """
    Ensure a minimal sessions row exists so we can mark manual_hold/priority.
    MVP uses NULL customer_id.
    """
    cur = conn.execute("SELECT session_id FROM sessions WHERE session_id = ? LIMIT 1", (session_id,))
    if cur.fetchone():
        return
    # FK prerequisites
    ensure_shop_row(
        conn,
        brand_id=brand_id,
        shop_id=shop_id,
        shop_code=shop_code or shop_id,
        display_name=shop_display_name or (shop_code or shop_id),
    )
    # best-effort source_id decomposition: platform/account_id from "platform/account/..."
    platform = "unknown"
    account_id = "unknown"
    try:
        parts = str(source_id).split("/")
        if len(parts) >= 2:
            platform = parts[0] or platform
            account_id = parts[1] or account_id
    except Exception:
        pass
    ensure_channel_row(
        conn,
        source_id=source_id,
        platform=platform,
        account_id=account_id,
        brand_id=brand_id,
        shop_id=shop_id,
    )
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO sessions(
          session_id, brand_id, shop_id, source_id, customer_id,
          state, manual_hold, priority, last_seen_at, last_event_at, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            session_id,
            brand_id,
            shop_id,
            source_id,
            None,
            "Idle",
            0,
            0,
            ts,
            ts,
            ts,
            ts,
        ),
    )
    conn.commit()


def insert_message(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    brand_id: str,
    shop_id: str,
    source_id: str,
    direction: str,
    text: str,
    confidence: float = 1.0,
    captured_at: str = "",
) -> None:
    """v1.6.14：客服对话存档——把每轮买家消息(direction='in')与我方回复
    (direction='out')写入 messages 表。失败只吞不抛，绝不影响接待主流程。

    依赖 sessions 行存在（调用方通常已 ensure_session_row）。
    """
    try:
        t = (text or "").strip()
        if not t:
            return
        ts = now_iso()
        conn.execute(
            """
            INSERT INTO messages(
              message_id, session_id, brand_id, shop_id, source_id,
              direction, text, confidence, bbox_json, captured_at, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                uuid.uuid4().hex,
                session_id,
                brand_id,
                shop_id,
                source_id,
                (direction or "in").strip().lower(),
                t,
                float(confidence),
                None,
                captured_at or ts,
                ts,
            ),
        )
        conn.commit()
    except Exception:
        # 存档为软附加：任何异常（FK 缺失/锁等）都不得影响回复
        try:
            conn.rollback()
        except Exception:
            pass


def session_has_message_today(
    conn: sqlite3.Connection, session_id: str
) -> bool:
    """v1.6.14：该会话在「今天（本地自然日）」是否已有任何 messages 记录。
    用于「同一自然日已对话过则不再发欢迎语」。异常按 False 处理（保守发欢迎语）。
    """
    try:
        sid = (session_id or "").strip()
        if not sid:
            return False
        today = now_iso()[:10]  # YYYY-MM-DD
        row = conn.execute(
            "SELECT 1 FROM messages WHERE session_id = ? "
            "AND substr(captured_at,1,10) = ? LIMIT 1",
            (sid, today),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def get_session_customer_display_name(conn: sqlite3.Connection, session_id: str) -> str:
    """优先返回 customers.display_name / external_ref，否则回退 session_id。"""
    sid = (session_id or "").strip()
    if not sid:
        return ""
    row = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(c.display_name), ''), NULLIF(TRIM(c.external_ref), ''), '')
        FROM sessions s
        LEFT JOIN customers c ON c.customer_id = s.customer_id
        WHERE s.session_id = ?
        LIMIT 1
        """,
        (sid,),
    ).fetchone()
    if row and str(row[0] or "").strip():
        return str(row[0]).strip()
    return sid


def set_manual_hold(conn: sqlite3.Connection, session_id: str, *, manual_hold: bool) -> None:
    ts = now_iso()
    conn.execute(
        "UPDATE sessions SET manual_hold = ?, state = ?, updated_at = ?, last_event_at = ? WHERE session_id = ?",
        (1 if manual_hold else 0, "ManualHold" if manual_hold else "Idle", ts, ts, session_id),
    )
    conn.commit()


def bump_priority(conn: sqlite3.Connection, session_id: str, *, priority: int) -> None:
    ts = now_iso()
    conn.execute(
        "UPDATE sessions SET priority = ?, updated_at = ?, last_event_at = ? WHERE session_id = ?",
        (int(priority), ts, ts, session_id),
    )
    conn.commit()
