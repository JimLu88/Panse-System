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

from app.database import get_db
from app.models.knowledge import AiKnowledge
from app.services import asset_service, health_report, sales_analytics

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


@router.get("/assets", response_model=AssetSummaryOut)
def assets_summary(db: Session = Depends(get_db)):
    """业务需求 14: 资产总额 + 饼图分类."""
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
    )


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
