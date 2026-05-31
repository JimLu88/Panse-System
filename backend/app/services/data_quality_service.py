"""数据完整性扫描 (Phase 13) — B1–B11 全部规则.

每条规则写入 DataException 异常池 (复用 exception_service.record).
所有扫描器幂等: 同 source_table+source_pk+type 不重复堆积.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models.order import FactoryOrder, Order
from app.models.finance import AlipayFlow, RefillRecord, FactoryReconciliation
from app.models.marketing import OutsourcingExpense, AfterSales, PromotionFlow, Sample, WoodLoss
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
# B12 — 支付宝余额连续性 (账户内 balance 跳变 → 流水可能缺失)
# ---------------------------------------------------------------------------

def scan_alipay_balance_gap(db: Session) -> int:
    """同账户按时间排序, 校验 balance[i] ≈ balance[i-1] + amount[i]。

    断点 (差额 > 1 元) → 报异常, 提示该账户有流水缺失或余额错位。
    仅对 balance 非空的相邻两条比较; 跳过缺 balance 的记录。
    """
    from decimal import Decimal
    count = 0
    accounts = [a[0] for a in db.query(AlipayFlow.account).distinct().all()]
    for account in accounts:
        rows = (
            db.query(AlipayFlow)
            .filter(AlipayFlow.account == account, AlipayFlow.balance.isnot(None))
            .order_by(AlipayFlow.transaction_time.asc(), AlipayFlow.id.asc())
            .all()
        )
        if len(rows) < 2:
            continue  # 余额全为 NULL 或只有1条, 无法连续性核查, 跳过 (不误报)
        prev = None
        for r in rows:
            if prev is not None:
                expected = (prev.balance or Decimal("0")) + (r.amount or Decimal("0"))
                gap = (r.balance or Decimal("0")) - expected
                if abs(gap) > Decimal("1"):
                    _record(
                        db,
                        source_table="alipay_flows",
                        source_pk=r.id,
                        exception_type="alipay_balance_gap",
                        severity="warning",
                        description=(
                            f"支付宝[{account}] 余额不连续: 上一条余额 ¥{prev.balance} + 本次 ¥{r.amount} "
                            f"= ¥{expected}, 实际余额 ¥{r.balance}, 差 ¥{gap}。可能有流水缺失。"
                        ),
                        suggestion_action="检查该账户该时间段是否有遗漏的流水未导入。",
                        context={"account": account, "gap": str(gap), "txn_no": r.transaction_no},
                    )
                    count += 1
            prev = r
    _log.info("scan_alipay_balance_gap: %d gaps", count)
    return count


# ---------------------------------------------------------------------------
# B13 — 木材损耗率异常偏高
# ---------------------------------------------------------------------------

def scan_wood_loss_high(db: Session) -> int:
    """木材损耗率 > 15% 报 error, 10%~15% 报 warning, 提示异常损耗/材料浪费。"""
    from decimal import Decimal
    threshold = Decimal(settings_service.get(db, "wood_loss_warn_pct") or "15")
    count = 0
    for r in db.query(WoodLoss).filter(WoodLoss.loss_rate_pct.isnot(None)).all():
        rate = Decimal(r.loss_rate_pct or 0)
        if rate <= threshold * Decimal("0.67"):
            continue
        sev = "error" if rate > threshold else "warning"
        _record(
            db,
            source_table="wood_losses",
            source_pk=r.id,
            exception_type="wood_loss_high",
            severity=sev,
            description=f"木材损耗 [{r.wood_type or '?'} {r.spec or ''}] 损耗率 {rate}% 偏高 (阈值 {threshold}%)。",
            suggestion_action="复核下料工艺/材料质量, 必要时调整 BOM 损耗系数。",
            context={"id": r.id, "loss_rate_pct": str(rate), "wood_type": r.wood_type},
        )
        count += 1
    _log.info("scan_wood_loss_high: %d", count)
    return count


# ---------------------------------------------------------------------------
# B14 — 样品缺成本 / 报损未记成本
# ---------------------------------------------------------------------------

def scan_sample_missing_cost(db: Session) -> int:
    """样品 cost 为空 → 影响样品资产/费用核算; 报损样品更需记成本。"""
    count = 0
    for r in db.query(Sample).filter(Sample.cost.is_(None)).all():
        is_scrap = (r.status or "") in ("报损", "报废")
        _record(
            db,
            source_table="samples",
            source_pk=r.id,
            exception_type="sample_missing_cost",
            severity="warning" if is_scrap else "info",
            description=f"样品 {r.sample_no} ({r.product_name or '?'}) 未填成本"
                        + ("，且已报损/报废，需计入费用。" if is_scrap else "。"),
            suggestion_action="补填样品制作成本。",
            context={"id": r.id, "sample_no": r.sample_no, "status": r.status},
        )
        count += 1
    _log.info("scan_sample_missing_cost: %d", count)
    return count


# ---------------------------------------------------------------------------
# B15 — 已发货订单无对应工厂单 (工厂单覆盖率)
# ---------------------------------------------------------------------------

def scan_factory_order_uncovered(db: Session) -> int:
    """状态为 shipped/signed 且有 actual_cost/theoretical_cost 的订单,
    若无任何有效 (未作废) 工厂单 → 报 info 级提示。

    仅对有成本的订单报告 (成本已录入说明有采购行为); 无成本的订单可能是库存直发或
    赠品, 不写异常, 避免误报。severity=info 确保不拉低健康分。
    """
    covered = {
        no for (no,) in db.query(FactoryOrder.platform_order_no)
        .filter(FactoryOrder.platform_order_no.isnot(None), FactoryOrder.voided_at.is_(None))
        .distinct().all()
    }
    count = 0
    for o in db.query(Order).filter(
        Order.status.in_(["shipped", "signed"]),
        Order.is_historical == False,  # noqa: E712
        # 只报有成本记录的订单; 无成本大概率库存直发
        (Order.theoretical_cost.isnot(None)) | (Order.actual_cost.isnot(None)),
    ).all():
        if o.order_no not in covered:
            _record(
                db,
                source_table="orders",
                source_pk=o.id,
                exception_type="factory_order_uncovered",
                severity="info",
                description=f"订单 {o.order_no} 已{o.status}, 有成本但无工厂下单记录。"
                            "如为库存直发可忽略; 如为工厂生产请补录工厂单。",
                suggestion_action="确认是否库存直发; 如非库存直发请在工厂下单页补录。",
                context={"order_no": o.order_no, "status": o.status},
            )
            count += 1
    _log.info("scan_factory_order_uncovered: %d", count)
    return count


# ---------------------------------------------------------------------------
# B16 — 推广充值记录缺支付宝流水号
# ---------------------------------------------------------------------------

def scan_promotion_recharge_unmatched(db: Session) -> int:
    """推广 '充值' 记录缺 alipay_flow_no → 无法与支付宝充值支出核对。"""
    count = 0
    for r in db.query(PromotionFlow).filter(
        PromotionFlow.flow_type == "充值",
        (PromotionFlow.alipay_flow_no.is_(None)) | (PromotionFlow.alipay_flow_no == ""),
    ).all():
        _record(
            db,
            source_table="promotion_flows",
            source_pk=r.id,
            exception_type="promotion_recharge_unmatched",
            severity="warning",
            description=f"推广充值记录 {r.id} (¥{r.amount}, {r.transaction_date}) 缺支付宝流水号, 无法核对充值出账。",
            suggestion_action="补填该充值对应的支付宝流水号。",
            context={"id": r.id, "amount": str(r.amount), "date": str(r.transaction_date)},
        )
        count += 1
    _log.info("scan_promotion_recharge_unmatched: %d", count)
    return count


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
        ("alipay_balance_gap", scan_alipay_balance_gap),
        ("wood_loss_high", scan_wood_loss_high),
        ("sample_missing_cost", scan_sample_missing_cost),
        ("factory_order_uncovered", scan_factory_order_uncovered),
        ("promotion_recharge_unmatched", scan_promotion_recharge_unmatched),
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
