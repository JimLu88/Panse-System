import csv
import io
from datetime import date as _date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.database import get_db
from app.models.knowledge import AiKnowledge
from app.models.marketing import OutsourcingExpense, PromotionFlow
from app.models.order import Order
from app.services import asset_service, data_freshness_service, health_report, sales_analytics, sales_rollup_service

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _range_for(period: str) -> tuple[_date, _date]:
    today = _date.today()
    if period == "7d":
        return today - timedelta(days=7), today
    if period == "30d":
        return today - timedelta(days=30), today
    if period == "month":
        return today.replace(day=1), today
    if period == "year":
        return today.replace(month=1, day=1), today
    raise HTTPException(400, f"未知 period: {period} (允许 7d/30d/month/year)")


class HealthReportOut(BaseModel):
    period_start: str
    period_end: str
    exceptions: dict[str, Any]
    reconciliation: dict[str, Any]
    inventory: dict[str, Any]
    orders: dict[str, Any]
    roi: dict[str, Any]
    integrity_score: int
    headlines: list[str]


@router.get("/monthly", response_model=HealthReportOut)
def monthly_health(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    try:
        r = health_report.generate(db, year, month)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return HealthReportOut(**health_report.to_dict(r))


@router.get("/monthly/current", response_model=HealthReportOut)
def current_month(db: Session = Depends(get_db)):
    now = datetime.now()
    return monthly_health(year=now.year, month=now.month, db=db)


class KnowledgeOut(BaseModel):
    id: int
    exception_type: str
    context_hash: str
    solution_text: str
    source_description: str | None
    model: str | None
    usage_count: int
    last_used_at: str | None
    created_at: str


# ----------------------------- Phase 4: 销售报表 ---------------------- #


class SalesSummaryOut(BaseModel):
    period_start: str
    period_end: str
    order_count: int
    revenue: float
    cost: float
    gross_profit: float
    net_profit: float
    top_products_by_profit: list[dict]
    top_products_by_profit_rate: list[dict]


def _dec(d) -> float:
    return float(Decimal(d or 0)) if d is not None else 0.0


@router.get("/sales/summary", response_model=SalesSummaryOut)
def sales_summary(
    period: str = Query("30d", description="7d / 30d / month / year"),
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """业务需求 15: 店铺销售汇总 (销售额/成本/毛利/净利 + 利润排行 Top 10)."""
    start, end = _range_for(period)
    s = sales_analytics.summary(db, start=start, end=end, platform=platform)
    def _ser(rows):
        return [
            {k: (_dec(v) if isinstance(v, Decimal) else v) for k, v in r.items()}
            for r in rows
        ]
    return SalesSummaryOut(
        period_start=s.period_start.isoformat(), period_end=s.period_end.isoformat(),
        order_count=s.order_count,
        revenue=_dec(s.revenue), cost=_dec(s.cost),
        gross_profit=_dec(s.gross_profit), net_profit=_dec(s.net_profit),
        top_products_by_profit=_ser(s.top_products_by_profit),
        top_products_by_profit_rate=_ser(s.top_products_by_profit_rate),
    )


@router.get("/sales/breakdown")
def sales_breakdown(
    period: str = Query("30d"),
    db: Session = Depends(get_db),
):
    """业务需求 16: 分产品 SKU 销售明细."""
    start, end = _range_for(period)
    rows = sales_analytics.product_breakdown(db, start=start, end=end)
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            d[k] = _dec(v) if isinstance(v, Decimal) else v
        out.append(d)
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "rows": out}


# ----------------------------- Phase 12: CSV 导出 -------------------- #


@router.get("/sales/breakdown.csv")
def export_sales_breakdown(
    period: str = Query("30d"),
    db: Session = Depends(get_db),
):
    """业务需求扩展: 分产品销售导出 CSV. 老板转发到企业微信群用."""
    start, end = _range_for(period)
    rows = sales_analytics.product_breakdown(db, start=start, end=end)
    buf = io.StringIO()
    buf.write("﻿")   # BOM, Excel 中文不乱码
    writer = csv.writer(buf)
    writer.writerow([
        "产品编码", "产品名", "SKU 编码", "SKU 名", "件数",
        "销售额", "成本", "毛利", "净利",
        "毛利率 %", "净利率 %",
    ])
    for r in rows:
        revenue = float(r.get("revenue") or 0)
        cost = float(r.get("cost") or 0)
        net = float(r.get("net_profit") or 0)
        writer.writerow([
            r.get("product_code") or "", r.get("product_name") or "",
            r.get("sku_code") or "", r.get("sku") or "",
            r.get("qty") or 0,
            f"{revenue:.2f}", f"{cost:.2f}",
            f"{revenue - cost:.2f}", f"{net:.2f}",
            f"{float(r.get('gross_profit_rate') or 0) * 100:.1f}",
            f"{float(r.get('net_profit_rate') or 0) * 100:.1f}",
        ])
    csv_data = buf.getvalue()
    fname = f"sales_{start.isoformat()}_{end.isoformat()}.csv"
    return StreamingResponse(
        iter([csv_data.encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.get("/sales/summary.csv")
def export_sales_summary(
    period: str = Query("30d"),
    db: Session = Depends(get_db),
):
    """导出汇总 + Top 10 利润排行."""
    start, end = _range_for(period)
    s = sales_analytics.summary(db, start=start, end=end)
    buf = io.StringIO()
    buf.write("﻿")
    writer = csv.writer(buf)
    writer.writerow(["项目", "数值"])
    writer.writerow(["周期", f"{start.isoformat()} ~ {end.isoformat()}"])
    writer.writerow(["订单数", s.order_count])
    writer.writerow(["销售额", float(s.revenue)])
    writer.writerow(["成本", float(s.cost)])
    writer.writerow(["毛利", float(s.gross_profit)])
    writer.writerow(["净利", float(s.net_profit)])
    writer.writerow([])
    writer.writerow(["Top 10 利润排行"])
    writer.writerow(["产品编码", "产品名", "订单数", "净利"])
    for r in s.top_products_by_profit:
        writer.writerow([
            r.get("product_code") or "", r.get("product_name") or "",
            r.get("order_count") or 0,
            f"{float(r.get('net_profit') or 0):.2f}",
        ])
    fname = f"summary_{start.isoformat()}_{end.isoformat()}.csv"
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.get("/forecast/30d")
def forecast_30d(db: Session = Depends(get_db)):
    """业务需求 7: 未来 30 天 SKU 销量预测 (移动平均 + 20% 安全系数)."""
    return {"forecast": sales_analytics.forecast_30d(db)}


@router.get("/stock-advice")
def stock_advice(db: Session = Depends(get_db)):
    """业务需求 8: 智能提前备货建议."""
    return sales_analytics.stock_advice(db)


@router.get("/slow-moving")
def slow_moving(
    long_no_sale_days: int = 60,
    overstock_ratio: float = 3.0,
    db: Session = Depends(get_db),
):
    """业务需求 8: 滞销分类 (长期未售 / 超大库存)."""
    return sales_analytics.slow_moving_split(
        db, long_no_sale_days=long_no_sale_days, overstock_ratio=overstock_ratio,
    )


# ----------------------------- 经营状况分析 (功能 5: 收支占比) -------- #


@router.get("/operating-analysis")
def operating_analysis(
    period: str = Query("30d", description="7d / 30d / month / year"),
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """业务需求扩展 (功能 5): 7天/月度/年度经营状况分析 — 各项收支占比.

    收入侧: 销售额。支出侧: 成本 / 运费等订单费用 / 推广 / 人员外包。
    每项给出金额与占销售额的百分比, 末了给净利与净利率。
    """
    start, end = _range_for(period)
    s = sales_analytics.summary(db, start=start, end=end, platform=platform)
    revenue = Decimal(s.revenue or 0)

    # 订单级费用 (运费/上楼/安装/赔付) — 从订单聚合
    fee_q = select(
        func.coalesce(func.sum(Order.actual_freight), 0),
        func.coalesce(func.sum(Order.upstairs_fee), 0),
        func.coalesce(func.sum(Order.install_fee), 0),
        func.coalesce(func.sum(Order.compensation_fee), 0),
        func.coalesce(func.sum(Order.platform_fee), 0),
    ).where(
        Order.order_date >= start, Order.order_date <= end,
        Order.is_historical == False,  # noqa: E712
    )
    if platform:
        fee_q = fee_q.where(Order.platform == platform)
    freight, upstairs, install, comp, platform_fee = db.execute(fee_q).one()

    # 推广支出 (PromotionFlow 支出) 与 人员外包
    promo = db.execute(
        select(func.coalesce(func.sum(PromotionFlow.amount), 0)).where(
            PromotionFlow.flow_type == "支出",
            PromotionFlow.transaction_date >= start,
            PromotionFlow.transaction_date <= end,
        )
    ).scalar() or 0
    personnel = db.execute(
        select(func.coalesce(func.sum(OutsourcingExpense.amount), 0)).where(
            OutsourcingExpense.payment_date >= start,
            OutsourcingExpense.payment_date <= end,
        )
    ).scalar() or 0

    def _pct(x) -> float:
        return float(Decimal(x or 0) / revenue * 100) if revenue else 0.0

    items = [
        {"name": "商品成本", "amount": _dec(s.cost), "pct": _pct(s.cost)},
        {"name": "运费", "amount": _dec(freight), "pct": _pct(freight)},
        {"name": "上楼费", "amount": _dec(upstairs), "pct": _pct(upstairs)},
        {"name": "安装费", "amount": _dec(install), "pct": _pct(install)},
        {"name": "赔付费", "amount": _dec(comp), "pct": _pct(comp)},
        {"name": "平台费", "amount": _dec(platform_fee), "pct": _pct(platform_fee)},
        {"name": "推广费", "amount": _dec(promo), "pct": _pct(promo)},
        {"name": "人员外包", "amount": _dec(personnel), "pct": _pct(personnel)},
    ]
    total_expense = sum(Decimal(str(i["amount"])) for i in items)
    net = revenue - total_expense
    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "revenue": _dec(revenue),
        "expense_items": items,
        "total_expense": _dec(total_expense),
        "net_profit": _dec(net),
        "net_profit_rate": float(net / revenue * 100) if revenue else 0.0,
    }


# ----------------------------- 销售汇总 (rollup 加速) ----------------- #


@router.get("/sales/rollup-summary")
def sales_rollup_summary(
    period: str = Query("30d"),
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """从 sales_daily_rollup 预聚合表快速取总览 (大数据量时比实时 SUM 快)。

    rollup 未覆盖该区间时返回 {} (前端回退到 /sales/summary 实时算)。
    """
    start, end = _range_for(period)
    return sales_rollup_service.query_summary(db, start=start, end=end, platform=platform)


@router.post("/sales/rollup-backfill")
def sales_rollup_backfill(
    period: str = Query("30d"),
    db: Session = Depends(get_db),
):
    """补算一段时间的销售日汇总 (历史回填 / rollup 缺口修补)。"""
    start, end = _range_for(period)
    r = sales_rollup_service.rollup_range(db, start, end)
    db.commit()
    return r


# ----------------------------- Phase 4: 资产 / 经营分析 -------------- #


class AssetCategoryOut(BaseModel):
    name: str
    amount: float
    detail: list[dict] = []


class AssetSummaryOut(BaseModel):
    total: float
    categories: list[AssetCategoryOut]
    formula_a: float
    formula_b: float
    diff: float
    breakdown: dict[str, float] = {}


@router.get("/assets", response_model=AssetSummaryOut)
def assets_summary(db: Session = Depends(get_db)):
    """业务需求 14: 资产总额 + 饼图分类; 业务需求 19: 公式 A/B 对比 + 逐项拆解."""
    s = asset_service.summary(db)
    return AssetSummaryOut(
        total=_dec(s.total),
        categories=[
            AssetCategoryOut(name=c.name, amount=_dec(c.amount), detail=c.detail)
            for c in s.categories
        ],
        formula_a=_dec(s.formula_a),
        formula_b=_dec(s.formula_b),
        diff=_dec(s.diff),
        breakdown=s.breakdown,
    )


@router.get("/data-freshness")
def data_freshness(db: Session = Depends(get_db)):
    """各数据源新鲜度状态: 距今天数 / 是否过期 / 提醒文字。前端可用于显示"待补录"小红点。"""
    items = data_freshness_service.check_all(db)
    return [
        {
            "source": i.source,
            "last_date": i.last_date.isoformat() if i.last_date else None,
            "days_stale": i.days_stale,
            "threshold_days": i.threshold_days,
            "overdue": i.overdue,
            "message": i.message,
        }
        for i in items
    ]


@router.post("/data-freshness/remind-now")
def remind_now(db: Session = Depends(get_db)):
    """手动触发一次新鲜度检查 + 推送 (无需等调度器)。"""
    return data_freshness_service.check_and_remind(db)


@router.get("/unmatched-flows")
def unmatched_flows(days: int = 7, db: Session = Depends(get_db)):
    """业务需求 19: 未核销异常池 — 最近 7 天 open 状态流水."""
    return {"days": days, "rows": asset_service.unmatched_recent_flows(db, days=days)}


@router.get("/unmatched-flows/classify")
def classify_unmatched(days: int = 7, limit: int = 50,
                       db: Session = Depends(get_db)):
    """Phase 11 P4-24: AI 辅助归类未核销流水."""
    from app.services import flow_classification_service
    return {"results": flow_classification_service.batch_classify(
        db, days=days, limit=limit,
    )}


@router.get("/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(limit: int = Query(50, le=200), db: Session = Depends(get_db)):
    """AI 知识库内容 (plan §12.2 常见问题库)."""
    rows = db.execute(
        select(AiKnowledge).order_by(AiKnowledge.usage_count.desc()).limit(limit)
    ).scalars().all()
    return [
        KnowledgeOut(
            id=r.id, exception_type=r.exception_type, context_hash=r.context_hash,
            solution_text=r.solution_text, source_description=r.source_description,
            model=r.model, usage_count=r.usage_count,
            last_used_at=r.last_used_at.isoformat() if r.last_used_at else None,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
