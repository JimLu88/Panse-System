"""工厂逐单对账 API — 导入工厂侧对账单 xlsx + 逐月对账 + 逐单填原因做平。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.order import Order, OrderDetail
from app.models.pricing import PricingSku
from app.services import (
    factory_recon_import_service,
    factory_recon_service,
    import_storage,
    sales_analytics,
)

router = APIRouter(prefix="/api/factory-recon", tags=["factory-recon"])


@router.post("/import")
def import_factory_recon(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """导入工厂侧对账单 xlsx (价格=工厂结算价=成本); 自动回填 Order.actual_cost。"""
    content = file.file.read()
    arch = import_storage.archive(
        db, content=content, original_name=file.filename or "factory_recon.xlsx",
        kind="factory_recon", source="web", uploaded_by=getattr(user, "username", None),
    )
    rep = factory_recon_import_service.import_factory_recon_xlsx(db, content)
    import_storage.update_summary(db, arch.file.id, {
        "inserted": rep.inserted, "skipped_duplicate": rep.skipped_duplicate,
        "backfilled_cost": rep.backfilled_cost,
    })
    db.commit()
    # 实时同步: 工厂对账单导入后自动跑全流水线(工厂流水匹配+货款对账+成本), 不用再手点
    from app.services import realtime_sync_service
    realtime_sync_service.trigger("import:factory-recon")
    return {
        "inserted": rep.inserted, "skipped_invalid": rep.skipped_invalid,
        "skipped_duplicate": rep.skipped_duplicate, "backfilled_cost": rep.backfilled_cost,
        "sheets": rep.sheets, "unmapped_columns": rep.unmapped_columns, "errors": rep.errors,
        "archived_file_id": arch.file.id, "duplicate_upload": arch.is_duplicate,
    }


@router.get("/summary")
def factory_recon_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """逐月对账汇总: 应付(Σ结算价) ↔ 实付(factory_payment) + 差额 + 已归因状态。"""
    return factory_recon_service.summary(db)


@router.get("/preview-from-orders")
def factory_recon_preview_from_orders(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """工厂对账单未导入时的逐单预估: 用我方「工厂下单」数据(应付=账单额/理论成本), 只读不写。"""
    return factory_recon_service.preview_from_orders(db)


@router.get("/items")
def factory_recon_items(
    period: str | None = Query(None, description="YYYY-MM"),
    status: str | None = Query(None, description="resolved / open"),
    q: str | None = Query(None, description="订单号/客户/详情 关键词"),
    limit: int = Query(500, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """工厂结算逐单明细。"""
    return factory_recon_service.list_items(
        db, period=period, status=status, q=q, limit=limit, offset=offset,
    )


@router.post("/items/{item_id}/resolve")
def resolve_factory_recon_item(
    item_id: int,
    reason: str = Body("", embed=True),
    resolved: bool = Body(True, embed=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """对某条工厂结算行「填原因做平」(扣减/减免/差异原因) 或撤销做平。"""
    out = factory_recon_service.resolve(
        db, item_id, reason=reason, actor=getattr(user, "username", None), resolved=resolved,
    )
    db.commit()
    return out


@router.post("/items/{item_id}/split")
def split_factory_recon_item(
    item_id: int,
    parts: list[dict] = Body(..., embed=True,
                             description='[{"amount":"120.00","resolution_kind":"价差","remark":"..."}]'),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """Plan L5: 差异行拆分归因 — Σ 子行金额必须 = 原行金额, 不平 → 400。"""
    try:
        out = factory_recon_service.split_item(
            db, item_id, parts=parts, actor=getattr(user, "username", None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return out


@router.post("/items/{item_id}/confirm")
def confirm_factory_recon_item(
    item_id: int,
    resolution_kind: str = Body(..., embed=True, description="漏单/价差/运费/补偿/其他"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """Plan L5: 确认差异行归因 (确认人/时间落库)。"""
    try:
        out = factory_recon_service.confirm_item(
            db, item_id, resolution_kind=resolution_kind,
            actor=getattr(user, "username", None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return out


def _month_key(o: Order) -> str | None:
    d = o.order_date or o.ship_date
    return d.strftime("%Y-%m") if d else None


def _predicted_factory(db: Session, o: Order) -> Decimal:
    """我方预测的工厂出厂成本: 多产品单按 order_details 各行 factory_cost 汇总; 否则单SKU factory_cost。"""
    lines = db.execute(
        select(OrderDetail).where(
            OrderDetail.order_no == o.order_no, OrderDetail.source == "import")
    ).scalars().all()
    if len(lines) >= 2:
        total = Decimal("0")
        for ln in lines:
            if not ln.sku_code:
                continue
            ps = db.execute(select(PricingSku).where(PricingSku.sku_code == ln.sku_code)).scalars().first()
            v = (ps.factory_cost or ps.physical_cost) if ps else None
            if v is not None:
                total += Decimal(str(v)) * int(ln.qty or 1)
        if total > 0:
            return total
    if o.sku_code:
        ps = db.execute(select(PricingSku).where(PricingSku.sku_code == o.sku_code)).scalars().first()
        v = (ps.factory_cost or ps.physical_cost) if ps else None
        if v is not None:
            return Decimal(str(v))
    return Decimal(str(o.theoretical_cost or 0))


@router.get("/cost-comparison")
def factory_cost_comparison(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """工厂实收(actual_cost=工厂结算价) vs 我方预测(pricing factory_cost) 按月对比 —— 饼图数据。

    只统计「已有工厂价格(actual_cost not None)」的成交单(用户拍板: 只算已生成工厂价的数据)。
    coverage = 本月有工厂价单数 / 本月成交单数(揭示 5/6月覆盖率低 → 预测占比大)。
    """
    orders = db.execute(
        select(Order).where(
            sales_analytics.settled_sale_clause(),
            Order.is_refill == False,  # noqa: E712
        )
    ).scalars().all()
    by_month: dict[str, dict] = {}
    for o in orders:
        m = _month_key(o)
        if not m:
            continue
        d = by_month.setdefault(m, {"predicted": Decimal("0"), "actual": Decimal("0"),
                                    "n_actual": 0, "n_total": 0})
        d["n_total"] += 1
        if o.actual_cost is None:
            continue
        d["predicted"] += _predicted_factory(db, o)
        d["actual"] += Decimal(str(o.actual_cost))
        d["n_actual"] += 1
    months = []
    tot_p = Decimal("0")
    tot_a = Decimal("0")
    for m in sorted(by_month):
        d = by_month[m]
        pred = d["predicted"]
        act = d["actual"]
        tot_p += pred
        tot_a += act
        months.append({
            "month": m,
            "predicted": float(pred),
            "actual": float(act),
            "diff": float(act - pred),
            "diff_pct": float((act - pred) / pred * 100) if pred else 0.0,
            "n_actual": d["n_actual"],
            "n_total": d["n_total"],
            "coverage_pct": round(d["n_actual"] / d["n_total"] * 100, 1) if d["n_total"] else 0.0,
        })
    return {
        "months": months,
        "totals": {
            "predicted": float(tot_p),
            "actual": float(tot_a),
            "diff": float(tot_a - tot_p),
            "diff_pct": float((tot_a - tot_p) / tot_p * 100) if tot_p else 0.0,
        },
    }
