"""工厂订单付款状态对账回填 — 消除"工厂欠款虚高"。

历史工厂订单导入后 `payment_status` 一直停在默认 'unpaid'(从未回填), 致现金流
"工厂结算未付"虚高。这里按证据/对账把确实已付的回填为 'paid':

  规则A(直接证据): 有支付宝流水号(alipay_flow_no) 或 有付款日期(payment_date) → 已付。
  规则B(已结算推断, 可关): 关联平台订单已签收(signed) 且 工厂下单距今 > 结算周期(默认45天)
       → 货已发出、客户已签收, 月结早已结清 → 判定已付(写备注便于审计/回溯)。

近期单 / 无证据无关联的单保持 unpaid(如实反映近期应付)。dry_run=True 只统计不落库。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import FactoryOrder, Order
from app.services import settings_service

_BACKFILL_NOTE = "对账回填:关联订单已签收且超结算周期,判定已结"

# 工厂结算周期(天) — 规则B用; 后台可配(system_settings), 默认 45。
SETTING_SETTLEMENT_DAYS = "factory_settlement_days"
DEFAULT_SETTLEMENT_DAYS = 45


def get_settlement_days(db: Session) -> int:
    raw = settings_service.get(db, SETTING_SETTLEMENT_DAYS, env_fallback=False)
    if raw is None or str(raw).strip() == "":
        return DEFAULT_SETTLEMENT_DAYS
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return DEFAULT_SETTLEMENT_DAYS


def set_settlement_days(db: Session, days: int) -> None:
    settings_service.set_value(
        db, SETTING_SETTLEMENT_DAYS, str(int(days)),
        description="工厂结算周期(天) — 工厂欠款回填规则B(关联订单已签收且超此天数判已结)",
    )


def backfill_payment_status(
    db: Session, *,
    settlement_days: Optional[int] = None,
    apply_settled_inference: bool = True,
    dry_run: bool = False,
) -> dict:
    """把确实已付的工厂订单 payment_status 从 unpaid 回填为 paid。

    settlement_days 缺省时读后台配置 (system_settings.factory_settlement_days, 默认 45)。
    返回 {scanned, by_evidence, by_settled, still_unpaid, ...}。
    """
    if settlement_days is None:
        settlement_days = get_settlement_days(db)
    rows = db.execute(
        select(FactoryOrder).where(
            FactoryOrder.payment_status == "unpaid",
            FactoryOrder.voided_at.is_(None),
        )
    ).scalars().all()

    # 关联平台订单状态/下单日 (按订单号 / 按 id)
    order_by_no: dict[str, tuple] = {}
    order_by_id: dict[int, tuple] = {}
    for oid, ono, st, od in db.execute(
        select(Order.id, Order.order_no, Order.status, Order.order_date)
    ).all():
        if ono:
            order_by_no[ono] = (st, od)
        order_by_id[oid] = (st, od)

    today = date.today()
    by_evidence = by_settled = still_unpaid = 0
    for fo in rows:
        # 规则A: 直接证据
        if fo.alipay_flow_no or fo.payment_date:
            if not dry_run:
                fo.payment_status = "paid"
                if not fo.payment_date:
                    fo.payment_date = today
            by_evidence += 1
            continue

        # 规则B: 关联订单已签收且超结算周期 → 判定已结
        settled = False
        if apply_settled_inference:
            link = order_by_no.get(fo.platform_order_no) if fo.platform_order_no else None
            if link is None and fo.source_order_id:
                link = order_by_id.get(fo.source_order_id)
            if link:
                st, od = link
                age_date = fo.order_date or od
                if st == "signed" and age_date and (today - age_date).days > settlement_days:
                    settled = True
        if settled:
            if not dry_run:
                fo.payment_status = "paid"
                fo.payment_date = fo.payment_date or today
                fo.remark = ((fo.remark + " | ") if fo.remark else "") + _BACKFILL_NOTE
            by_settled += 1
        else:
            still_unpaid += 1

    if not dry_run:
        db.flush()
    return {
        "scanned": len(rows),
        "by_evidence": by_evidence,        # 有流水号/付款日 → 已付
        "by_settled": by_settled,          # 关联订单已签收且超结算周期 → 判定已结
        "still_unpaid": still_unpaid,       # 保持未付(近期/无证据)
        "settlement_days": settlement_days,
        "applied_inference": apply_settled_inference,
        "dry_run": dry_run,
    }
