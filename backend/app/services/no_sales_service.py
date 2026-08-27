"""无动销商品历史登记与本场平台判定记录。

存 system_settings 键 `no_sales_item_ids` = JSON [taobao_item_id, ...] (商品维度——
平台动销校验是商品级"近60天销量≥1")。
- 来源①: 报名回执"动销不达标"自动登记(自愈);
- 来源②: 每场平台重检通过后自动移除；
- 历史登记是活动报名硬排除依据；登记商品不进入官方活动报名表；
- 登记商品只走同期单品立减兜底，恢复动销后需经明确转正流程移除登记。

镜像 delisted_sku_service 的存储与自愈模式。
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from sqlalchemy.orm import Session

_KEY = "no_sales_item_ids"
_NO_SALES_MARKERS = ("动销", "销售件数≥1", "销售件数&ge;1")
_NON_NO_SALES_FAILURE_MARKERS = (
    "最低标价", "普惠券后价", "红线价", "缺失的SKUID", "要求全部SKU",
    "可编辑", "不可上调", "下架", "失效", "重复", "不在活动范围",
)
# Production Taobao IDs are normally much longer; four digits keeps historical
# fixtures/imports usable while still rejecting the junk values ("5", "待定",
# "暂无") that polluted the durable registry in the incident report.
_ITEM_ID_RE = re.compile(r"\d{4,20}")


def _normalize_item_ids(item_ids: Iterable[object]) -> set[str]:
    """Keep only real-looking Taobao item IDs; reject notes such as 待定/暂无/5."""
    return {
        text for value in (item_ids or [])
        if (text := str(value or "").strip()) and _ITEM_ID_RE.fullmatch(text)
    }


def is_valid_item_id(value: object) -> bool:
    """Return whether ``value`` is a structurally valid Taobao item id."""
    return bool(_ITEM_ID_RE.fullmatch(str(value or "").strip()))


def normalize_item_ids(item_ids: Iterable[object]) -> set[str]:
    """Public normalizer used by campaign ingestion and diagnostics."""
    return _normalize_item_ids(item_ids)


def get_no_sales(db: Session) -> set[str]:
    """当前登记的无动销商品 item_id 集合。"""
    from app.services import settings_service
    raw = settings_service.get(db, _KEY, env_fallback=False)
    try:
        items = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        items = []
    return _normalize_item_ids(items)


def sanitize_registry(db: Session) -> dict:
    """Remove only structurally invalid registry values and report exact changes.

    This is deliberately conservative: every 4-20 digit value is preserved.
    It cannot infer whether a valid-looking item should be promoted or removed.
    """
    from app.services import settings_service

    raw = settings_service.get(db, _KEY, env_fallback=False)
    parse_error = False
    try:
        parsed = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        parsed = []
        parse_error = bool(raw)
    values = parsed if isinstance(parsed, list) else []
    valid = _normalize_item_ids(values)
    removed = sorted({str(value or "").strip() for value in values} - valid)
    malformed = parse_error or bool(raw and not isinstance(parsed, list))
    if removed or malformed:
        _save(db, valid)
    return {
        "changed": bool(removed or malformed),
        "removed_invalid_values": removed,
        "registered": sorted(valid),
    }


def _save(db: Session, ids: set[str]) -> None:
    from app.services import settings_service
    settings_service.set_value(
        db, _KEY, json.dumps(sorted(ids), ensure_ascii=False),
        description="无动销报名硬排除(只走同期单品立减；恢复动销后显式转正)")
    db.commit()


def add_no_sales(db: Session, item_ids: Iterable[str]) -> set[str]:
    """登记无动销商品(并集)。返回登记后的全集。无变化不写库。"""
    cur = get_no_sales(db)
    new = cur | _normalize_item_ids(item_ids)
    if new != cur:
        _save(db, new)
    return new


def remove_no_sales(db: Session, item_ids: Iterable[str]) -> set[str]:
    """移除登记(本场平台资格重检通过后)。返回剩余全集。"""
    cur = get_no_sales(db)
    new = cur - _normalize_item_ids(item_ids)
    if new != cur:
        _save(db, new)
    return new


def extract_no_sales_from_feedback(failed_items) -> set[str]:
    """从报名失败明细抽"动销不达标"的商品 item_id(供自愈登记)。
    failed_items = [{item_id, sku_id, reason, raw}, ...]。"""
    out: set[str] = set()
    for it in failed_items or []:
        raw = str((it or {}).get("raw") or "") + " " + str((it or {}).get("reason") or "")
        if any(m in raw for m in _NO_SALES_MARKERS):
            iid = str((it or {}).get("item_id") or "").strip()
            if iid:
                out.add(iid)
    return out


def extract_no_sales_only_from_feedback(failed_items) -> set[str]:
    """只返回失败原因纯粹为无动销的商品；混合价格/SKU失败不得兜底。"""
    by_item: dict[str, list[tuple[str, str]]] = {}
    for row in failed_items or []:
        item_id = str((row or {}).get("item_id") or "").strip()
        if not item_id:
            continue
        reason = str((row or {}).get("reason") or "").strip()
        raw = str((row or {}).get("raw") or "").strip()
        by_item.setdefault(item_id, []).append((reason, raw))
    out: set[str] = set()
    for item_id, rows in by_item.items():
        # The platform's raw detail appends generic policy copy such as
        # “最低标价” even when the parsed terminal reason is only no-sales.
        # Prefer every explicit parsed reason; use raw text only when the
        # parser could not produce a reason at all.
        classified = [reason if reason else raw for reason, raw in rows]
        if (classified
                and all(any(marker in text for marker in _NO_SALES_MARKERS)
                        for text in classified)
                and not any(any(marker in text for marker in _NON_NO_SALES_FAILURE_MARKERS)
                            for text in classified)):
            out.add(item_id)
    return out
