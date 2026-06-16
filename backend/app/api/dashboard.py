"""Dashboard 聚合 API — 订单/库存/财务三大指标."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.order import Order
from app.models.inventory import PartInventory, ProductInventory
from app.models.finance import AlipayFlow
from app.models.marketing import AfterSales
from app.models.exception import DataException

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _safe_decimal(v) -> float:
    if v is None:
        return 0.0
    return float(Decimal(str(v)))


@router.get("")
def get_dashboard(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    today = date.today()
    last_30 = today - timedelta(days=30)
    last_7 = today - timedelta(days=7)

    # ── 订单概览 ──────────────────────────────────────────────
    status_counts = dict(
        db.query(Order.status, func.count(Order.id))
        .filter(Order.is_historical == False)  # noqa: E712
        .group_by(Order.status)
        .all()
    )

    # 近 30 天趋势 — 收入口径(#11 用户拍板): 订单买家实付 paid_amount, 排除 补单(is_refill) + 关闭单(cancelled);
    # 退款金额单独列(refund_30d), 不在此从收入里扣(净额由前端/财务页按需扣)。
    trend_rows = (
        db.query(Order.order_date, func.count(Order.id), func.sum(Order.paid_amount))
        .filter(Order.order_date >= last_30, Order.is_historical == False,  # noqa: E712
                Order.is_refill == False, Order.status != "cancelled")  # noqa: E712
        .group_by(Order.order_date)
        .order_by(Order.order_date)
        .all()
    )
    refund_30d = float(
        db.query(func.coalesce(func.sum(Order.refund_amount), 0))
        .filter(Order.order_date >= last_30, Order.is_historical == False)  # noqa: E712
        .scalar() or 0
    )
    refill_excluded_30d = float(
        db.query(func.coalesce(func.sum(Order.paid_amount), 0))
        .filter(Order.order_date >= last_30, Order.is_historical == False,  # noqa: E712
                Order.is_refill == True)  # noqa: E712
        .scalar() or 0
    )
    order_trend = [
        {"date": str(r[0]), "count": r[1], "revenue": _safe_decimal(r[2])}
        for r in trend_rows if r[0]
    ]

    total_orders_30d = sum(r["count"] for r in order_trend)
    total_revenue_30d = sum(r["revenue"] for r in order_trend)

    orders_7d = (
        db.query(func.count(Order.id))
        .filter(Order.order_date >= last_7, Order.is_historical == False)  # noqa: E712
        .scalar() or 0
    )

    # ── 库存运营 ──────────────────────────────────────────────
    part_total = db.query(func.count(PartInventory.id)).scalar() or 0
    part_negative = (
        db.query(func.count(PartInventory.id))
        .filter(PartInventory.physical_qty < 0)
        .scalar() or 0
    )

    prod_total = db.query(func.count(ProductInventory.id)).scalar() or 0
    prod_low = (
        db.query(func.count(ProductInventory.id))
        .filter(ProductInventory.physical_qty <= 5)
        .scalar() or 0
    )
    # 缺料 (低于安全库存) / 超卖 (锁定 > 实物)
    part_below_safety = (
        db.query(func.count(PartInventory.id))
        .filter(PartInventory.safety_stock.isnot(None),
                PartInventory.physical_qty < PartInventory.safety_stock)
        .scalar() or 0
    )
    part_oversold = (
        db.query(func.count(PartInventory.id))
        .filter(PartInventory.physical_qty < PartInventory.locked_qty)
        .scalar() or 0
    )

    # ── 财务概览 ──────────────────────────────────────────────
    revenue_30d_alipay = (
        db.query(func.sum(AlipayFlow.amount))
        .filter(
            AlipayFlow.amount > 0,
            AlipayFlow.transaction_time >= func.date(str(last_30)),
        )
        .scalar()
    )

    # 成本 & 毛利 (近30天; 实际成本优先, 缺则理论成本)
    eff_cost_expr = func.coalesce(Order.actual_cost, Order.theoretical_cost, 0)
    cost_agg = (
        db.query(
            func.coalesce(func.sum(Order.theoretical_cost), 0),
            func.coalesce(func.sum(Order.actual_cost), 0),
            func.coalesce(func.sum(eff_cost_expr), 0),
        )
        .filter(Order.order_date >= last_30, Order.is_historical == False)  # noqa: E712
        .one()
    )
    theoretical_cost_30d = _safe_decimal(cost_agg[0])
    actual_cost_30d = _safe_decimal(cost_agg[1])
    effective_cost_30d = _safe_decimal(cost_agg[2])
    gross_profit_30d = round(total_revenue_30d - effective_cost_30d, 2)
    gross_margin_rate = round(gross_profit_30d / total_revenue_30d, 4) if total_revenue_30d else 0.0

    # 对账未清 (reconciliation_diff 类未处理异常)
    recon_unresolved = (
        db.query(func.count(DataException.id))
        .filter(DataException.status == "open",
                DataException.exception_type == "reconciliation_diff")
        .scalar() or 0
    )

    # 售后 (笔数 + 平台内外售后总成本)
    aftersales_count = db.query(func.count(AfterSales.id)).scalar() or 0
    aftersales_cost_expr = (func.coalesce(AfterSales.in_platform_total, 0)
                            + func.coalesce(AfterSales.out_platform_total, 0))
    aftersales_cost = _safe_decimal(db.query(func.sum(aftersales_cost_expr)).scalar())

    # 未解决异常数
    open_exceptions = (
        db.query(func.count(DataException.id))
        .filter(DataException.status == "open")
        .scalar() or 0
    )

    # 健康度评分: 100 - open_exceptions * 2 (floor 0)
    health_score = max(0, min(100, 100 - open_exceptions * 2))

    # 对账规则逐条状态 (动态枚举 reconciliation_service.RULES, 新增规则自动出现)
    RULE_LABELS = {
        "factory_payment": "货款对账",
        "install_fee": "安装费",
        "promotion": "推广支出",
        "refill_compensation": "补单赔付",
        "inventory_value": "库存资产",
        "logistics_fee": "物流费",
        "revenue_alipay": "收入对账",
        "operating_expense": "经营支出",
        "purchase_payment": "采购付款",
    }
    recon_rules = []
    for rule_key in RULE_LABELS:
        rows = (
            db.query(DataException.severity, func.count(DataException.id))
            .filter(
                DataException.status == "open",
                DataException.exception_type == "reconciliation_diff",
                DataException.source_pk.like(f"{rule_key}:%"),
            )
            .group_by(DataException.severity)
            .all()
        )
        error_cnt = sum(cnt for sev, cnt in rows if sev == "error")
        warning_cnt = sum(cnt for sev, cnt in rows if sev == "warning")
        if error_cnt > 0:
            status = "error"
        elif warning_cnt > 0:
            status = "warning"
        else:
            status = "ok"
        recon_rules.append({
            "key": rule_key,
            "label": RULE_LABELS[rule_key],
            "status": status,
            "error": error_cnt,
            "warning": warning_cnt,
        })

    # 各异常类型 open 计数 (供健康雷达 + 月结面板)
    type_counts = dict(
        db.query(DataException.exception_type, func.count(DataException.id))
        .filter(DataException.status == "open")
        .group_by(DataException.exception_type)
        .all()
    )

    def _tc(*types: str) -> int:
        return sum(int(type_counts.get(t, 0)) for t in types)

    # 各异常类型严重度权重 (error=10, warning=5, info=1)
    # 用于雷达扣分, 用 severity 区分; 单维最多扣到底 (floor 0)
    sev_counts = dict(
        db.query(DataException.severity, func.count(DataException.id))
        .filter(DataException.status == "open")
        .group_by(DataException.severity)
        .all()
    )

    def _weighted_score(*types: str) -> int:
        """计算指定类型的异常加权扣分, 返回 [0,100] 的得分。
        error 扣 8 分/条, warning 扣 3 分/条, info 扣 0.5 分/条, 最多扣完 100 分。
        """
        from app.models.exception import DataException as _DE
        rows = (
            db.query(_DE.severity, func.count(_DE.id))
            .filter(_DE.status == "open", _DE.exception_type.in_(types))
            .group_by(_DE.severity)
            .all()
        )
        deduct = sum(
            cnt * (8 if sev == "error" else 3 if sev == "warning" else 0.5)
            for sev, cnt in rows
        )
        return max(0, round(100 - deduct))

    # 五维健康雷达 (每维 0~100, 按异常严重度加权)
    health_dimensions = [
        {"name": "订单完整", "score": _weighted_score(
            "order_missing_cost", "order_missing_alipay", "order_missing_tracking",
            "factory_order_uncovered", "refill_unmatched")},
        {"name": "财务对账", "score": _weighted_score("reconciliation_diff")},
        {"name": "库存健康", "score": max(0, 100 - part_negative * 20 - part_oversold * 10 - min(part_below_safety, 10) * 3)},
        {"name": "流水完整", "score": _weighted_score(
            "alipay_missing_txn", "alipay_balance_gap", "factory_recon_incomplete")},
        {"name": "经营营销", "score": _weighted_score(
            "outsourcing_missing", "wood_loss_high", "sample_missing_cost",
            "promotion_recharge_unmatched")},
    ]

    # 月结清单: 每条对账规则 + 本月关键数据是否到位
    cur_y, cur_m = today.year, today.month
    month_start = date(cur_y, cur_m, 1)

    def _exists(model, date_col) -> bool:
        return (db.query(func.count()).select_from(model)
                .filter(date_col >= month_start, date_col <= today).scalar() or 0) > 0

    from app.models.marketing import PromotionFlow as _PF
    from app.models.finance import LogisticsBill as _LB, WanshifuBill as _WB, AccountBalance as _AB
    monthly_close = []
    for r in recon_rules:
        monthly_close.append({
            "key": r["key"], "label": r["label"], "category": "对账",
            "done": r["status"] == "ok",
            "detail": "无差异" if r["status"] == "ok" else f"差异 错误{r['error']}/警告{r['warning']}",
        })
    data_items = [
        ("alipay", "本月支付宝流水", _exists(AlipayFlow, AlipayFlow.transaction_time)),
        ("promotion", "本月推广记录", _exists(_PF, _PF.transaction_date)),
        ("logistics_bill", "本月物流账单", _exists(_LB, _LB.bill_date)),
        ("wanshifu_bill", "本月万师傅账单", _exists(_WB, _WB.bill_date)),
        ("account_balance", "本月账户余额", (db.query(func.count(_AB.id)).filter(
            _AB.period_year == cur_y, _AB.period_month == cur_m).scalar() or 0) > 0),
    ]
    for key, label, ok in data_items:
        monthly_close.append({
            "key": key, "label": label, "category": "数据",
            "done": bool(ok), "detail": "已录入" if ok else "本月暂无数据",
        })

    return {
        "orders": {
            "status_counts": status_counts,
            "trend_30d": order_trend,
            "total_30d": total_orders_30d,
            "revenue_30d": total_revenue_30d,
            "refill_excluded_30d": refill_excluded_30d,   # 注释用: 有补单 ¥X 未计入
            "refund_30d": refund_30d,                     # #11 近30天退款额(单独列, 未从收入扣)
            "revenue_caliber": "买家实付(paid_amount)·不含补单/关闭单·退款另列",  # #11 口径说明
            "count_7d": orders_7d,
        },
        "inventory": {
            "part_total": part_total,
            "part_negative": part_negative,
            "part_below_safety": part_below_safety,
            "part_oversold": part_oversold,
            "product_total": prod_total,
            "product_low_stock": prod_low,
        },
        "finance": {
            "alipay_income_30d": _safe_decimal(revenue_30d_alipay),
            "order_revenue_30d": total_revenue_30d,
            "theoretical_cost_30d": theoretical_cost_30d,
            "actual_cost_30d": actual_cost_30d,
            "gross_profit_30d": gross_profit_30d,
            "gross_margin_rate": gross_margin_rate,
            "reconciliation_unresolved": recon_unresolved,
            "aftersales_count": aftersales_count,
            "aftersales_cost": aftersales_cost,
        },
        "health": {
            "open_exceptions": open_exceptions,
            "health_score": health_score,
            "health_note": "健康度 = 100 − 未解决异常数×2 (满分100; 异常越多越低, ≥50条即0%)",  # #9 口径
        },
        "recon_rules": recon_rules,
        "health_dimensions": health_dimensions,
        "monthly_close": monthly_close,
    }
