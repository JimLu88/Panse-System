"""供应链「工厂下单表」API (用户拍板 2026-06-15)。

逐单列出工厂下单内容 + 系统推算成本 + 工厂实际成本 + 差异 + 支付状态/时间 + 对账状态,
支持逐单和工厂核对。配件清单(BOM)按需展开(单独接口, 前端点开才加载, 避免列表太重)。
财务「工厂对账」只看月度结果; 这里做到逐单。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

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
from app.services import factory_settlement_service

router = APIRouter(prefix="/api/factory-orders", tags=["factory-orders"])
_CENTS = Decimal("0.01")
_NO_FACTORY_COST_TYPE = "same_order_topup"


def _has_no_factory_cost(fo: FactoryOrder) -> bool:
    return (fo.factory_cost_type or "normal") == _NO_FACTORY_COST_TYPE


def _expected(fo: FactoryOrder) -> Optional[Decimal]:
    """推算成本(应付): 优先 expected_amount, 否则 unit_price×qty。"""
    if fo.expected_amount is not None:
        return Decimal(str(fo.expected_amount)).quantize(_CENTS)
    if fo.unit_price is not None:
        return (Decimal(str(fo.unit_price)) * Decimal(fo.qty or 1)).quantize(_CENTS)
    return None


def _unpaid_reason(fo: FactoryOrder, exp: Optional[Decimal], act: Optional[Decimal]) -> Optional[str]:
    """按当前可核验字段生成待付初判；仅作排查线索，不改业务数据。"""
    if _has_no_factory_cost(fo):
        return None
    if fo.payment_status == "paid":
        return None
    if act is None and exp is None:
        return "缺少推算成本，且未导入或未匹配工厂账单"
    if act is None:
        return "未导入或未匹配工厂账单，暂按推算成本待付"
    if act == 0:
        return "工厂账单金额为0，需确认退款、抵扣、样品或赠品"
    if fo.payment_date or fo.alipay_flow_no:
        return "已有付款信息但状态仍为未付，需核销支付状态"
    return "已有工厂账单，尚未匹配付款流水或月结销账"


def _row(fo: FactoryOrder) -> dict:
    exp = _expected(fo)
    act = Decimal(str(fo.factory_bill_amount)).quantize(_CENTS) if fo.factory_bill_amount is not None else None
    no_factory_cost = _has_no_factory_cost(fo)
    diff = None if no_factory_cost else ((exp - act).quantize(_CENTS) if (exp is not None and act is not None) else None)
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
        "reconciled": no_factory_cost or act is not None,             # 免计费分类本身即完成核对
        "remark": fo.remark,
        "unpaid_reason": _unpaid_reason(fo, exp, act),
        "unpaid_reason_note": fo.unpaid_reason_note,
        "factory_cost_type": fo.factory_cost_type or "normal",
        "related_primary_order_no": fo.related_primary_order_no,
        "no_factory_cost": no_factory_cost,
    }


def _payable_amount(row: dict) -> float:
    if row.get("no_factory_cost"):
        return 0
    return row["factory_bill_amount"] if row["factory_bill_amount"] is not None else (row["expected_amount"] or 0)


def _effective_expected_amount(row: dict) -> float:
    return 0 if row.get("no_factory_cost") else (row["expected_amount"] or 0)


def _effective_actual_amount(row: dict) -> float:
    return 0 if row.get("no_factory_cost") else (row["factory_bill_amount"] or 0)


def _monthly_summary(rows: list[dict]) -> list[dict]:
    """按下单月归集有效工厂单；金额口径与页面已付/待付卡片一致。"""
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        month = (row["order_date"] or "")[:7] or "未注明日期"
        groups[month].append(row)

    result: list[dict] = []
    for month, items in groups.items():
        chargeable = [row for row in items if not row.get("no_factory_cost")]
        paid = [row for row in chargeable if row["payment_status"] == "paid"]
        unpaid = [row for row in chargeable if row["payment_status"] != "paid"]
        result.append({
            "month": month,
            "count": len(items),
            "no_factory_cost_count": len(items) - len(chargeable),
            "expected_sum": round(sum(_effective_expected_amount(row) for row in items), 2),
            "actual_sum": round(sum(_effective_actual_amount(row) for row in items), 2),
            "paid_count": len(paid),
            "paid_sum": round(sum(_payable_amount(row) for row in paid), 2),
            "unpaid_count": len(unpaid),
            "unpaid_sum": round(sum(_payable_amount(row) for row in unpaid), 2),
            "missing_bill_count": sum(1 for row in unpaid if row["factory_bill_amount"] is None),
            "unresolved_count": sum(1 for row in unpaid if not (row["unpaid_reason_note"] or "").strip()),
        })
    dated = sorted(
        (item for item in result if item["month"] != "未注明日期"),
        key=lambda item: item["month"],
        reverse=True,
    )
    return dated + [item for item in result if item["month"] == "未注明日期"]


@router.get("")
def list_factory_orders(
    factory: Optional[str] = Query(None, description="按工厂名过滤"),
    payment_status: Optional[str] = Query(None, description="unpaid/paid"),
    only_unreconciled: bool = Query(False, description="只看未核对(未录工厂实际)"),
    only_diff: bool = Query(False, description="只看推算与实际有差异的"),
    month: Optional[str] = Query(None, description="YYYY-MM 按下单月"),
    product_search: Optional[str] = Query(None, description="产品名/SKU/产品编码 模糊搜索"),
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
    stmt = factory_settlement_service._apply_product_search(stmt, product_search)
    # 月表基于同一工厂/产品范围内的全部记录计算，不受当前月份和支付筛选影响。
    all_rows = [_row(fo) for fo in db.execute(stmt).scalars().all()]
    rows = all_rows
    if payment_status:
        rows = [
            r for r in rows
            if not r["no_factory_cost"] and r["payment_status"] == payment_status
        ]
    if month:
        rows = [r for r in rows if (r["order_date"] or "").startswith(month)]
    if only_unreconciled:
        rows = [r for r in rows if not r["reconciled"]]
    if only_diff:
        rows = [r for r in rows if r["diff"] is not None and abs(r["diff"]) >= 0.01]
    exp_sum = sum(_effective_expected_amount(r) for r in rows)
    act_sum = sum(_effective_actual_amount(r) for r in rows)
    rec = sum(1 for r in rows if r["reconciled"])
    # 支付维度: 已付金额取工厂实际(账单), 无账单则退回推算(应付); 答用户"不然不知道"
    chargeable_rows = [r for r in rows if not r["no_factory_cost"]]
    paid_rows = [r for r in chargeable_rows if r["payment_status"] == "paid"]
    unpaid_rows = [r for r in chargeable_rows if r["payment_status"] != "paid"]
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
            "no_factory_cost_count": len(rows) - len(chargeable_rows),
            "paid_sum": round(sum(_payable_amount(r) for r in paid_rows), 2),
            "unpaid_sum": round(sum(_payable_amount(r) for r in unpaid_rows), 2),
        },
        "monthly_summary": _monthly_summary(all_rows),
        "factories": sorted({r["factory_name"] for r in all_rows if r["factory_name"]}),
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
    unpaid_reason_note: Optional[str] = None
    factory_cost_type: Optional[Literal["normal", "same_order_topup"]] = None
    related_primary_order_no: Optional[str] = None


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
    target_cost_type = body.factory_cost_type or fo.factory_cost_type or "normal"
    if target_cost_type == _NO_FACTORY_COST_TYPE:
        primary_order_no = (body.related_primary_order_no or fo.related_primary_order_no or "").strip()
        if not primary_order_no:
            raise HTTPException(400, "同订单补差价必须填写关联订单1")
        if primary_order_no == (fo.platform_order_no or "").strip():
            raise HTTPException(400, "关联订单1不能与当前补差订单相同")
        fo.factory_cost_type = _NO_FACTORY_COST_TYPE
        fo.related_primary_order_no = primary_order_no
        fo.factory_bill_amount = Decimal("0")
    else:
        fo.factory_cost_type = "normal"
        fo.related_primary_order_no = None
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
    if body.unpaid_reason_note is not None:
        fo.unpaid_reason_note = body.unpaid_reason_note.strip() or None
    db.commit()
    db.refresh(fo)
    return _row(fo)


@router.post("/{factory_order_no}/receive-restock")
def receive_restock_factory_order(
    factory_order_no: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """备货工厂单到货入库 (R3 生产入库): 设到货日 + 成品现货加回。

    只对「备货单」(非客户单 source_order_id 为空)生效; 客户单(MTO)到货由订单发货处理。
    幂等: 重复调用不会重复加库存。
    """
    from app.services import factory_order_service as fos
    fo = db.execute(
        select(FactoryOrder).where(FactoryOrder.factory_order_no == factory_order_no)
    ).scalar_one_or_none()
    if fo is None:
        raise HTTPException(404, "工厂单不存在")
    if fo.source_order_id is not None:
        raise HTTPException(400, "这是客户单(MTO), 到货应随订单发货处理, 不作备货入库")
    fos.mark_restock_delivered(db, fo.id, actor=getattr(user, "username", "system"))
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
        result = factory_bill_import_service.import_bill(db, content, dry_run=dry_run)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"对账单解析失败: {e}")
    if not dry_run:
        # 实时同步: 工厂对账单导入后立即重算货款对账
        from app.services import realtime_sync_service
        realtime_sync_service.trigger("import:factory-bill")
    return result
