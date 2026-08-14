"""Authoritative platform price-floor evidence for campaign preflight.

Fresh activity exports provide the two different platform gates per SKU:
minimum list price and minimum universal-coupon-after price.  Failed batch
feedback can also teach an exact line, but only as evidence for the next
user-approved program run; it never changes a signup price or retries a job.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.services import settings_service


EVIDENCE_KEY = "campaign_price_floor_evidence_v1"
_NUMBER = r"([0-9][0-9,]*(?:\.[0-9]+)?)"
_MIN_LIST_PATTERNS = (
    re.compile(rf"管控期标价为\s*{_NUMBER}\s*元"),
    re.compile(rf"最低标价[：:\s]*{_NUMBER}\s*元"),
)
_MIN_COUPON_PATTERNS = (
    re.compile(rf"最低普惠券后价[：:\s]*{_NUMBER}\s*元"),
    re.compile(rf"最低券后价[：:\s]*{_NUMBER}\s*元"),
)


def _decimal(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return number.quantize(Decimal("0.01")) if number > 0 else None


def _load(db: Session) -> dict[str, dict]:
    raw = settings_service.get(db, EVIDENCE_KEY, env_fallback=False)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def evidence_map(db: Session) -> dict[str, dict]:
    return _load(db)


def _write(db: Session, payload: dict[str, dict]) -> None:
    settings_service.set_value(
        db,
        EVIDENCE_KEY,
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        description="淘宝活动逐SKUID最低标价/最低普惠券后价证据及采集时间",
    )


def record_activity_export(
    db: Session,
    records: Iterable[dict],
    *,
    source: str,
    observed_at: Optional[datetime] = None,
) -> dict:
    """Persist fresh H/I columns from a platform activity export.

    The export already contains the platform's current control-window result, so
    fresh values replace old values.  This intentionally does not take a
    lifetime minimum: a historical line may expire when its platform window ends.
    """
    now = observed_at or datetime.now(timezone.utc)
    seen_at = now.astimezone(timezone.utc).isoformat()
    payload = _load(db)
    observed = 0
    for record in records:
        sid = str(record.get("sku_id") or "").strip()
        if not sid:
            continue
        observed += 1
        min_list = _decimal(record.get("min_list_price"))
        min_coupon = _decimal(record.get("min_coupon_line"))
        payload[sid] = {
            "sku_id": sid,
            "item_id": str(record.get("item_id") or "").strip(),
            "sku_name": str(record.get("sku_name") or "").strip(),
            "min_list_price": float(min_list) if min_list is not None else None,
            "min_coupon_line": float(min_coupon) if min_coupon is not None else None,
            # Blank H/I cells in an authoritative current activity export mean
            # that the platform exposed the gate but has no numeric requirement.
            # Preserve that distinction from a SKU that was absent from the export.
            "min_list_price_observed": "min_list_price" in record,
            "min_coupon_line_observed": "min_coupon_line" in record,
            "observed_at": seen_at,
            "source": source,
        }
    _write(db, payload)
    db.flush()
    return {"observed": observed, "source": source}


def record_partial_evidence(
    db: Session,
    records: Iterable[dict],
    *,
    source: str,
    observed_at: Optional[datetime] = None,
) -> dict:
    """Merge explicitly supplied floor columns without clearing the other gate.

    This exists only for old feedback spreadsheets that contain one of the two
    platform lines.  R17 still blocks submission until both lines are present
    and fresh.  No ERP or signup price field is changed.
    """
    now = observed_at or datetime.now(timezone.utc)
    seen_at = now.astimezone(timezone.utc).isoformat()
    payload = _load(db)
    observed = 0
    for record in records:
        sid = str(record.get("sku_id") or "").strip()
        if not sid:
            continue
        min_list = _decimal(record.get("min_list_price"))
        min_coupon = _decimal(record.get("min_coupon_line"))
        if min_list is None and min_coupon is None:
            continue
        observed += 1
        previous = payload.get(sid) if isinstance(payload.get(sid), dict) else {}
        payload[sid] = {
            "sku_id": sid,
            "item_id": str(record.get("item_id") or previous.get("item_id") or "").strip(),
            "sku_name": str(record.get("sku_name") or previous.get("sku_name") or "").strip(),
            "min_list_price": (
                float(min_list) if min_list is not None else previous.get("min_list_price")),
            "min_coupon_line": (
                float(min_coupon) if min_coupon is not None else previous.get("min_coupon_line")),
            "min_list_price_observed": bool(
                min_list is not None or previous.get("min_list_price_observed")),
            "min_coupon_line_observed": bool(
                min_coupon is not None or previous.get("min_coupon_line_observed")),
            "observed_at": seen_at,
            "source": source,
        }
    if observed:
        _write(db, payload)
        db.flush()
    return {"observed": observed, "source": source}


def _extract(patterns, text: str) -> Optional[Decimal]:
    values: list[Decimal] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            value = _decimal(match.group(1))
            if value is not None:
                values.append(value)
    return min(values) if values else None


def record_failed_feedback(
    db: Session,
    failed_items: Iterable[dict] | None,
    *,
    source: str,
    observed_at: Optional[datetime] = None,
) -> dict:
    """Record exact lines from failed feedback without adjusting or retrying."""
    now = observed_at or datetime.now(timezone.utc)
    seen_at = now.astimezone(timezone.utc).isoformat()
    payload = _load(db)
    learned: list[dict] = []
    for item in failed_items or []:
        sid = str(item.get("sku_id") or "").strip()
        raw = str(item.get("raw") or item.get("reason") or "")
        if not sid or not raw:
            continue
        min_list = _extract(_MIN_LIST_PATTERNS, raw)
        min_coupon = _extract(_MIN_COUPON_PATTERNS, raw)
        if min_list is None and min_coupon is None:
            continue
        previous = payload.get(sid) if isinstance(payload.get(sid), dict) else {}
        entry = {
            "sku_id": sid,
            "item_id": str(item.get("item_id") or previous.get("item_id") or "").strip(),
            "sku_name": str(previous.get("sku_name") or ""),
            "min_list_price": (
                float(min_list) if min_list is not None else previous.get("min_list_price")),
            "min_coupon_line": (
                float(min_coupon) if min_coupon is not None else previous.get("min_coupon_line")),
            "min_list_price_observed": bool(
                min_list is not None or previous.get("min_list_price_observed")),
            "min_coupon_line_observed": bool(
                min_coupon is not None or previous.get("min_coupon_line_observed")),
            "observed_at": seen_at,
            "source": source,
        }
        payload[sid] = entry
        learned.append(entry)
    if learned:
        _write(db, payload)
        db.flush()
    return {"learned": learned, "count": len(learned), "source": source}


def evidence_age_hours(entry: dict, *, now: Optional[datetime] = None) -> Optional[float]:
    raw = entry.get("observed_at") if isinstance(entry, dict) else None
    if not raw:
        return None
    try:
        observed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return max(0.0, (current.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds() / 3600)
