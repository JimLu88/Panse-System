import csv
import io
from datetime import date as _date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import not_, or_, select
from sqlalchemy.orm import Session

from sqlalchemy import func

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.finance import AlipayFlow
from app.models.knowledge import AiKnowledge
from app.models.marketing import AfterSales, OutsourcingExpense, PromotionFlow
from app.models.order import FactoryOrder, Order
from app.services import asset_service, dashboard_monthly_service, data_freshness_service, health_report, sales_analytics, sales_rollup_service, shop_report_service

# 路由级守卫: 报表含财务数据, 统一要求登录 (此前多数端点无守卫, 外网下可被未登录读取)。
router = APIRouter(
    prefix="/api/reports",
    tags=["reports"],
    dependencies=[Depends(require_role("admin", "operator", "viewer"))],
)


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
    if period == "last_month":  # 上个月整月
        prev_last = today.replace(day=1) - timedelta(days=1)
        return prev_last.replace(day=1), prev_last
    # 具体月份 YYYY-MM (用户拍板 2026-06-17: 时间筛选支持按月下拉)
    import re
    from calendar import monthrange
    mm = re.fullmatch(r"(\d{4})-(\d{1,2})", period or "")
    if mm:
        y, mo = int(mm.group(1)), int(mm.group(2))
        if 1 <= mo <= 12:
            return _date(y, mo, 1), _date(y, mo, monthrange(y, mo)[1])
    raise HTTPException(400, f"未知 period: {period} (允许 7d/30d/month/year/last_month/YYYY-MM)")


class ShopStatOut(BaseModel):
    shop: str
    order_count: int
    total_qty: int
    total_revenue: float


@router.get("/shops", response_model=list[ShopStatOut])
def shop_stats(
    period: Optional[str] = Query(None, description="7d/30d/month/year; 不填=全部"),
    db: Session = Depends(get_db),
):
    """分店统计: 各店铺 单数/销量/销售额 (按销售额降序, 未归属单独成桶)。"""
    start = end = None
    if period:
        start, end = _range_for(period)
    return shop_report_service.compute_shop_stats(db, start=start, end=end)


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


@router.get("/monthly-financial")
def monthly_financial(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    fmt: str = Query("json", pattern="^(json|xlsx)$"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """月度财务报表 (优化 #10): 营收/成本/毛利/净利/订单数; fmt=xlsx 下载 Excel。"""
    from app.services import financial_report_service
    s = financial_report_service.monthly_summary(db, year, month)
    if fmt == "xlsx":
        data = financial_report_service.build_excel(s)
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="financial-{s["period"]}.xlsx"'},
        )
    return s


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
    bottom_products_by_profit: list[dict] = []


def _dec(d) -> float:
    return float(Decimal(d or 0)) if d is not None else 0.0


@router.get("/sales/summary", response_model=SalesSummaryOut)
def sales_summary(
    period: str = Query("30d", description="7d / 30d / month / year"),
    platform: Optional[str] = None,
    brand: Optional[str] = Query(None, description="PS / PFG 品牌过滤 (Plan F8)"),
    db: Session = Depends(get_db),
):
    """业务需求 15: 店铺销售汇总 (销售额/成本/毛利/净利 + 利润排行 Top 10)."""
    start, end = _range_for(period)
    s = sales_analytics.summary(db, start=start, end=end, platform=platform, brand=brand)
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
        bottom_products_by_profit=_ser(s.bottom_products_by_profit),
    )


@router.get("/sales/breakdown")
def sales_breakdown(
    period: str = Query("30d"),
    brand: Optional[str] = Query(None, description="PS / PFG 品牌过滤 (Plan F8)"),
    db: Session = Depends(get_db),
):
    """业务需求 16: 分产品 SKU 销售明细."""
    start, end = _range_for(period)
    rows = sales_analytics.product_breakdown(db, start=start, end=end, brand=brand)
    out = []
    for r in rows:
        d = {}
        for k, v in r.items():
            d[k] = _dec(v) if isinstance(v, Decimal) else v
        out.append(d)
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "rows": out}


@router.get("/sales/cost-anomaly")
def sales_cost_anomaly(
    period: str = Query("year"),
    product_code: Optional[str] = Query(None, description="只看某产品 (如 PPS24210070901)"),
    db: Session = Depends(get_db),
):
    """诊断销售汇总成本异常: 找『实付小却背全额成本』的错配单(成本>实付), 量化它们把总成本拉高多少。
    用于解释为何某产品利润率偏低 / 总利润为负。"""
    from app.models.order import Order as _O
    start, end = _range_for(period)
    q = select(_O).where(_O.order_date >= start, _O.order_date <= end,
                         _O.status.in_(("paid", "shipped", "signed")))
    if product_code:
        q = q.where(_O.product_code == product_code)
    orders = db.execute(q).scalars().all()
    tot_rev = tot_cost = Decimal("0")
    mism = []
    for o in orders:
        rev = Decimal(o.paid_amount or 0)
        cost = Decimal(o.actual_cost if o.actual_cost is not None else (o.theoretical_cost or 0))
        tot_rev += rev
        tot_cost += cost
        if cost > rev:
            mism.append({
                "order_no": o.order_no, "product_code": o.product_code,
                "paid": float(rev), "cost": float(cost), "excess": float(cost - rev),
                "cost_src": "actual" if o.actual_cost is not None else "theoretical",
                "status": o.status,
            })
    mism.sort(key=lambda r: r["excess"], reverse=True)
    return {
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "orders": len(orders), "total_revenue": float(tot_rev), "total_cost": float(tot_cost),
        "cost_minus_revenue": float(tot_cost - tot_rev),
        "mismatched_count": len(mism),
        "mismatched_excess_total": round(sum(m["excess"] for m in mism), 2),
        "samples": mism[:40],
    }


@router.get("/sales/ranking")
def sales_ranking(
    granularity: str = Query("month", description="month(按月) / year(按年)"),
    metric: str = Query("revenue", description="revenue(销售额) / qty(销量)"),
    period: Optional[str] = Query(None, description="指定周期 2026-04 / 2026; 缺省取最新"),
    limit: int = Query(30, le=100),
    db: Session = Depends(get_db),
):
    """销售排行榜: 按月/年 分产品 销量/销售额 排行 + 每期冠军时间线 (正式销售, 不含补单)."""
    return sales_analytics.product_ranking(
        db, granularity=granularity, metric=metric, period=period, limit=limit,
    )


@router.get("/monthly-pnl")
def monthly_pnl(db: Session = Depends(get_db)):
    """数据大盘·月度经营: 工厂口径利润 + 对账完成度(accurate/reference_only) + 推广ROI + 累计投资回收率, 逐月。"""
    return dashboard_monthly_service.monthly_pnl(db)


@router.get("/sales-mix")
def sales_mix(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    by: str = Query("product", description="product(产品) / shop(店铺)"),
    db: Session = Depends(get_db),
):
    """某月销售占比(饼图数据): 正式销售按 产品/店铺 维度, 剔除补差价邮费专链。"""
    return dashboard_monthly_service.sales_mix(db, year=year, month=month, by=by)


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
    # 统一会计 P&L (用户拍板 2026-06-18: 与月度经营/逐单核对/大盘 完全同口径)。platform 过滤已并入整体口径。
    from app.services import order_financials as _ofin
    s = _ofin.accounting_summary(db, start, end)
    revenue = s["revenue"]

    def _pct(x) -> float:
        return float(Decimal(x or 0) / revenue * 100) if revenue else 0.0

    items = [
        {"name": "物理产品成本", "amount": _dec(s["goods"]), "pct": _pct(s["goods"])},
        {"name": "物流费", "amount": _dec(s["freight"]), "pct": _pct(s["freight"])},
        {"name": "安装/上楼", "amount": _dec(s["install"]), "pct": _pct(s["install"])},
        {"name": "平台扣点(实付−实收)", "amount": _dec(s["platform"]), "pct": _pct(s["platform"])},
        {"name": "税费", "amount": _dec(s["tax"]), "pct": _pct(s["tax"])},
        {"name": "额外售后(退款外)", "amount": _dec(s["aftersales"]), "pct": _pct(s["aftersales"])},
        {"name": "推广费", "amount": _dec(s["promo"]), "pct": _pct(s["promo"])},
        {"name": "人员外包", "amount": _dec(s["outsourcing"]), "pct": _pct(s["outsourcing"])},
        {"name": "固定成本(房租等)", "amount": _dec(s["fixed"]), "pct": _pct(s["fixed"])},
        {"name": "补单(刷单)成本", "amount": _dec(s["refill"]["total"]), "pct": _pct(s["refill"]["total"])},
    ]
    net = s["net"]
    return {
        "period": period,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "revenue": _dec(revenue),
        "expense_items": items,
        "total_expense": _dec(s["total_cost"]),
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


@router.get("/assets/diff-drilldown")
def assets_diff_drilldown(db: Session = Depends(get_db)):
    """Plan L6: 两口径资金差额下钻 — 每个科目的构成明细 TopN."""
    return asset_service.diff_drilldown(db)


@router.get("/sales-by-brand")
def sales_by_brand(
    period: str = Query("30d", description="7d / 30d / month / year"),
    db: Session = Depends(get_db),
):
    """Plan F8: 按品牌 (PS 畔色 / PFG 孚格) 汇总销售额/净利/单量."""
    start, end = _range_for(period)
    out = []
    for b in ("PS", "PFG"):
        s = sales_analytics.summary(db, start=start, end=end, brand=b)
        out.append({
            "brand": b, "order_count": s.order_count,
            "revenue": _dec(s.revenue), "net_profit": _dec(s.net_profit),
        })
    return {"period": period, "brands": out}


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


# ─── 月度经营数据表格 ─────────────────────────────────────────────────────────


def _month_range(year: int, month: int) -> tuple[_date, _date]:
    start = _date(year, month, 1)
    if month == 12:
        end = _date(year, 12, 31)
    else:
        end = _date(year, month + 1, 1) - timedelta(days=1)
    return start, end


def _business_month(db: Session, year: int, month: int) -> dict:
    """单月经营数据汇总。"""
    start, end = _month_range(year, month)

    def _qs(col, *wheres):
        stmt = select(func.coalesce(func.sum(col), 0))
        for w in wheres:
            stmt = stmt.where(w)
        return float(db.execute(stmt).scalar() or 0)

    def _qc(*wheres):
        stmt = select(func.count(Order.id))
        for w in wheres:
            stmt = stmt.where(w)
        return int(db.execute(stmt).scalar() or 0)

    # 不再按 is_historical 过滤: 日常导入的订单大多被标了 is_historical=True, 若过滤会把绝大多数
    # 真实订单漏掉(出现"某月 0 单"的怪数)。报表本就从 2026 起, 直接按下单日统计。
    # 真实订单口径(用户拍板 2026-06-17, 全系统统一): 只算"已付款且成交"——排除 待付款/取消/关闭
    # 与 全额退款单(原来只排取消, 把 90 个待付款也算进 1 月营收 → 净利率虚高到 73%)。
    from app.services.sales_analytics import settled_sale_clause
    _settled = settled_sale_clause()
    base = [Order.order_date >= start, Order.order_date <= end]
    real = [*base, _settled, Order.is_refill == False]  # noqa: E712

    from app.services import order_financials as _ofin
    # 统一会计 P&L (用户拍板 2026-06-18: 全系统同口径; 月度/经营状况/逐单/大盘 都走 accounting_summary)
    s = _ofin.accounting_summary(db, start, end)
    real_count = _qc(*real)
    real_revenue = round(float(s["revenue"]), 2)
    refill_count = _qc(*base, Order.is_refill == True)  # noqa: E712
    refill_revenue = _qs(Order.paid_amount, *base, Order.is_refill == True)  # noqa: E712

    factory_bill = float(db.execute(
        select(func.coalesce(func.sum(FactoryOrder.factory_bill_amount), 0)).where(
            FactoryOrder.order_date >= start,
            FactoryOrder.order_date <= end,
            FactoryOrder.voided_at.is_(None),
        )
    ).scalar() or 0)

    cogs = float(s["goods"]); cogs_estimated = bool(s["goods_estimated"])
    freight = float(s["freight"]); install_upstairs = float(s["install"])
    platform_ded = float(s["platform"])          # 平台扣点(实付−实收, 含平台优惠券)
    tax_expense = float(s["tax"])
    aftersales_comp = float(s["aftersales"]); aftersales_count = int(s["aftersales_count"])
    promo = float(s["promo"])
    outsourcing = float(s["outsourcing"]); outsourcing_estimated = bool(s["outsourcing_estimated"])
    fixed_costs = float(s["fixed"])
    refill_cost = float(s["refill"]["total"])
    total_expense = float(s["total_cost"])
    net_profit = float(s["net"])

    total_revenue = real_revenue + refill_revenue   # 总流水 (含补单, 仅参考)
    total_orders = real_count + refill_count
    refill_ratio = round(refill_count / total_orders * 100, 1) if total_orders else 0.0
    refill_cost_ratio = round(refill_revenue / total_revenue * 100, 1) if total_revenue else 0.0
    promo_ratio = round(promo / real_revenue * 100, 1) if real_revenue else 0.0
    aftersales_rate = round(aftersales_count / real_count * 100, 1) if real_count else 0.0
    lead_time_days = None
    lt_rows = db.execute(
        select(FactoryOrder.order_date, FactoryOrder.actual_delivery).where(
            FactoryOrder.order_date >= start,
            FactoryOrder.order_date <= end,
            FactoryOrder.actual_delivery.isnot(None),
            FactoryOrder.voided_at.is_(None),
        )
    ).all()
    if lt_rows:
        deltas = [(r.actual_delivery - r.order_date).days for r in lt_rows if r.actual_delivery >= r.order_date]
        if deltas:
            lead_time_days = round(sum(deltas) / len(deltas), 1)

    return {
        "year": year,
        "month": month,
        "period": f"{year}-{month:02d}",
        "real_order_count": real_count,
        "refill_order_count": refill_count,
        "real_revenue": round(real_revenue, 2),
        "refill_revenue": round(refill_revenue, 2),
        "total_revenue": round(total_revenue, 2),
        "refill_order_ratio": refill_ratio,
        "refill_cost_ratio": refill_cost_ratio,
        "promo_expense": round(promo, 2),
        "promo_ratio": promo_ratio,
        "factory_bill": round(factory_bill, 2),
        "effective_cost": round(cogs, 2),                        # 商品成本 (Σ逐单 actual/theoretical)
        "cogs_estimated": cogs_estimated,                        # 含推演(工厂未对账)
        "freight_expense": round(freight, 2),                    # 物流费
        "install_upstairs_expense": round(install_upstairs, 2),  # 安装上楼
        "platform_deduction": round(platform_ded, 2),           # 平台扣点 (实付−实收, 含优惠券)
        "tax_expense": round(tax_expense, 2),                    # 税费
        "aftersales_compensation": round(aftersales_comp, 2),    # 额外售后 (按订单归属, 退款不重复计)
        "aftersales_count": aftersales_count,
        "aftersales_rate": aftersales_rate,
        "outsourcing_expense": round(outsourcing, 2),
        "outsourcing_estimated": outsourcing_estimated,          # 5月起按¥10000/月预估
        "fixed_costs": round(fixed_costs, 2),                    # 固定成本/管理费用 (房租等)
        "refill_cost": round(refill_cost, 2),                    # 补单(刷单)纯成本
        "total_expense": round(total_expense, 2),
        "net_profit": round(net_profit, 2),
        "net_profit_rate": round(net_profit / real_revenue * 100, 1) if real_revenue else 0.0,
        "avg_lead_time_days": lead_time_days,
    }


@router.get("/business-monthly")
def business_monthly_table(
    from_year: int = Query(2026, description="起始年份"),
    from_month: int = Query(1, ge=1, le=12, description="起始月份"),
    to_year: Optional[int] = Query(None),
    to_month: Optional[int] = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """月度经营数据表格 — 从指定月份至今, 每月一行。

    返回字段:
      period, real_order_count, refill_order_count, real_revenue, refill_revenue,
      total_revenue, refill_order_ratio (%), refill_cost_ratio (%),
      promo_expense, promo_ratio (%), factory_bill,
      aftersales_compensation, aftersales_count, aftersales_rate (%),
      outsourcing_expense, platform_fee,
      total_expense, net_profit, net_profit_rate (%),
      avg_lead_time_days
    """
    today = _date.today()
    end_year = to_year or today.year
    end_month = to_month or today.month

    months = []
    y, m = from_year, from_month
    while (y, m) <= (end_year, end_month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1

    rows = [_business_month(db, y, m) for y, m in months]
    rows.reverse()  # 最新月在前

    # 汇总行
    def _sum(key):
        return round(sum(r[key] for r in rows if isinstance(r[key], (int, float))), 2)

    total_real = sum(r["real_order_count"] for r in rows)
    total_refill = sum(r["refill_order_count"] for r in rows)
    total_rev = _sum("total_revenue")
    total_refill_rev = _sum("refill_revenue")
    total_promo = _sum("promo_expense")
    total_exp = _sum("total_expense")
    total_net = _sum("net_profit")
    total_real_rev = _sum("real_revenue")

    def _any(key):
        return any(bool(r.get(key)) for r in rows)

    summary = {
        "period": "合计",
        "real_order_count": total_real,
        "refill_order_count": total_refill,
        "real_revenue": total_real_rev,
        "refill_revenue": total_refill_rev,
        "total_revenue": total_rev,
        "refill_order_ratio": round(total_refill / (total_real + total_refill) * 100, 1) if (total_real + total_refill) else 0.0,
        "refill_cost_ratio": round(total_refill_rev / total_rev * 100, 1) if total_rev else 0.0,
        "promo_expense": total_promo,
        "promo_ratio": round(total_promo / total_real_rev * 100, 1) if total_real_rev else 0.0,
        "factory_bill": _sum("factory_bill"),
        "effective_cost": _sum("effective_cost"),
        "cogs_estimated": _any("cogs_estimated"),
        "freight_expense": _sum("freight_expense"),
        "install_upstairs_expense": _sum("install_upstairs_expense"),
        "platform_deduction": _sum("platform_deduction"),
        "tax_expense": _sum("tax_expense"),
        "aftersales_compensation": _sum("aftersales_compensation"),
        "aftersales_count": sum(r["aftersales_count"] for r in rows),
        "aftersales_rate": round(sum(r["aftersales_count"] for r in rows) / total_real * 100, 1) if total_real else 0.0,
        "outsourcing_expense": _sum("outsourcing_expense"),
        "outsourcing_estimated": _any("outsourcing_estimated"),
        "fixed_costs": _sum("fixed_costs"),
        "refill_cost": _sum("refill_cost"),
        "total_expense": total_exp,
        "net_profit": total_net,
        "net_profit_rate": round(total_net / total_real_rev * 100, 1) if total_real_rev else 0.0,
        "avg_lead_time_days": None,
    }

    return {"rows": rows, "summary": summary}


@router.get("/per-order-reconcile")
def per_order_reconcile(
    year: int = Query(2026),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
):
    """逐单核对 (财务) — 某月每笔真实成交订单的完整成本拆解 + 支付宝覆盖/对账状态 + 问题单高亮。
    口径与经营状况一致 (order_financials 会计成本); 合计再减该月推广费、人员外包 = 本月真实净利。
    """
    start, end = _month_range(year, month)
    from app.services import order_financials as _ofin, sales_analytics
    coef = _ofin.load_coefficients(db)
    as_by_order = _ofin.extra_aftersales_by_order(db)

    orders = db.execute(
        select(Order).where(
            Order.order_date >= start, Order.order_date <= end,
            sales_analytics.settled_sale_clause(), Order.is_refill == False,  # noqa: E712
        )
    ).scalars().all()

    rows = []
    SUM_KEYS = ("paid_amount", "refund_amount", "revenue", "cost_goods", "cost_freight",
                "cost_install", "cost_platform", "cost_tax", "cost_aftersales", "cost_total", "net_profit")
    sums = {k: 0.0 for k in SUM_KEYS}
    for o in orders:
        paid = float(o.paid_amount or 0)
        refund = float(o.refund_amount or 0)
        revenue = paid - refund
        b = _ofin.cost_breakdown(o, coef, Decimal("0"))   # 售后改逐单口径, 不用人均均摊
        goods = float(b["physical"]); freight = float(b["freight"]); install = float(b["install_upstairs"])
        platform = float(b["platform"]); tax = float(b["tax"])
        aftersales = float(as_by_order.get(o.order_no, 0))
        cost_total = goods + freight + install + platform + tax + aftersales
        net = revenue - cost_total
        cost_estimated = o.actual_cost is None   # 用推演成本(未对账), 非工厂实报
        row = {
            "order_no": o.order_no,
            "product_name": o.product_name or o.product_code or "",
            "is_custom": bool(o.is_custom),
            "order_date": o.order_date.isoformat() if o.order_date else None,
            "paid_amount": round(paid, 2), "refund_amount": round(refund, 2), "revenue": round(revenue, 2),
            "cost_goods": round(goods, 2), "cost_freight": round(freight, 2), "cost_install": round(install, 2),
            "cost_platform": round(platform, 2), "cost_tax": round(tax, 2), "cost_aftersales": round(aftersales, 2),
            "cost_total": round(cost_total, 2), "net_profit": round(net, 2),
            "net_margin": round(net / revenue * 100, 1) if revenue else 0.0,
            "alipay_covered": o.alipay_flow_no is not None,    # 已配上支付宝到账流水
            "cost_reconciled": o.actual_cost is not None,      # 工厂成本已对账(非推演)
            "cost_estimated": cost_estimated,
            "is_loss": net < 0,
        }
        rows.append(row)
        for k in SUM_KEYS:
            sums[k] += row[k]

    promo = float(db.execute(
        select(func.coalesce(func.sum(PromotionFlow.amount), 0)).where(
            PromotionFlow.flow_type == "支出",
            PromotionFlow.transaction_date >= start, PromotionFlow.transaction_date <= end)
    ).scalar() or 0)
    _os, os_est = _ofin.outsourcing_for_range(db, start, end, coef)
    outsourcing = float(_os)
    fixed_costs = float(_ofin.fixed_costs_monthly(db))            # 固定成本/管理费用 (房租等, 年度÷12)
    rc = _ofin.refill_cost(db, start, end, coef)                  # 补单=刷单 纯成本 (本金回流非收入)
    refill_c = float(rc["total"])
    # 本月真实净利 = 行净利合计 − 推广 − 人员 − 固定成本 − 补单成本(刷单是纯支出)
    period_net = sums["net_profit"] - promo - outsourcing - fixed_costs - refill_c

    # 问题单高亮: 亏损 / 支付宝未覆盖 (用推演是常态——未对账, 单列标蓝, 不算问题, 否则会全是问题)
    problem_count = sum(1 for r in rows if r["is_loss"] or not r["alipay_covered"])
    rows.sort(key=lambda r: (
        0 if r["is_loss"] else 1,
        0 if not r["alipay_covered"] else 1,
        -r["paid_amount"],
    ))

    return {
        "period": f"{year}-{month:02d}",
        "order_count": len(rows),
        "problem_count": problem_count,
        "loss_count": sum(1 for r in rows if r["is_loss"]),
        "uncovered_count": sum(1 for r in rows if not r["alipay_covered"]),
        "estimated_count": sum(1 for r in rows if r["cost_estimated"]),
        "rows": rows,
        "subtotal": {
            **{k: round(v, 2) for k, v in sums.items()},
            "promo_expense": round(promo, 2),
            "outsourcing_expense": round(outsourcing, 2),
            "outsourcing_estimated": os_est,
            "fixed_costs": round(fixed_costs, 2),
            "fixed_cost_items": _ofin.fixed_cost_items(db),
            "refill_count": rc["count"],
            "refill_gmv": float(rc["gmv"]),         # 刷单流水(本金, 来回滚抵销)
            "refill_cost": refill_c,                # 补单成本 = 平台扣点+税+运费+佣金
            "refill_platform": float(rc["platform"]),
            "refill_tax": float(rc["tax"]),
            "refill_commission": float(rc["commission"]),
            "period_net_profit": round(period_net, 2),
            "period_net_margin": round(period_net / sums["revenue"] * 100, 1) if sums["revenue"] else 0.0,
        },
    }
