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
    s = sales_analytics.summary(db, start=start, end=end, platform=platform)
    revenue = Decimal(s.revenue or 0)

    # 订单级会计成本逐项 (统一口径 order_financials: 物理/物流/安装上楼/售后/平台扣点/税) —
    # 与销售汇总同源, 避免双算 (用户拍板 2026-06-17)
    from app.services import order_financials as _ofin
    _coef = _ofin.load_coefficients(db)
    _as_avg = _ofin.aftersales_avg(db)
    _oq = select(Order).where(
        Order.order_date >= start, Order.order_date <= end,
        sales_analytics.settled_sale_clause(), Order.is_refill == False,  # noqa: E712
    )
    if platform:
        _oq = _oq.where(Order.platform == platform)
    physical = freight = install_upstairs = aftersales_c = platform_ded = tax_c = Decimal("0")
    for _o in db.execute(_oq).scalars().all():
        _b = _ofin.cost_breakdown(_o, _coef, _as_avg)
        physical += _b["physical"]; freight += _b["freight"]; install_upstairs += _b["install_upstairs"]
        aftersales_c += _b["aftersales"]; platform_ded += _b["platform"]; tax_c += _b["tax"]

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
        {"name": "物理产品成本", "amount": _dec(physical), "pct": _pct(physical)},
        {"name": "物流费", "amount": _dec(freight), "pct": _pct(freight)},
        {"name": "安装/上楼", "amount": _dec(install_upstairs), "pct": _pct(install_upstairs)},
        {"name": "售后费", "amount": _dec(aftersales_c), "pct": _pct(aftersales_c)},
        {"name": "平台扣点(手续费+活动抽成)", "amount": _dec(platform_ded), "pct": _pct(platform_ded)},
        {"name": "税费", "amount": _dec(tax_c), "pct": _pct(tax_c)},
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

    # 订单 (真实成交)
    real_count = _qc(*real)
    # 实收 = Σ实付 − Σ部分退款 (全额退款单已被 _settled 排除)
    real_paid = _qs(Order.paid_amount, *real)
    real_refund = _qs(func.coalesce(Order.refund_amount, 0), *real)
    real_revenue = round(real_paid - real_refund, 2)
    # 补单 (监控用, 统计当月全部补单, 不限成交状态)
    refill_count = _qc(*base, Order.is_refill == True)  # noqa: E712
    refill_revenue = _qs(Order.paid_amount, *base, Order.is_refill == True)  # noqa: E712
    platform_fee = _qs(Order.platform_fee, *real)

    # 支出
    promo = float(db.execute(
        select(func.coalesce(func.sum(PromotionFlow.amount), 0)).where(
            PromotionFlow.flow_type == "支出",   # 只算推广"支出"; 不能把"收入/充值"也加进推广费(否则翻倍)
            PromotionFlow.transaction_date >= start, PromotionFlow.transaction_date <= end,
        )
    ).scalar() or 0)

    factory_bill = float(db.execute(
        select(func.coalesce(func.sum(FactoryOrder.factory_bill_amount), 0)).where(
            FactoryOrder.order_date >= start,
            FactoryOrder.order_date <= end,
            FactoryOrder.voided_at.is_(None),
        )
    ).scalar() or 0)

    aftersales_rows = db.execute(
        select(AfterSales).where(
            AfterSales.processed_at >= start,
            AfterSales.processed_at <= end,
        )
    ).scalars().all()
    aftersales_comp = sum(
        float((a.factory_compensation or 0) + (a.logistics_compensation or 0) + (a.compensation_fee or 0))
        for a in aftersales_rows
    )
    aftersales_count = len(aftersales_rows)

    outsourcing = float(db.execute(
        select(func.coalesce(func.sum(OutsourcingExpense.amount), 0)).where(
            OutsourcingExpense.payment_date >= start,
            OutsourcingExpense.payment_date <= end,
        )
    ).scalar() or 0)

    # 主口径 = 正式销售额 (剔除补单, 用户拍板 2026-06-12); refill_revenue 仅作"未计入"注释
    total_revenue = real_revenue + refill_revenue   # 总流水 (含补单, 仅参考)
    # 商品成本(COGS, #17 修复): 每单 actual_cost(已对账)否则 theoretical_cost(BOM预估) —— 未对账月也有成本,
    # 利润不再虚高(原 total_expense 漏算商品成本 = 1月"25万假利润"根因)。已对账时 actual_cost≈factory_bill, 用它进利润不重复计。
    effective_cost = _qs(func.coalesce(Order.actual_cost, Order.theoretical_cost, 0), *real)
    cogs = max(effective_cost, factory_bill)   # 取更全的: 有工厂账单用账单, 否则用逐单成本估算(未对账月也有成本)
    # 税费 + 平台活动抽成 (用户拍板 2026-06-17: 所有扣项都要算进去, 之前漏了税)
    from app.services import order_financials as _ofin
    _coef = _ofin.load_coefficients(db)
    tax_filled = _qs(func.coalesce(Order.tax, 0), *real)
    tax_est_base = _qs(Order.paid_amount, *real, Order.tax.is_(None))
    tax_expense = tax_filled + tax_est_base * float(_coef["tax_rate"])
    activity_base = _qs(Order.paid_amount, *real, Order.order_date >= _coef["activity_since"])
    platform_activity = activity_base * float(_coef["activity_rate"])
    total_expense = cogs + promo + aftersales_comp + outsourcing + platform_fee + tax_expense + platform_activity
    net_profit = real_revenue - total_expense
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
        "effective_cost": round(cogs, 2),   # #17 商品成本(工厂账单与逐单估算取更全), 进利润计算
        "aftersales_compensation": round(aftersales_comp, 2),
        "aftersales_count": aftersales_count,
        "aftersales_rate": aftersales_rate,
        "outsourcing_expense": round(outsourcing, 2),
        "platform_fee": round(platform_fee, 2),
        "tax_expense": round(tax_expense, 2),                     # 税费 (用户拍板 2026-06-17 补)
        "platform_activity_expense": round(platform_activity, 2),  # 平台活动抽成 2% (5月起)
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

    summary = {
        "period": "合计",
        "real_order_count": total_real,
        "refill_order_count": total_refill,
        "real_revenue": _sum("real_revenue"),
        "refill_revenue": total_refill_rev,
        "total_revenue": total_rev,
        "refill_order_ratio": round(total_refill / (total_real + total_refill) * 100, 1) if (total_real + total_refill) else 0.0,
        "refill_cost_ratio": round(total_refill_rev / total_rev * 100, 1) if total_rev else 0.0,
        "promo_expense": total_promo,
        "promo_ratio": round(total_promo / total_rev * 100, 1) if total_rev else 0.0,
        "factory_bill": _sum("factory_bill"),
        "aftersales_compensation": _sum("aftersales_compensation"),
        "aftersales_count": sum(r["aftersales_count"] for r in rows),
        "aftersales_rate": round(sum(r["aftersales_count"] for r in rows) / total_real * 100, 1) if total_real else 0.0,
        "outsourcing_expense": _sum("outsourcing_expense"),
        "platform_fee": _sum("platform_fee"),
        "tax_expense": _sum("tax_expense"),
        "platform_activity_expense": _sum("platform_activity_expense"),
        "total_expense": total_exp,
        "net_profit": total_net,
        "net_profit_rate": round(total_net / total_rev * 100, 1) if total_rev else 0.0,
        "avg_lead_time_days": None,
    }

    return {"rows": rows, "summary": summary}
