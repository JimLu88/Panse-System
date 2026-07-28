"""工厂预付款（待抵扣）台账。

预付款是真实已经转出的现金，因此不改支付宝原始流水；但在工厂尚未抵扣前，
它仍是企业可收回/可抵账的资产，计入现金流加项。月结销账时按目标月份自动
抵扣，撤销销账时恢复；人工也可以直接把余额改为 0，并保留完整变更记录。

为避免给正在开发的采购模块引入迁移冲突，台账以结构化 JSON 存在
``system_settings``。所有变更都写不可删除的 history，便于追溯。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service

SETTING_FACTORY_ADVANCE_LEDGER = "factory_advance_ledger_v1"
_DESCRIPTION = "工厂预付款待抵扣台账(JSON，含人工修改/自动抵扣/撤销历史)"
_Q = Decimal("0.01")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_MAX_HISTORY = 200


def _money(value) -> Decimal:
    try:
        return max(Decimal("0"), Decimal(str(value or "0"))).quantize(_Q)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def _default() -> dict:
    return {
        "balance": "0.00",
        "target_month": None,
        "note": "",
        "updated_at": None,
        "updated_by": None,
        "history": [],
    }


def _load(db: Session) -> dict:
    raw = settings_service.get(
        db, SETTING_FACTORY_ADVANCE_LEDGER, env_fallback=False,
    )
    if not raw:
        return _default()
    try:
        saved = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return _default()
    if not isinstance(saved, dict):
        return _default()
    state = _default()
    state.update(saved)
    state["balance"] = str(_money(state.get("balance")))
    target = str(state.get("target_month") or "").strip()
    state["target_month"] = target if _MONTH_RE.fullmatch(target) else None
    state["note"] = str(state.get("note") or "")[:500]
    state["history"] = state.get("history") if isinstance(state.get("history"), list) else []
    return state


def _save(db: Session, state: dict) -> None:
    state["history"] = state.get("history", [])[-_MAX_HISTORY:]
    settings_service.set_value(
        db,
        SETTING_FACTORY_ADVANCE_LEDGER,
        json.dumps(state, ensure_ascii=False, separators=(",", ":")),
        description=_DESCRIPTION,
    )


def get_state(db: Session) -> dict:
    """返回前端可直接使用的预付款余额及最近变更记录。"""
    state = _load(db)
    balance = _money(state["balance"])
    return {
        "balance": str(balance),
        "target_month": state.get("target_month"),
        "note": state.get("note") or "",
        "status": "pending" if balance > 0 else "settled",
        "updated_at": state.get("updated_at"),
        "updated_by": state.get("updated_by"),
        "history": list(reversed(state.get("history", [])[-20:])),
    }


def set_manual(
    db: Session,
    *,
    balance: Decimal,
    target_month: Optional[str] = None,
    note: Optional[str] = None,
    by: Optional[str] = None,
) -> dict:
    """人工设置待抵扣余额；允许设置为 0，且不删除原始流水或旧记录。"""
    new_balance = _money(balance)
    month = str(target_month or "").strip()
    if month and not _MONTH_RE.fullmatch(month):
        raise ValueError("预计抵扣月份必须为 YYYY-MM")

    state = _load(db)
    before = _money(state["balance"])
    now = datetime.now(timezone.utc).isoformat()
    next_note = state.get("note") or "" if note is None else str(note).strip()[:500]
    event = {
        "kind": "manual",
        "before": str(before),
        "after": str(new_balance),
        "amount": str((new_balance - before).quantize(_Q)),
        "target_month": month or None,
        "note": next_note,
        "at": now,
        "by": by,
    }
    state.update({
        "balance": str(new_balance),
        "target_month": month or None,
        "note": next_note,
        "updated_at": now,
        "updated_by": by,
    })
    state.setdefault("history", []).append(event)
    _save(db, state)
    return get_state(db)


def apply_for_settlement(
    db: Session,
    *,
    payment_id: int,
    month: str,
    billed_total: Decimal,
    by: Optional[str] = None,
) -> dict:
    """销账时自动抵扣目标月份预付款；同一 payment_id 幂等。"""
    state = _load(db)
    balance = _money(state["balance"])
    billed = _money(billed_total)
    target = state.get("target_month")
    history = state.setdefault("history", [])
    if balance <= 0 or billed <= 0 or (target and target != month):
        return {"used": Decimal("0.00"), "remaining": balance}
    if any(e.get("kind") == "apply" and e.get("payment_id") == payment_id for e in history):
        return {"used": Decimal("0.00"), "remaining": balance}

    used = min(balance, billed).quantize(_Q)
    remaining = (balance - used).quantize(_Q)
    now = datetime.now(timezone.utc).isoformat()
    history.append({
        "kind": "apply",
        "payment_id": payment_id,
        "settlement_month": month,
        "before": str(balance),
        "after": str(remaining),
        "amount": str(used),
        "at": now,
        "by": by,
    })
    state.update({
        "balance": str(remaining),
        "updated_at": now,
        "updated_by": by,
    })
    _save(db, state)
    return {"used": used, "remaining": remaining}


def reverse_for_settlement(
    db: Session, *, payment_id: int, by: Optional[str] = None,
) -> dict:
    """撤销销账时恢复该批自动抵扣的预付款；重复撤销不重复增加。"""
    state = _load(db)
    history = state.setdefault("history", [])
    applied = next(
        (
            e for e in reversed(history)
            if e.get("kind") == "apply" and e.get("payment_id") == payment_id
        ),
        None,
    )
    already_reversed = any(
        e.get("kind") == "reverse" and e.get("payment_id") == payment_id
        for e in history
    )
    balance = _money(state["balance"])
    if not applied or already_reversed:
        return {"restored": Decimal("0.00"), "remaining": balance}

    restored = _money(applied.get("amount"))
    remaining = (balance + restored).quantize(_Q)
    now = datetime.now(timezone.utc).isoformat()
    history.append({
        "kind": "reverse",
        "payment_id": payment_id,
        "settlement_month": applied.get("settlement_month"),
        "before": str(balance),
        "after": str(remaining),
        "amount": str(restored),
        "at": now,
        "by": by,
    })
    state.update({
        "balance": str(remaining),
        "updated_at": now,
        "updated_by": by,
    })
    _save(db, state)
    return {"restored": restored, "remaining": remaining}


def applied_by_payment(db: Session) -> dict[int, Decimal]:
    """返回未撤销的每笔销账预付款抵扣额，供销账记录展示。"""
    history = _load(db).get("history", [])
    reversed_ids = {
        int(e["payment_id"])
        for e in history
        if e.get("kind") == "reverse" and e.get("payment_id") is not None
    }
    out: dict[int, Decimal] = {}
    for event in history:
        pid = event.get("payment_id")
        if event.get("kind") != "apply" or pid is None or int(pid) in reversed_ids:
            continue
        out[int(pid)] = _money(event.get("amount"))
    return out
