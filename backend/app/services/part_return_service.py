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

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
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


# ----------------------------- 供应商退款流水自动匹配 ----------------- #

# 自动结算阈值: 金额一致(3) + 供应商匹配(3) = 6 才敢自动对账 (保守, 避免错配)。
_AUTO_SETTLE_SCORE = 6


def _linked_flow_nos(db: Session) -> set[str]:
    """已被某条返厂单占用的支付宝流水号 (避免一笔退款配到多单)."""
    rows = db.execute(
        select(PartReturn.alipay_flow_no).where(PartReturn.alipay_flow_no.isnot(None))
    ).scalars()
    return {r for r in rows if r}


def find_refund_candidates(db: Session, return_id: int, *, limit: int = 8) -> list[dict]:
    """为一条待收退款的返厂单, 找疑似供应商退款的支付宝流水 (收入流水).

    评分: 金额一致(+3)/接近(+1) · 供应商匹配(+3) · 日期在返厂之后(+1)。按分降序返回。
    """
    rec = db.get(PartReturn, return_id)
    if rec is None or rec.amount_kind != "refund" or rec.amount is None:
        return []
    expected = Decimal(rec.amount)
    if expected <= 0:
        return []
    tol = max(expected * Decimal("0.01"), Decimal("5"))   # 1% 或 5 元, 取大
    lo, hi = expected - tol, expected + tol
    linked = _linked_flow_nos(db)
    sup = (rec.supplier or "").strip()
    flows = db.execute(
        select(AlipayFlow).where(
            AlipayFlow.amount > 0,           # 退款是收入
            AlipayFlow.amount >= lo,
            AlipayFlow.amount <= hi,
        )
    ).scalars().all()
    out: list[dict] = []
    for f in flows:
        if f.transaction_no in linked:
            continue
        amt = Decimal(f.amount)
        diff = abs(amt - expected)
        score = 0
        reasons: list[str] = []
        if diff == 0:
            score += 3
            reasons.append("金额一致")
        else:
            score += 1
            reasons.append(f"金额接近(差{diff})")
        cp = f.counterparty or ""
        if sup and (sup in cp or cp in sup):
            score += 3
            reasons.append("供应商匹配")
        if f.transaction_time and rec.processed_at:
            if f.transaction_time.date() >= rec.processed_at - timedelta(days=1):
                score += 1
                reasons.append("日期在返厂后")
            else:
                score -= 1
        out.append({
            "transaction_no": f.transaction_no,
            "account": f.account,
            "transaction_time": f.transaction_time.isoformat() if f.transaction_time else None,
            "counterparty": f.counterparty,
            "amount": float(amt),
            "score": score,
            "reason": " · ".join(reasons),
        })
    out.sort(key=lambda x: (-x["score"], x["amount"]))
    return out[:limit]


def auto_reconcile(db: Session, *, actor: str = "system") -> dict:
    """一键自动对账: 对每条待收退款单, 仅当"唯一且金额一致+供应商匹配"的强候选时自动结算.

    其余 (无供应商/多候选/仅金额接近) 保守留给人工, 不乱配。
    """
    matched: list[dict] = []
    for rec in list_returns(db, status="open"):
        if rec.amount_kind != "refund" or rec.amount is None:
            continue
        cands = find_refund_candidates(db, rec.id, limit=5)
        strong = [c for c in cands if c["score"] >= _AUTO_SETTLE_SCORE]
        if len(strong) == 1:
            settle(db, rec.id, alipay_flow_no=strong[0]["transaction_no"],
                   actor=actor, remark="自动对账: 匹配供应商退款流水")
            matched.append({
                "return_id": rec.id, "material_code": rec.material_code,
                "transaction_no": strong[0]["transaction_no"],
                "amount": float(rec.amount),
            })
    return {"matched": len(matched), "details": matched}
