"""供应链「工厂下单表」API (用户拍板 2026-06-15)。

逐单列出工厂下单内容 + 系统推算成本 + 工厂实际成本 + 差异 + 支付状态/时间 + 对账状态,
支持逐单和工厂核对。配件清单(BOM)按需展开(单独接口, 前端点开才加载, 避免列表太重)。
财务「工厂对账」只看月度结果; 这里做到逐单。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import FactoryOrder, Order

router = APIRouter(prefix="/api/factory-orders", tags=["factory-orders"])
_CENTS = Decimal("0.01")


def _expected(fo: FactoryOrder) -> Optional[Decimal]:
    """推算成本(应付): 优先 expected_amount, 否则 unit_price×qty。"""
    if fo.expected_amount is not None:
        return Decimal(str(fo.expected_amount)).quantize(_CENTS)
    if fo.unit_price is not None:
        return (Decimal(str(fo.unit_price)) * Decimal(fo.qty or 1)).quantize(_CENTS)
    return None


def _row(fo: FactoryOrder) -> dict:
    exp = _expected(fo)
    act = Decimal(str(fo.factory_bill_amount)).quantize(_CENTS) if fo.factory_bill_amount is not None else None
    diff = (exp - act).quantize(_CENTS) if (exp is not None and act is not None) else None
    return {
        "id": fo.id,
        "factory_order_no": fo.factory_order_no,
        "platform_order_no": fo.platform_order_no,
        "factory_name": fo.factory_name,
        "order_date": fo.order_date.isoformat() if fo.order_date else None,
        "product_name": fo.product_name,
        "sku": fo.sku,
        "qty": fo.qty,
        "expected_amount": float(exp) if exp is not None else None,   # 推算成本(应付)
        "factory_bill_amount": float(act) if act is not None else None,  # 工厂实际(账单)
        "diff": float(diff) if diff is not None else None,            # 差异=推算−实际
        "payment_status": fo.payment_status,
        "payment_date": fo.payment_date.isoformat() if fo.payment_date else None,
        "alipay_flow_no": fo.alipay_flow_no,
        "reconciled": act is not None,                                # 录了工厂实际即视为已核对
        "remark": fo.remark,
    }


@router.get("")
def list_factory_orders(
    factory: Optional[str] = Query(None, description="按工厂名过滤"),
    payment_status: Optional[str] = Query(None, description="unpaid/paid"),
    only_unreconciled: bool = Query(False, description="只看未核对(未录工厂实际)"),
    only_diff: bool = Query(False, description="只看推算与实际有差异的"),
    month: Optional[str] = Query(None, description="YYYY-MM 按下单月"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator", "viewer")),
):
    """工厂下单表: 逐单 下单内容 + 推算成本 + 工厂实际 + 差异 + 支付/对账状态 + 顶部汇总。"""
    stmt = (
        select(FactoryOrder)
        .where(FactoryOrder.voided_at.is_(None))
        .order_by(FactoryOrder.order_date.desc().nullslast(), FactoryOrder.id.desc())
    )
    if factory:
        stmt = stmt.where(FactoryOrder.factory_name == factory)
    if payment_status:
        stmt = stmt.where(FactoryOrder.payment_status == payment_status)
    rows = [_row(fo) for fo in db.execute(stmt).scalars().all()]
    if month:
        rows = [r for r in rows if (r["order_date"] or "").startswith(month)]
    if only_unreconciled:
        rows = [r for r in rows if not r["reconciled"]]
    if only_diff:
        rows = [r for r in rows if r["diff"] is not None and abs(r["diff"]) >= 0.01]
    exp_sum = sum((r["expected_amount"] or 0) for r in rows)
    act_sum = sum((r["factory_bill_amount"] or 0) for r in rows)
    rec = sum(1 for r in rows if r["reconciled"])
    # 支付维度: 已付金额取工厂实际(账单), 无账单则退回推算(应付); 答用户"不然不知道"
    _amt = lambda r: (r["factory_bill_amount"] if r["factory_bill_amount"] is not None else (r["expected_amount"] or 0))
    paid_rows = [r for r in rows if r["payment_status"] == "paid"]
    unpaid_rows = [r for r in rows if r["payment_status"] != "paid"]
    return {
        "rows": rows,
        "summary": {
            "count": len(rows),
            "expected_sum": round(exp_sum, 2),
            "actual_sum": round(act_sum, 2),
            "diff_sum": round(exp_sum - act_sum, 2),
            "reconciled": rec,
            "reconciled_pct": round(rec / len(rows) * 100, 1) if rows else 0,
            "paid_count": len(paid_rows),
            "unpaid_count": len(unpaid_rows),
            "paid_sum": round(sum(_amt(r) for r in paid_rows), 2),
            "unpaid_sum": round(sum(_amt(r) for r in unpaid_rows), 2),
        },
        "factories": sorted({r["factory_name"] for r in rows if r["factory_name"]}),
    }


@router.get("/{factory_order_no}/accessories")
def factory_order_accessories(
    factory_order_no: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator", "viewer")),
):
    """该工厂单的配件清单(按 SKU 的 BOM 展开)。前端点开行才调, 避免列表太重。"""
    fo = db.execute(
        select(FactoryOrder).where(FactoryOrder.factory_order_no == factory_order_no)
    ).scalar_one_or_none()
    if fo is None:
        raise HTTPException(404, "工厂单不存在")
    # BOM 以 sku_code 为键; FactoryOrder 无 sku_code → 经平台订单号回查 Order.sku_code, 兜底用 fo.sku
    sku_code: Optional[str] = None
    if fo.platform_order_no:
        o = db.execute(select(Order).where(Order.order_no == fo.platform_order_no)).scalar_one_or_none()
        sku_code = o.sku_code if o else None
    sku_code = sku_code or fo.sku
    acc: list[dict] = []
    if sku_code:
        rows = db.execute(
            select(BomLine, Material.name)
            .join(Material, BomLine.material_code == Material.code, isouter=True)
            .where(BomLine.sku_code == sku_code)
        ).all()
        for bom, mat_name in rows:
            acc.append({
                "material_code": bom.material_code,
                "material_name": mat_name or bom.material_name,
                "qty_per_product": float(bom.qty_per_product or 1),
            })
    return {"factory_order_no": factory_order_no, "sku_code": sku_code, "accessories": acc}


class ReconcileIn(BaseModel):
    factory_bill_amount: Optional[Decimal] = None
    payment_status: Optional[str] = None
    payment_date: Optional[date] = None
    alipay_flow_no: Optional[str] = None
    remark: Optional[str] = None


@router.post("/{factory_order_no}/reconcile")
def reconcile_factory_order(
    factory_order_no: str,
    body: ReconcileIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """逐单核对: 填工厂实际成本 / 支付状态 / 支付时间 / 支付宝流水号; 录了实际即"已核对"。"""
    fo = db.execute(
        select(FactoryOrder).where(FactoryOrder.factory_order_no == factory_order_no)
    ).scalar_one_or_none()
    if fo is None:
        raise HTTPException(404, "工厂单不存在")
    if body.factory_bill_amount is not None:
        fo.factory_bill_amount = body.factory_bill_amount
    if body.payment_status is not None:
        fo.payment_status = body.payment_status
    if body.payment_date is not None:
        fo.payment_date = body.payment_date
    if body.alipay_flow_no is not None:
        fo.alipay_flow_no = body.alipay_flow_no
    if body.remark is not None:
        fo.remark = body.remark
    db.commit()
    db.refresh(fo)
    return _row(fo)


@router.post("/sync-from-orders")
def sync_factory_orders_from_orders(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """把订单系统里 已付款/已发货/已签收 (去补单/去退款, 待付款不进) 的订单并入工厂下单表。

    幂等去重(source_order_id / 平台订单号); 新行带推算成本(定价表), 工厂实际留空待对账。
    用户拍板 2026-06-15: 工厂下单表 = 手工录入 + 订单系统真实订单, 加总去重。
    """
    from app.services import factory_order_service
    return factory_order_service.sync_from_orders(db)


@router.post("/import-bill")
async def import_factory_bill(
    file: UploadFile = File(...),
    dry_run: bool = Query(False, description="试运行: 只解析+匹配, 不写库"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """上传工厂对账单 xlsx → 按订单号(含追加号)把「价格」写进工厂实际(factory_bill_amount)。

    匹配不上的(备货/售后/查无订单/价格非数字)只报告、不动 —— 等后续账单再补 (用户拍板 2026-06-15)。
    支持工厂随便发来的两个 sheet 任意一个; 自动跳过标题/小计/优惠后等行。
    """
    from app.services import factory_bill_import_service
    content = await file.read()
    try:
        return factory_bill_import_service.import_bill(db, content, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"对账单解析失败: {e}")
