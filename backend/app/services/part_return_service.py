"""配件返厂/退货 财务闭环 (方案 C).

在「处理待返厂坏件」时, 除了库存动作 (part_defect_service.resolve_defective), 再记一条
PartReturn 台账, 把钱也管起来:
    returned (退货退款) → amount = 应收供应商退款; status=open(待收), 收到后 settle()
    repaired (返厂维修) → amount = 维修费; status=settled (确认即记)
    scrapped (报废)     → amount = 报废损失(=采购成本); status=settled

之后用 list_returns / summary 做供应商对账 (待收退款 / 已收 / 维修费 / 报废损失)。

公开 API:
    record_resolution(...)  库存处置 + 生成台账
    settle(db, return_id, alipay_flow_no=...)  退款收到/费用结清 → settled
    list_returns(db, status=...)
    summary(db)  对账汇总
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import PartReturn
from app.models.material import Material
from app.services import part_defect_service

DEFAULT_WAREHOUSE = part_defect_service.DEFAULT_WAREHOUSE

# disposition -> 金额含义 (amount_kind)
_AMOUNT_KIND = {
    "returned": "refund",       # 应收供应商退款
    "repaired": "repair_fee",   # 返厂维修费
    "scrapped": "scrap_loss",   # 报废损失
}


def record_resolution(
    db: Session, *, material_code: str, qty, disposition: str,
    warehouse: str = DEFAULT_WAREHOUSE, actor: str = "user",
    amount=None, supplier: Optional[str] = None,
    related_purchase_no: Optional[str] = None,
    tracking_no: Optional[str] = None,
    reason: Optional[str] = None, remark: Optional[str] = None,
) -> PartReturn:
    """处理待返厂坏件: 先做库存处置, 再记一条返厂台账 (含钱)."""
    if disposition not in _AMOUNT_KIND:
        raise ValueError(f"未知处理方式: {disposition!r} (应为 repaired/scrapped/returned)")
    # 1) 库存处置 (含数量守卫 + 库存台账留痕)
    part_defect_service.resolve_defective(
        db, material_code=material_code, qty=qty, disposition=disposition,
        actor=actor, remark=remark, warehouse=warehouse,
    )
    # 2) 财务台账
    amt = Decimal(str(amount)) if amount not in (None, "") else None
    kind = _AMOUNT_KIND[disposition]
    # 只有"退货退款且金额>0"才需要等收款; 维修费/报废损失确认即结清。
    status = "open" if (disposition == "returned" and amt and amt > 0) else "settled"
    mat = db.execute(
        select(Material).where(Material.code == material_code)
    ).scalar_one_or_none()
    rec = PartReturn(
        material_code=material_code,
        material_name=mat.name if mat else None,
        warehouse=warehouse,
        qty=Decimal(str(qty)),
        disposition=disposition,
        amount_kind=kind,
        amount=amt,
        reason=reason,
        supplier=supplier,
        related_purchase_no=related_purchase_no,
        tracking_no=tracking_no,
        status=status,
        actor=actor,
        processed_at=date.today(),
        remark=remark,
    )
    db.add(rec)
    db.flush()
    return rec


def settle(
    db: Session, return_id: int, *,
    alipay_flow_no: Optional[str] = None, actor: str = "user",
    remark: Optional[str] = None,
) -> PartReturn:
    """退款收到 / 费用结清 → 标记 settled, 可关联支付宝流水号."""
    rec = db.get(PartReturn, return_id)
    if rec is None:
        raise ValueError("返厂单不存在")
    rec.status = "settled"
    if alipay_flow_no:
        rec.alipay_flow_no = alipay_flow_no
    if remark:
        rec.remark = f"{rec.remark}\n{remark}" if rec.remark else remark
    rec.processed_at = date.today()
    db.flush()
    return rec


def list_returns(
    db: Session, *, status: Optional[str] = None, limit: int = 200,
) -> list[PartReturn]:
    q = select(PartReturn).order_by(PartReturn.id.desc())
    if status:
        q = q.where(PartReturn.status == status)
    return list(db.execute(q.limit(limit)).scalars())


def summary(db: Session) -> dict:
    """供应商对账汇总: 待收退款 / 已收退款 / 维修费 / 报废损失."""
    rows = list(db.execute(select(PartReturn)).scalars())

    def _sum(pred) -> float:
        total = Decimal("0")
        for r in rows:
            if r.amount is not None and pred(r):
                total += Decimal(r.amount)
        return float(total)

    return {
        "pending_refund": _sum(lambda r: r.amount_kind == "refund" and r.status == "open"),
        "received_refund": _sum(lambda r: r.amount_kind == "refund" and r.status == "settled"),
        "repair_fee_total": _sum(lambda r: r.amount_kind == "repair_fee"),
        "scrap_loss_total": _sum(lambda r: r.amount_kind == "scrap_loss"),
        "open_count": sum(1 for r in rows if r.status == "open"),
        "total_count": len(rows),
    }
