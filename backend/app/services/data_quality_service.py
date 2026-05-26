"""数据完整性扫描 (Phase 13) — B1–B11 全部规则.

每条规则写入 DataException 异常池 (复用 exception_service.record).
所有扫描器幂等: 同 source_table+source_pk+type 不重复堆积.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.finance import AlipayFlow, RefillRecord, FactoryReconciliation
from app.models.marketing import OutsourcingExpense, AfterSales
from app.services import exception_service, settings_service

_log = logging.getLogger("panse.data_quality")


def _record(db: Session, **kwargs: Any) -> None:
    """幂等写: 若同 source_table+source_pk+type+status=open 已存在则跳过."""
    from app.models.exception import DataException
    existing = db.query(DataException).filter_by(
        source_table=kwargs.get("source_table"),
        source_pk=str(kwargs.get("source_pk")),
        exception_type=kwargs.get("exception_type"),
        status="open",
    ).first()
    if existing:
        return
    exception_service.record(db, **kwargs)


# ---------------------------------------------------------------------------
# B1 — 订单缺理论/实际成本
# ---------------------------------------------------------------------------

def scan_order_missing_cost(db: Session) -> int:
    count = 0
    for o in db.query(Order).filter(
        Order.status.notin_(["cancelled"]),
        Order.is_historical == False,  # noqa: E712
    ).all():
        if o.theoretical_cost is None and o.actual_cost is None:
            _record(
                db,
                source_table="orders",
                source_pk=o.id,
                exception_type="order_missing_cost",
                severity="warning",
                description=f"订单 {o.order_no} 理论成本与实际成本均未填写, 影响毛利核算。",
                suggestion_action="请补填理论成本或实际成本。",
                context={"order_no": o.order_no, "status": o.status},
            )
            count += 1
    _log.info("scan_order_missing_cost: %d orders missing cost", count)
    return count


# ---------------------------------------------------------------------------
# B2 — 订单缺支付宝流水号匹配
# ---------------------------------------------------------------------------

def scan_order_missing_alipay(db: Session) -> int:
    linked = {
        row.related_order_no
        for row in db.query(AlipayFlow).filter(AlipayFlow.related_order_no.isnot(None)).all()
    }
    count = 0
    for o in db.query(Order).filter(
        Order.status.notin_(["cancelled", "pending_payment"]),
        Order.is_historical == False,  # noqa: E712
    ).all():
        if o.order_no not in linked:
            _record(
                db,
                source_table="orders",
                source_pk=o.id,
                exception_type="order_missing_alipay",
                severity="warning",
                description=f"订单 {o.order_no} 无对应支付宝流水记录, 无法财务匹配。",
                suggestion_action="在支付宝流水页找到对应记录并填写 related_order_no。",
                context={"order_no": o.order_no, "paid_amount": str(o.paid_amount)},
            )
            count += 1
    _log.info("scan_order_missing_alipay: %d unlinked", count)
    return count


# ---------------------------------------------------------------------------
# B3 — 导入陈旧提醒 (> N 天无新订单)
# ---------------------------------------------------------------------------

def scan_stale_import(db: Session) -> int:
    from sqlalchemy import func
    threshold = int(settings_service.get(db, "stale_import_days") or "7")
    latest = db.query(func.max(Order.order_date)).scalar()
    if latest is None:
        return 0
    if isinstance(latest, str):
        latest = date.fromisoformat(latest)
    days_stale = (date.today() - latest).days
    if days_stale > threshold:
        _record(
            db,
            source_table="orders",
            source_pk="stale_import",
            exception_type="stale_import",
            severity="error",
            description=f"最新订单日期 {latest}, 距今 {days_stale} 天 (阈值 {threshold} 天), 可能有订单未导入。",
            suggestion_action="请在导入页面上传最新订单 Excel。",
            context={"latest_order_date": str(latest), "days_stale": days_stale},
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# B4 — 已发货订单缺物流单号
# ---------------------------------------------------------------------------

def scan_order_missing_tracking(db: Session) -> int:
    count = 0
    for o in db.query(Order).filter(
        Order.status.in_(["shipped", "signed"]),
        Order.tracking_no.is_(None),
    ).all():
        _record(
            db,
            source_table="orders",
            source_pk=o.id,
            exception_type="order_missing_tracking",
            severity="warning",
            description=f"订单 {o.order_no} 状态为 {o.status}, 但物流单号为空。",
            suggestion_action="请补填承运商和物流单号。",
            context={"order_no": o.order_no, "status": o.status},
        )
        count += 1
    _log.info("scan_order_missing_tracking: %d", count)
    return count


# ---------------------------------------------------------------------------
# B7 — 补单记录: 订单号在主订单表中找不到 → 报异常
# ---------------------------------------------------------------------------

def scan_refill_unmatched(db: Session) -> int:
    known_orders = {o.order_no for o in db.query(Order.order_no).all()}
    count = 0
    for r in db.query(RefillRecord).all():
        if r.order_no not in known_orders:
            _record(
                db,
                source_table="refill_records",
                source_pk=r.id,
                exception_type="refill_unmatched",
                severity="warning",
                description=f"补单记录 {r.id} 的订单号 '{r.order_no}' 在订单总表中找不到。",
                suggestion_action="请确认补单记录的订单号是否正确。",
                context={"refill_id": r.id, "order_no": r.order_no},
            )
            count += 1
    _log.info("scan_refill_unmatched: %d", count)
    return count


# ---------------------------------------------------------------------------
# B8 — 支付宝流水号空值 (空字符串)
# ---------------------------------------------------------------------------

def scan_alipay_missing_txn(db: Session) -> int:
    count = 0
    for row in db.query(AlipayFlow).filter(
        (AlipayFlow.transaction_no == "") | (AlipayFlow.transaction_no == "null")
    ).all():
        _record(
            db,
            source_table="alipay_flows",
            source_pk=row.id,
            exception_type="alipay_missing_txn",
            severity="warning",
            description=f"支付宝流水 (账户: {row.account}, 时间: {row.transaction_time}) 流水号为空, 无法去重/对账。",
            suggestion_action="请在支付宝后台找到该笔流水, 补填交易流水号。",
            context={"account": row.account, "amount": str(row.amount), "transaction_time": str(row.transaction_time)},
        )
        count += 1
    _log.info("scan_alipay_missing_txn: %d", count)
    return count


# ---------------------------------------------------------------------------
# B9 — 工厂对账单缺字段
# ---------------------------------------------------------------------------

def scan_factory_recon_incomplete(db: Session) -> int:
    count = 0
    for r in db.query(FactoryReconciliation).all():
        missing = []
        if r.bill_amount is None:
            missing.append("bill_amount (工厂账单金额)")
        if r.paid_amount is None:
            missing.append("paid_amount (实际支付)")
        if not r.alipay_flow_no:
            missing.append("alipay_flow_no (支付流水号)")
        if missing:
            _record(
                db,
                source_table="factory_reconciliations",
                source_pk=r.id,
                exception_type="factory_recon_incomplete",
                severity="warning",
                description=f"工厂对账 [{r.factory_name}] 缺少: {', '.join(missing)}。",
                suggestion_action="请补填工厂对账单对应字段。",
                context={"id": r.id, "factory_name": r.factory_name, "missing_fields": missing},
            )
            count += 1
    _log.info("scan_factory_recon_incomplete: %d", count)
    return count


# ---------------------------------------------------------------------------
# B10 — 人员外包费用缺流水号/支付日期
# ---------------------------------------------------------------------------

def scan_outsourcing_missing(db: Session) -> int:
    count = 0
    for r in db.query(OutsourcingExpense).all():
        missing = []
        if not r.alipay_flow_no:
            missing.append("alipay_flow_no")
        if not r.payment_date:
            missing.append("payment_date")
        if missing:
            _record(
                db,
                source_table="outsourcing_expenses",
                source_pk=r.id,
                exception_type="outsourcing_missing",
                severity="warning",
                description=f"人员外包费用 [{r.payee}] 缺少: {', '.join(missing)}。",
                suggestion_action="请补填外包费用的支付宝流水号和支付日期。",
                context={"id": r.id, "payee": r.payee, "missing_fields": missing},
            )
            count += 1
    _log.info("scan_outsourcing_missing: %d", count)
    return count


# ---------------------------------------------------------------------------
# B11 — 售后表为空
# ---------------------------------------------------------------------------

def scan_aftersales_empty(db: Session) -> int:
    total = db.query(AfterSales).count()
    if total == 0:
        _record(
            db,
            source_table="after_sales",
            source_pk="empty",
            exception_type="aftersales_empty",
            severity="info",
            description="售后表当前无任何记录, 如有历史售后单请及时录入。",
            suggestion_action="在售后/退货页面录入或导入历史售后数据。",
            context={"total": 0},
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# 全量扫描入口
# ---------------------------------------------------------------------------

def run_all(db: Session) -> dict[str, int]:
    results: dict[str, int] = {}
    scanners = [
        ("order_missing_cost", scan_order_missing_cost),
        ("order_missing_alipay", scan_order_missing_alipay),
        ("stale_import", scan_stale_import),
        ("order_missing_tracking", scan_order_missing_tracking),
        ("refill_unmatched", scan_refill_unmatched),
        ("alipay_missing_txn", scan_alipay_missing_txn),
        ("factory_recon_incomplete", scan_factory_recon_incomplete),
        ("outsourcing_missing", scan_outsourcing_missing),
        ("aftersales_empty", scan_aftersales_empty),
    ]
    for name, fn in scanners:
        try:
            results[name] = fn(db)
            db.commit()
        except Exception as e:  # pragma: no cover
            db.rollback()
            _log.error("scanner %s failed: %s", name, e)
            results[name] = -1
    return results
