"""打包费月度应付 ↔ 支付流水核销。

上传阶段的本子合计只用于人工预览复核；入库后以 PackingBill 有效明细合计为应付。
支付宝付款通过本表的 allocation 明确分配到费用账期，支持一笔付款拆到多个月。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow, PackingBill, PackingPaymentAllocation


_YM_RE = re.compile(r"(?:(20\d{2})\s*年\s*)?([1-9]|1[0-2])\s*月")
_ISO_YM_RE = re.compile(r"(?<!\d)(20\d{2})[-/.]([01]?\d)(?!\d)")
_PACKING_TEXT_RE = re.compile(r"打包(?:费|费用|货款)?")
_STRONG_PACKING_RE = re.compile(r"打包费|打包费用")
_AMBIGUOUS_KEYS = ("包装", "泡沫", "纸箱", "木架", "材料", "运费", "快递", "手提袋")


def _money(value) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def _flow_text(flow: AlipayFlow) -> str:
    return " ".join(filter(None, (flow.counterparty, flow.counterparty_account, flow.remark,
                                  flow.transaction_type)))


def suggested_months(flow: AlipayFlow, known_months: Optional[Iterable[str]] = None) -> list[str]:
    """从付款备注提取费用账期；无年份时按付款时间推断最近的过去月份。"""
    text = _flow_text(flow)
    found: list[tuple[Optional[int], int]] = []
    occupied: list[tuple[int, int]] = []
    for m in _ISO_YM_RE.finditer(text):
        month = int(m.group(2))
        if 1 <= month <= 12:
            found.append((int(m.group(1)), month))
            occupied.append(m.span())
    for m in _YM_RE.finditer(text):
        if any(a <= m.start() < b for a, b in occupied):
            continue
        found.append((int(m.group(1)) if m.group(1) else None, int(m.group(2))))

    tx_date = flow.transaction_time.date() if flow.transaction_time else None
    explicit_year = next((y for y, _ in found if y is not None), None)
    known = set(known_months or ())
    out: list[str] = []
    for year, month in found:
        if year is None:
            if explicit_year is not None:
                year = explicit_year
            elif tx_date is not None:
                year = tx_date.year if month <= tx_date.month else tx_date.year - 1
            else:
                matching = [ym for ym in known if ym.endswith(f"-{month:02d}")]
                if len(matching) != 1:
                    continue
                year = int(matching[0][:4])
        ym = f"{year:04d}-{month:02d}"
        if ym not in out:
            out.append(ym)
    return out


def is_candidate(flow: AlipayFlow) -> bool:
    return _money(flow.amount) < 0 and bool(_PACKING_TEXT_RE.search(_flow_text(flow)))


def is_strong_candidate(flow: AlipayFlow) -> bool:
    text = _flow_text(flow)
    return (is_candidate(flow) and bool(_STRONG_PACKING_RE.search(text))
            and not any(k in text for k in _AMBIGUOUS_KEYS))


def _allocated_by_flow(db: Session) -> dict[int, Decimal]:
    return {
        int(flow_id): _money(total)
        for flow_id, total in db.execute(
            select(PackingPaymentAllocation.alipay_flow_id,
                   func.coalesce(func.sum(PackingPaymentAllocation.amount), 0))
            .group_by(PackingPaymentAllocation.alipay_flow_id)
        ).all()
    }


def auto_allocate(db: Session) -> dict[str, int]:
    """只自动处理“明确写打包费 + 唯一账期”的支出；跨月/混合费用留给人工分配。"""
    from app.services import field_change_service

    allocated = _allocated_by_flow(db)
    locked = field_change_service.human_pks(
        db, table="alipay_flows", field="reconciliation_type")
    bill_months = set(db.execute(
        select(PackingBill.bill_month).where(PackingBill.bill_month.isnot(None)).distinct()
    ).scalars().all())
    created = reclassified = skipped = 0
    flows = db.execute(select(AlipayFlow).where(AlipayFlow.amount < 0)).scalars().all()
    for flow in flows:
        months = suggested_months(flow, bill_months)
        remaining = abs(_money(flow.amount)) - allocated.get(flow.id, Decimal("0.00"))
        if (allocated.get(flow.id, Decimal("0.00")) > 0
                or not is_strong_candidate(flow) or len(months) != 1 or months[0] not in bill_months
                or remaining <= Decimal("0.00")):
            if is_candidate(flow) and remaining > Decimal("0.00"):
                skipped += 1
            continue
        db.add(PackingPaymentAllocation(
            alipay_flow_id=flow.id, bill_month=months[0], amount=remaining,
            allocation_source="auto", note="按付款备注中的唯一打包费账期自动分配",
        ))
        allocated[flow.id] = allocated.get(flow.id, Decimal("0.00")) + remaining
        created += 1
        if str(flow.id) not in locked and flow.reconciliation_type != "packing_payment":
            flow.reconciliation_type = "packing_payment"
            reclassified += 1
    db.flush()
    return {"allocated": created, "reclassified": reclassified, "needs_review": skipped}


def create_allocation(db: Session, *, flow_id: int, bill_month: str, amount,
                      note: Optional[str] = None) -> PackingPaymentAllocation:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", bill_month or ""):
        raise ValueError("账期格式应为 YYYY-MM")
    flow = db.get(AlipayFlow, flow_id)
    if flow is None:
        raise LookupError("支付宝流水不存在")
    if _money(flow.amount) >= 0:
        raise ValueError("只有支出流水可以核销打包费")
    value = _money(amount)
    if value <= 0:
        raise ValueError("分配金额必须大于 0")

    existing = db.execute(select(PackingPaymentAllocation).where(
        PackingPaymentAllocation.alipay_flow_id == flow_id,
        PackingPaymentAllocation.bill_month == bill_month,
    )).scalar_one_or_none()
    other_total = db.execute(select(func.coalesce(func.sum(PackingPaymentAllocation.amount), 0)).where(
        PackingPaymentAllocation.alipay_flow_id == flow_id,
        PackingPaymentAllocation.id != (existing.id if existing else -1),
    )).scalar() or 0
    if _money(other_total) + value > abs(_money(flow.amount)) + Decimal("0.01"):
        raise ValueError(f"累计分配不能超过流水支出 ¥{abs(_money(flow.amount))}")

    if existing is None:
        existing = PackingPaymentAllocation(
            alipay_flow_id=flow_id, bill_month=bill_month, amount=value,
            allocation_source="manual", note=note,
        )
        db.add(existing)
    else:
        existing.amount = value
        existing.allocation_source = "manual"
        existing.note = note
    flow.reconciliation_type = "packing_payment"
    db.flush()
    return existing


def delete_allocation(db: Session, allocation_id: int) -> bool:
    row = db.get(PackingPaymentAllocation, allocation_id)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def _due_date(db: Session, bill_month: str) -> date:
    year, month = map(int, bill_month.split("-"))
    days = 45
    try:
        from app.services import settings_service
        raw = settings_service.get(db, "packing_payment_due_days", env_fallback=False)
        if raw is not None:
            days = max(0, min(180, int(raw)))
    except Exception:
        pass
    end = date(year, month, calendar.monthrange(year, month)[1])
    return end + timedelta(days=days)


def month_summary(db: Session, bill_month: str, *, include_candidates: bool = True) -> dict:
    payable = _money(db.execute(select(func.coalesce(func.sum(PackingBill.packing_fee), 0)).where(
        PackingBill.bill_month == bill_month,
        PackingBill.excluded.is_(False),
    )).scalar())
    allocations = db.execute(
        select(PackingPaymentAllocation, AlipayFlow)
        .join(AlipayFlow, AlipayFlow.id == PackingPaymentAllocation.alipay_flow_id)
        .where(PackingPaymentAllocation.bill_month == bill_month)
        .order_by(AlipayFlow.transaction_time, PackingPaymentAllocation.id)
    ).all()
    paid = sum((_money(a.amount) for a, _ in allocations), Decimal("0.00"))
    diff = paid - payable
    due = _due_date(db, bill_month)
    pending = date.today() <= due and diff < Decimal("-0.01")
    if abs(diff) <= Decimal("0.01"):
        status = "balanced"
    elif pending:
        status = "pending"
    elif paid == 0:
        status = "unpaid"
    elif diff < 0:
        status = "partial"
    elif payable == 0:
        status = "no_bill"
    else:
        status = "overpaid"

    payment_rows = [{
        "allocation_id": a.id,
        "flow_id": f.id,
        "bill_month": a.bill_month,
        "allocated_amount": float(_money(a.amount)),
        "allocation_source": a.allocation_source,
        "note": a.note,
        "transaction_no": f.transaction_no,
        "transaction_time": f.transaction_time.isoformat() if f.transaction_time else None,
        "account": f.account,
        "counterparty": f.counterparty,
        "counterparty_account": f.counterparty_account,
        "flow_amount": float(abs(_money(f.amount))),
        "remark": f.remark,
    } for a, f in allocations]

    candidates: list[dict] = []
    if include_candidates:
        allocated_by_flow = _allocated_by_flow(db)
        known_months = set(db.execute(
            select(PackingBill.bill_month).where(PackingBill.bill_month.isnot(None)).distinct()
        ).scalars().all())
        allocated_month_keys = set(db.execute(
            select(PackingPaymentAllocation.alipay_flow_id, PackingPaymentAllocation.bill_month)
        ).all())
        for flow in db.execute(select(AlipayFlow).where(AlipayFlow.amount < 0)
                               .order_by(AlipayFlow.transaction_time.desc().nulls_last(),
                                         AlipayFlow.id.desc())).scalars().all():
            if not is_candidate(flow):
                continue
            remaining = abs(_money(flow.amount)) - allocated_by_flow.get(flow.id, Decimal("0.00"))
            if remaining <= Decimal("0.00"):
                continue
            months = suggested_months(flow, known_months)
            if months and bill_month not in months:
                continue
            if (flow.id, bill_month) in allocated_month_keys:
                continue
            candidates.append({
                "flow_id": flow.id,
                "transaction_no": flow.transaction_no,
                "transaction_time": flow.transaction_time.isoformat() if flow.transaction_time else None,
                "account": flow.account,
                "counterparty": flow.counterparty,
                "counterparty_account": flow.counterparty_account,
                "flow_amount": float(abs(_money(flow.amount))),
                "remaining_amount": float(remaining),
                "reconciliation_type": flow.reconciliation_type,
                "remark": flow.remark,
                "suggested_months": months,
                "auto_eligible": is_strong_candidate(flow) and len(months) == 1,
            })

    return {
        "bill_month": bill_month,
        "payable_total": float(payable),
        "paid_total": float(paid),
        "diff": float(diff),
        "status": status,
        "due_date": due.isoformat(),
        "payments": payment_rows,
        "candidates": candidates,
    }
