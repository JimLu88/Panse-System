"""工厂逐单对账 — 基于导入的工厂侧对账单 (factory_recon_items)。

口径 (用户确认): 「价格」= 工厂结算价 = 应付工厂的成本。
逐月对账: 应付(Σ结算价) ↔ 实付(支付宝 reconciliation_type='factory_payment' 支出)。
对不上 → 逐单查, 在条目上「填原因做平」(resolved + settle_reason, 记 谁/何时)。
当某月所有差异条目都已填原因, 该月状态 = explained(已归因做平)。

与 factory_reconciliation_service 的区别: 那个是 表6 FactoryOrder 的 (工厂×周期) 汇总;
本服务是工厂侧对账单逐行明细 + 行级做平 (也是手工差异归因的雏形)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.factory_recon_item import FactoryReconItem
from app.models.finance import AlipayFlow

_TOLERANCE = Decimal("5")   # 应付 vs 实付 对平容差 (元)


def _month_key(d) -> Optional[str]:
    return f"{d.year}-{d.month:02d}" if d else None


def _paid_by_month(db: Session) -> dict[str, Decimal]:
    """支付宝 factory_payment 支出按月汇总 (实付)。"""
    rows = db.execute(
        select(AlipayFlow.transaction_time, AlipayFlow.amount)
        .where(AlipayFlow.reconciliation_type == "factory_payment")
    ).all()
    out: dict[str, Decimal] = {}
    for t, amt in rows:
        key = _month_key(t) if t else "(无日期)"
        out[key] = out.get(key, Decimal("0")) + abs(Decimal(amt or 0))
    return out


def summary(db: Session) -> dict:
    """逐月对账汇总: 应付/实付/差额/已归因, + 全局合计 + 覆盖。"""
    items = db.execute(select(FactoryReconItem)).scalars().all()
    paid_by_month = _paid_by_month(db)

    by_month: dict[str, dict] = {}
    for it in items:
        key = _month_key(it.order_date) or "(无日期)"
        m = by_month.setdefault(key, {
            "period": key, "items_total": 0, "items_resolved": 0,
            "billed": Decimal("0"), "resolved_amount": Decimal("0"),
        })
        m["items_total"] += 1
        m["billed"] += it.settle_price or Decimal("0")
        if it.resolved:
            m["items_resolved"] += 1
            m["resolved_amount"] += it.settle_price or Decimal("0")

    rows = []
    tot_billed = tot_paid = Decimal("0")
    for key in sorted(set(by_month) | set(paid_by_month), reverse=True):
        m = by_month.get(key, {"period": key, "items_total": 0, "items_resolved": 0,
                               "billed": Decimal("0"), "resolved_amount": Decimal("0")})
        billed = m["billed"]
        paid = paid_by_month.get(key, Decimal("0"))
        diff = paid - billed
        open_items = m["items_total"] - m["items_resolved"]
        if abs(diff) <= _TOLERANCE:
            status = "balanced"
        elif m["items_total"] > 0 and open_items == 0:
            status = "explained"     # 全部差异已填原因做平(残差=已解释的扣减/减免合计)
        else:
            status = "diff"          # 待归因
        rows.append({
            "period": key,
            "items_total": m["items_total"],
            "items_resolved": m["items_resolved"],
            "items_open": open_items,
            "billed": float(billed),
            "paid": float(paid),
            "diff": float(diff),
            "status": status,
        })
        tot_billed += billed
        tot_paid += paid

    return {
        "total_items": len(items),
        "total_billed": float(tot_billed),
        "total_paid": float(tot_paid),
        "total_diff": float(tot_paid - tot_billed),
        "resolved_items": sum(1 for it in items if it.resolved),
        "months": rows,
    }


def list_items(
    db: Session, *, period: Optional[str] = None, status: Optional[str] = None,
    q: Optional[str] = None, limit: int = 500, offset: int = 0,
) -> dict:
    """逐单明细列表。status: resolved / open; period: YYYY-MM; q: 订单号/客户/详情 关键词。"""
    stmt = select(FactoryReconItem).order_by(
        FactoryReconItem.order_date.desc().nulls_last(), FactoryReconItem.id.desc()
    )
    if status == "resolved":
        stmt = stmt.where(FactoryReconItem.resolved.is_(True))
    elif status == "open":
        stmt = stmt.where(FactoryReconItem.resolved.is_(False))
    rows = db.execute(stmt).scalars().all()

    def _match(it: FactoryReconItem) -> bool:
        if period and (_month_key(it.order_date) or "(无日期)") != period:
            return False
        if q:
            hay = " ".join(str(x or "") for x in (
                it.order_no, it.extra_order_no1, it.extra_order_no2,
                it.customer_info, it.detail, it.doc_no))
            if q not in hay:
                return False
        return True

    filtered = [it for it in rows if _match(it)]
    total = len(filtered)
    page = filtered[offset:offset + limit]
    return {
        "total": total,
        "rows": [{
            "id": it.id, "source_sheet": it.source_sheet, "doc_no": it.doc_no,
            "order_no": it.order_no, "extra_order_no1": it.extra_order_no1,
            "extra_order_no2": it.extra_order_no2, "detail": it.detail, "qty": it.qty,
            "settle_price": float(it.settle_price or 0),
            "customer_info": it.customer_info,
            "order_date": it.order_date.isoformat() if it.order_date else None,
            "ship_date": it.ship_date.isoformat() if it.ship_date else None,
            "remark": it.remark, "resolved": it.resolved,
            "settle_reason": it.settle_reason, "resolved_by": it.resolved_by,
            "resolved_at": it.resolved_at.isoformat() if it.resolved_at else None,
        } for it in page],
    }


RESOLUTION_KINDS = ("漏单", "价差", "运费", "补偿", "其他")


def split_item(db: Session, item_id: int, *, parts: list[dict],
               actor: Optional[str] = None) -> dict:
    """Plan L5: 把一条差异行拆成多条归因子行。Σ 子行金额必须 = 原行金额 (Decimal 校验)。

    parts: [{"amount": "120.00", "resolution_kind": "价差", "remark": "..."}]
    """
    it = db.get(FactoryReconItem, item_id)
    if it is None:
        raise ValueError(f"工厂对账条目不存在: {item_id}")
    if it.parent_item_id is not None:
        raise ValueError("子行不能再拆分")
    if not parts or len(parts) < 2:
        raise ValueError("拆分至少要两条")
    total = Decimal("0")
    cleaned: list[tuple[Decimal, str, Optional[str]]] = []
    for p in parts:
        try:
            amt = Decimal(str(p.get("amount")))
        except Exception:
            raise ValueError(f"拆分金额不是数字: {p.get('amount')!r}")
        kind = (p.get("resolution_kind") or "").strip()
        if kind not in RESOLUTION_KINDS:
            raise ValueError(f"归因必须是 {'/'.join(RESOLUTION_KINDS)} 之一: {kind!r}")
        cleaned.append((amt, kind, (p.get("remark") or None)))
        total += amt
    if total != Decimal(it.settle_price or 0):
        raise ValueError(f"拆分金额合计 {total} ≠ 原行金额 {it.settle_price}, 必须打平")
    children = []
    for amt, kind, remark in cleaned:
        child = FactoryReconItem(
            source_sheet=it.source_sheet, doc_no=it.doc_no, order_no=it.order_no,
            detail=it.detail, qty=it.qty, settle_price=amt,
            customer_info=it.customer_info, order_date=it.order_date,
            ship_date=it.ship_date, remark=remark,
            parent_item_id=it.id, resolution_kind=kind, source="split",
        )
        db.add(child)
        children.append(child)
    # 原行标记已做平 (被拆分), 金额保留供追溯
    it.resolved = True
    it.settle_reason = f"已拆分为 {len(children)} 条归因子行"
    it.resolved_by = actor
    it.resolved_at = datetime.now(timezone.utc)
    db.flush()
    return {"parent_id": it.id, "children": [c.id for c in children]}


def confirm_item(db: Session, item_id: int, *, resolution_kind: str,
                 actor: Optional[str] = None) -> dict:
    """Plan L5: 确认一条差异行的归因。全部确认后调用方可触发期间重算。"""
    it = db.get(FactoryReconItem, item_id)
    if it is None:
        raise ValueError(f"工厂对账条目不存在: {item_id}")
    kind = (resolution_kind or "").strip()
    if kind not in RESOLUTION_KINDS:
        raise ValueError(f"归因必须是 {'/'.join(RESOLUTION_KINDS)} 之一: {kind!r}")
    it.resolution_kind = kind
    it.confirmed_by = actor
    it.confirmed_at = datetime.now(timezone.utc)
    if not it.resolved:
        it.resolved = True
        it.settle_reason = it.settle_reason or f"确认归因: {kind}"
        it.resolved_by = actor
        it.resolved_at = it.confirmed_at
    db.flush()
    return {"id": it.id, "resolution_kind": it.resolution_kind,
            "confirmed_at": it.confirmed_at.isoformat()}


def resolve(db: Session, item_id: int, *, reason: str, actor: Optional[str] = None,
            resolved: bool = True) -> dict:
    """对某条工厂结算行「填原因做平」(或撤销)。reason=扣减/减免/差异原因。"""
    it = db.get(FactoryReconItem, item_id)
    if it is None:
        raise ValueError(f"工厂对账条目不存在: {item_id}")
    if resolved:
        if not (reason or "").strip():
            raise ValueError("做平必须填写原因")
        it.resolved = True
        it.settle_reason = reason.strip()
        it.resolved_by = actor
        it.resolved_at = datetime.now(timezone.utc)
    else:
        it.resolved = False
        it.settle_reason = None
        it.resolved_by = None
        it.resolved_at = None
    db.flush()
    return {"id": it.id, "resolved": it.resolved, "settle_reason": it.settle_reason}
