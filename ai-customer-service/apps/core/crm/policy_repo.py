"""店铺级策略 policy_settings 读写。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from apps.core.crm.events import ensure_brand_row, ensure_shop_row, now_iso


@dataclass(frozen=True, slots=True)
class PolicyRow:
    anger_hit_threshold: int
    unknown_topic_threshold: float
    strong_reminder: int
    strong_reminder_until: str | None
    popup_auto_dismiss: int
    jim_intercept_push: int
    price_sensitive_handoff: int
    real_photo_jim_intercept: int
    # 1=完整 Jim（安抚 + ManualHold）；0=仅推送 + 记事件（不设 ManualHold、不入队安抚）
    jim_price_full_takeover: int
    jim_photo_full_takeover: int
    handoff_soothe_line: str | None
    outbound_preview_enabled: int
    outbound_preview_delay_seconds: int


_DEFAULT = PolicyRow(
    anger_hit_threshold=3,
    unknown_topic_threshold=0.45,
    strong_reminder=0,
    strong_reminder_until=None,
    popup_auto_dismiss=1,  # v1.6.0 默认开启（弹窗自动关闭 + 风控弹窗自救）
    jim_intercept_push=1,
    price_sensitive_handoff=1,
    real_photo_jim_intercept=1,
    jim_price_full_takeover=1,
    jim_photo_full_takeover=1,
    handoff_soothe_line=None,
    outbound_preview_enabled=1,
    outbound_preview_delay_seconds=8,
)


def _gv(row: sqlite3.Row, col: str, default):
    try:
        v = row[col]
    except (KeyError, IndexError):
        return default
    return default if v is None else v


def get_policy(conn: sqlite3.Connection, *, brand_id: str, shop_id: str) -> PolicyRow:
    try:
        row = conn.execute(
            "SELECT * FROM policy_settings WHERE brand_id = ? AND shop_id = ? LIMIT 1",
            (brand_id, shop_id),
        ).fetchone()
    except sqlite3.OperationalError:
        return _DEFAULT
    if not row:
        return _DEFAULT
    raw_handoff = _gv(row, "handoff_soothe_line", None)
    handoff_line = str(raw_handoff).strip() if raw_handoff is not None else ""
    return PolicyRow(
        anger_hit_threshold=int(_gv(row, "anger_hit_threshold", 3)),
        unknown_topic_threshold=float(_gv(row, "unknown_topic_threshold", 0.45)),
        strong_reminder=int(_gv(row, "strong_reminder", 0)),
        strong_reminder_until=str(_gv(row, "strong_reminder_until", None) or "")
        or None,
        popup_auto_dismiss=int(_gv(row, "popup_auto_dismiss", 0)),
        jim_intercept_push=int(_gv(row, "jim_intercept_push", 1)),
        price_sensitive_handoff=int(_gv(row, "price_sensitive_handoff", 1)),
        real_photo_jim_intercept=int(_gv(row, "real_photo_jim_intercept", 1)),
        jim_price_full_takeover=int(_gv(row, "jim_price_full_takeover", 1)),
        jim_photo_full_takeover=int(_gv(row, "jim_photo_full_takeover", 1)),
        handoff_soothe_line=handoff_line or None,
        outbound_preview_enabled=int(_gv(row, "outbound_preview_enabled", 1)),
        outbound_preview_delay_seconds=int(_gv(row, "outbound_preview_delay_seconds", 8)),
    )


def ensure_policy_row(conn: sqlite3.Connection, *, brand_id: str, shop_id: str) -> None:
    ensure_brand_row(conn, brand_id=brand_id)
    cur_s = conn.execute("SELECT shop_id FROM shops WHERE shop_id = ? LIMIT 1", (shop_id,))
    if not cur_s.fetchone():
        code = shop_id.split(":", 1)[-1] if ":" in shop_id else shop_id
        ensure_shop_row(conn, brand_id=brand_id, shop_id=shop_id, shop_code=code, display_name=code)
    cur = conn.execute(
        "SELECT 1 FROM policy_settings WHERE brand_id = ? AND shop_id = ? LIMIT 1",
        (brand_id, shop_id),
    )
    if cur.fetchone():
        return
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO policy_settings(
          brand_id, shop_id, followup_level, anger_hit_threshold, unknown_topic_threshold,
          unknown_topic_fallback_version, ocr_polling_seconds, strong_reminder,
          strong_reminder_until, popup_auto_dismiss, jim_intercept_push,
          created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            brand_id,
            shop_id,
            "medium",
            3,
            0.45,
            1,
            8,
            0,
            None,
            0,
            1,
            ts,
            ts,
        ),
    )
    conn.commit()


def update_policy_fields(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    anger_hit_threshold: int | None = None,
    strong_reminder: int | None = None,
    strong_reminder_until: str | None = None,
    popup_auto_dismiss: int | None = None,
    price_sensitive_handoff: int | None = None,
    real_photo_jim_intercept: int | None = None,
    jim_price_full_takeover: int | None = None,
    jim_photo_full_takeover: int | None = None,
    outbound_preview_enabled: int | None = None,
    outbound_preview_delay_seconds: int | None = None,
) -> None:
    ensure_policy_row(conn, brand_id=brand_id, shop_id=shop_id)
    ts = now_iso()
    sets = ["updated_at = ?"]
    vals: list = [ts]
    if anger_hit_threshold is not None:
        sets.append("anger_hit_threshold = ?")
        vals.append(int(anger_hit_threshold))
    if strong_reminder is not None:
        sets.append("strong_reminder = ?")
        vals.append(int(strong_reminder))
    if strong_reminder is not None and int(strong_reminder) == 0:
        sets.append("strong_reminder_until = NULL")
    elif strong_reminder_until is not None:
        sets.append("strong_reminder_until = ?")
        vals.append(strong_reminder_until)
    if popup_auto_dismiss is not None:
        sets.append("popup_auto_dismiss = ?")
        vals.append(int(popup_auto_dismiss))
    if price_sensitive_handoff is not None:
        sets.append("price_sensitive_handoff = ?")
        vals.append(int(price_sensitive_handoff))
    if real_photo_jim_intercept is not None:
        sets.append("real_photo_jim_intercept = ?")
        vals.append(int(real_photo_jim_intercept))
    if outbound_preview_enabled is not None:
        sets.append("outbound_preview_enabled = ?")
        vals.append(int(outbound_preview_enabled))
    if outbound_preview_delay_seconds is not None:
        sets.append("outbound_preview_delay_seconds = ?")
        vals.append(int(outbound_preview_delay_seconds))
    if jim_price_full_takeover is not None:
        sets.append("jim_price_full_takeover = ?")
        vals.append(int(jim_price_full_takeover))
    if jim_photo_full_takeover is not None:
        sets.append("jim_photo_full_takeover = ?")
        vals.append(int(jim_photo_full_takeover))
    vals.extend([brand_id, shop_id])
    conn.execute(
        f"UPDATE policy_settings SET {', '.join(sets)} WHERE brand_id = ? AND shop_id = ?",
        vals,
    )
    conn.commit()


def session_get_state(conn: sqlite3.Connection, session_id: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT anger_streak, followup_after_can FROM sessions WHERE session_id = ? LIMIT 1",
        (session_id,),
    ).fetchone()
    if not row:
        return 0, 0
    return int(row[0] or 0), int(row[1] or 0)


def session_set_anger(conn: sqlite3.Connection, session_id: str, streak: int) -> None:
    conn.execute(
        "UPDATE sessions SET anger_streak = ?, updated_at = ? WHERE session_id = ?",
        (int(streak), now_iso(), session_id),
    )
    conn.commit()


def session_set_followup(conn: sqlite3.Connection, session_id: str, pending: int) -> None:
    conn.execute(
        "UPDATE sessions SET followup_after_can = ?, updated_at = ? WHERE session_id = ?",
        (int(pending), now_iso(), session_id),
    )
    conn.commit()
