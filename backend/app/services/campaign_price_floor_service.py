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
PLAN_EVIDENCE_KEY_PREFIX = "campaign_price_floor_evidence_v2_plan_"
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


def _plan_id(plan) -> Optional[str]:
    value = getattr(plan, "id", plan)
    text = str(value or "").strip()
    return text if text.isdigit() else None


def _storage_key(plan=None) -> str:
    plan_id = _plan_id(plan)
    return f"{PLAN_EVIDENCE_KEY_PREFIX}{plan_id}" if plan_id else EVIDENCE_KEY


def _decode(raw) -> dict[str, dict]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load(db: Session, *, plan=None, fallback_legacy: bool = True) -> dict[str, dict]:
    key = _storage_key(plan)
    payload = _decode(settings_service.get(db, key, env_fallback=False))
    if payload or key == EVIDENCE_KEY or not fallback_legacy:
        return payload
    # Compatibility bridge for plans that have not had their first scoped
    # refresh yet. Once a plan-specific setting exists, no other campaign can
    # overwrite or influence it.
    return _decode(settings_service.get(db, EVIDENCE_KEY, env_fallback=False))


def evidence_map(db: Session, *, plan=None) -> dict[str, dict]:
    return _load(db, plan=plan)


def _write(db: Session, payload: dict[str, dict], *, plan=None) -> None:
    settings_service.set_value(
        db,
        _storage_key(plan),
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        description=(
            f"淘宝活动计划{_plan_id(plan)}逐SKUID最低标价/最低普惠券后价证据"
            if _plan_id(plan)
            else "淘宝活动逐SKUID最低标价/最低普惠券后价证据及采集时间"
        ),
    )


def record_activity_export(
    db: Session,
    records: Iterable[dict],
    *,
    source: str,
    observed_at: Optional[datetime] = None,
    plan=None,
) -> dict:
    """Persist fresh H/I columns from a platform activity export.

    The export already contains the platform's current control-window result, so
    fresh values replace old values.  This intentionally does not take a
    lifetime minimum: a historical line may expire when its platform window ends.
    """
    now = observed_at or datetime.now(timezone.utc)
    seen_at = now.astimezone(timezone.utc).isoformat()
    payload = _load(db, plan=plan, fallback_legacy=False)
    observed = 0
    grouped: dict[str, dict] = {}
    for record in records:
        sid = str(record.get("sku_id") or "").strip()
        if not sid:
            continue
        observed += 1
        min_list = _decimal(record.get("min_list_price"))
        min_coupon = _decimal(record.get("min_coupon_line"))
        current = grouped.setdefault(sid, {
            "sku_id": sid,
            "item_id": "",
            "sku_name": "",
            "min_list_values": [],
            "min_coupon_values": [],
            "min_list_price_observed": False,
            "min_coupon_line_observed": False,
        })
        current["item_id"] = str(record.get("item_id") or current["item_id"] or "").strip()
        current["sku_name"] = str(record.get("sku_name") or current["sku_name"] or "").strip()
        if min_list is not None:
            current["min_list_values"].append(min_list)
        if min_coupon is not None:
            current["min_coupon_values"].append(min_coupon)
        current["min_list_price_observed"] = bool(
            current["min_list_price_observed"] or "min_list_price" in record)
        current["min_coupon_line_observed"] = bool(
            current["min_coupon_line_observed"] or "min_coupon_line" in record)

    for sid, current in grouped.items():
        # One activity export can contain the same SKU under several marketing
        # records.  Qualification uses the strictest active/historical line;
        # last-row-wins can silently erase it with a later blank row.
        min_list = min(current["min_list_values"], default=None)
        min_coupon = min(current["min_coupon_values"], default=None)
        payload[sid] = {
            "sku_id": sid,
            "item_id": current["item_id"],
            "sku_name": current["sku_name"],
            "min_list_price": float(min_list) if min_list is not None else None,
            "min_coupon_line": float(min_coupon) if min_coupon is not None else None,
            # Blank H/I cells in an authoritative current activity export mean
            # that the platform exposed the gate but has no numeric requirement.
            # Preserve that distinction from a SKU that was absent from the export.
            "min_list_price_observed": current["min_list_price_observed"],
            "min_coupon_line_observed": current["min_coupon_line_observed"],
            "observed_at": seen_at,
            "source": source,
        }
    _write(db, payload, plan=plan)
    db.flush()
    return {"observed": observed, "source": source, "scope": _storage_key(plan)}


def record_partial_evidence(
    db: Session,
    records: Iterable[dict],
    *,
    source: str,
    observed_at: Optional[datetime] = None,
    plan=None,
) -> dict:
    """Merge explicitly supplied floor columns without clearing the other gate.

    This exists only for old feedback spreadsheets that contain one of the two
    platform lines.  R17 still blocks submission until both lines are present
    and fresh.  No ERP or signup price field is changed.
    """
    now = observed_at or datetime.now(timezone.utc)
    seen_at = now.astimezone(timezone.utc).isoformat()
    payload = _load(db, plan=plan, fallback_legacy=False)
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
        _write(db, payload, plan=plan)
        db.flush()
    return {"observed": observed, "source": source, "scope": _storage_key(plan)}


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
    plan=None,
) -> dict:
    """Record exact lines from failed feedback without adjusting or retrying."""
    now = observed_at or datetime.now(timezone.utc)
    seen_at = now.astimezone(timezone.utc).isoformat()
    payload = _load(db, plan=plan, fallback_legacy=False)
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
        _write(db, payload, plan=plan)
        db.flush()
    return {
        "learned": learned,
        "count": len(learned),
        "source": source,
        "scope": _storage_key(plan),
    }


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
