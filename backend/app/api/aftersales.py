"""售后 / 退货 API (Phase 5, 业务需求 9)."""
from __future__ import annotations

import csv
import io
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.marketing import AfterSales
from app.services import bill_import_service, return_service

router = APIRouter(prefix="/api/aftersales", tags=["aftersales"])


def _refresh_pnl(db: Session, a: AfterSales) -> None:
    """售后写操作成功后刷新该订单 P&L (Plan L4); 失败不阻断主流程。"""
    try:
        from app.services import order_sync_service
        order_sync_service.refresh_order_compensation(db, a.platform_order_no)
    except Exception:  # pragma: no cover
        pass


class AfterSalesOut(BaseModel):
    id: int
    platform_order_no: str
    customer_name: Optional[str] = None
    product_name: Optional[str] = None
    status: Optional[str]
    reason: Optional[str]
    refill_tracking_no: Optional[str]
    return_tracking_no: Optional[str] = None
    product_code: Optional[str] = None   # 关联订单的产品编码 (行内拆BOM预填用)
    sku_code: Optional[str] = None
    second_inbound_confirmed: Optional[str]
    processed_at: Optional[str]
    remark: Optional[str]
    in_platform_total: Optional[Decimal] = None    # 平台内售后成本
    out_platform_total: Optional[Decimal] = None    # 平台外售后成本
    total_cost: Optional[Decimal] = None            # 售后总成本(赔付费+好评返+二次上门+返厂运费)


def _out(a: AfterSales, order=None) -> AfterSalesOut:
    from app.services.aftersales_finance_service import total_cost
    total = total_cost(a)
    return AfterSalesOut(
        id=a.id, platform_order_no=a.platform_order_no, status=a.status,
        reason=a.reason, refill_tracking_no=a.refill_tracking_no,
        return_tracking_no=a.return_tracking_no,
        second_inbound_confirmed=a.second_inbound_confirmed,
        processed_at=a.processed_at.isoformat() if a.processed_at else None,
        remark=a.remark,
        customer_name=getattr(order, "customer_name", None),
        product_name=getattr(order, "product_name", None),
        product_code=getattr(order, "product_code", None),
        sku_code=getattr(order, "sku_code", None),
        in_platform_total=a.in_platform_total,
        out_platform_total=a.out_platform_total,
        total_cost=total,
    )


@router.get("", response_model=list[AfterSalesOut])
def list_aftersales(
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    from app.models.order import Order
    q = select(AfterSales).order_by(AfterSales.id.desc()).limit(limit)
    if status:
        q = q.where(AfterSales.status == status)
    rows = db.execute(q).scalars().all()
    # join 订单补 客户名 + 产品 (售后按 platform_order_no = Order.order_no 关联)
    nos = [a.platform_order_no for a in rows if a.platform_order_no]
    omap: dict = {}
    if nos:
        for o in db.execute(select(Order).where(Order.order_no.in_(nos))).scalars().all():
            omap.setdefault(o.order_no, o)
    return [_out(a, omap.get(a.platform_order_no)) for a in rows]


# -------- 个人支付宝售后打款关联账 --------

class PaymentScanIn(BaseModel):
    start_date: date
    end_date: date
    auto_confirm_safe: bool = False


class PaymentDecisionIn(BaseModel):
    expected_version: int
    order_no: Optional[str] = None
    category: Optional[str] = None
    wanshifu_order_no: Optional[str] = None
    note: Optional[str] = None


class PaymentRejectIn(BaseModel):
    expected_version: int
    note: str


@router.get("/payment-links/preview")
def preview_payment_links(
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """只读预览：不建候选、不改流水、不改售后/订单/万师傅。"""
    if end_date < start_date:
        raise HTTPException(400, "end_date 不能早于 start_date")
    from app.services import aftersales_payment_link_service as links
    rows = links.preview(db, start_date=start_date, end_date=end_date)
    return {
        "count": len(rows),
        "auto_eligible": sum(1 for row in rows if row.auto_eligible),
        "needs_review": sum(1 for row in rows if not row.auto_eligible),
        "rows": [asdict(row) for row in rows],
        "execution_boundary": {"accounting_write": False, "platform_write": False, "notification": False},
    }


@router.post("/payment-links/scan")
def scan_payment_links(
    payload: PaymentScanIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """建立可审计候选；只有显式开启 auto_confirm_safe 才会确认安全唯一项。"""
    if payload.end_date < payload.start_date:
        raise HTTPException(400, "end_date 不能早于 start_date")
    from app.services import aftersales_payment_link_service as links
    result = links.persist_scan(
        db, start_date=payload.start_date, end_date=payload.end_date,
        auto_confirm_safe=payload.auto_confirm_safe,
        actor=getattr(user, "username", None) or "operator",
    )
    db.commit()
    return {**result, "execution_boundary": {
        "candidate_write": True,
        "accounting_write": bool(result["confirmed"]),
        "platform_write": False,
        "notification": False,
    }}


@router.get("/payment-links")
def get_payment_links(
    status: Optional[str] = None,
    limit: int = 500,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    from app.services import aftersales_payment_link_service as links
    try:
        rows = links.list_links(db, status=status, limit=limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"count": len(rows), "rows": rows}


@router.post("/payment-links/{link_id}/confirm")
def confirm_payment_link(
    link_id: int,
    payload: PaymentDecisionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    from app.services import aftersales_payment_link_service as links
    try:
        row = links.confirm(
            db, link_id, expected_version=payload.expected_version,
            actor=getattr(user, "username", None) or "operator",
            order_no=payload.order_no, category=payload.category,
            wanshifu_order_no=payload.wanshifu_order_no,
            decision_note=payload.note,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    db.commit()
    return links.serialize(db, row)


@router.post("/payment-links/{link_id}/reject")
def reject_payment_link(
    link_id: int,
    payload: PaymentRejectIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    from app.services import aftersales_payment_link_service as links
    try:
        row = links.reject(
            db, link_id, expected_version=payload.expected_version,
            actor=getattr(user, "username", None) or "operator", decision_note=payload.note,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    db.commit()
    return links.serialize(db, row)


@router.post("/payment-links/{link_id}/void")
def void_payment_link(
    link_id: int,
    payload: PaymentRejectIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    """作废不删记录；金额从系统托管售后行撤销，完整保留决策轨迹。"""
    from app.services import aftersales_payment_link_service as links
    try:
        row = links.void(
            db, link_id, expected_version=payload.expected_version,
            actor=getattr(user, "username", None) or "admin", decision_note=payload.note,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    db.commit()
    return links.serialize(db, row)


class CreateReturnIn(BaseModel):
    order_no: str
    reason: str
    tracking_no: Optional[str] = None


@router.post("", response_model=AfterSalesOut, status_code=201)
def create_return(
    payload: CreateReturnIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        a = return_service.create_return(
            db, order_no=payload.order_no, reason=payload.reason,
            tracking_no=payload.tracking_no,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    _refresh_pnl(db, a)
    db.commit()
    return _out(a)


@router.post("/{after_sales_id}/mark-received", response_model=AfterSalesOut)
def mark_received(
    after_sales_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        a = return_service.mark_received(db, after_sales_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    _refresh_pnl(db, a)
    db.commit()
    return _out(a)


class ConfirmInboundIn(BaseModel):
    product_code: str
    sku_code: Optional[str] = None
    qty: int = 1


@router.post("/{after_sales_id}/confirm-inbound", response_model=AfterSalesOut)
def confirm_inbound(
    after_sales_id: int, payload: ConfirmInboundIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """业务需求 9: 二次确认入库 (整产品, 不拆 BOM)."""
    try:
        a = return_service.confirm_return_inbound(
            db, after_sales_id, product_code=payload.product_code,
            sku_code=payload.sku_code, qty=payload.qty,
            actor=getattr(user, "username", "user"),
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    _refresh_pnl(db, a)
    db.commit()
    return _out(a)


class MarkDamagedIn(BaseModel):
    reason: str = "产品损坏不入库"


@router.post("/{after_sales_id}/mark-damaged", response_model=AfterSalesOut)
def mark_damaged(
    after_sales_id: int, payload: MarkDamagedIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    try:
        a = return_service.mark_return_damaged(
            db, after_sales_id, reason=payload.reason,
            actor=getattr(user, "username", "user"),
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    _refresh_pnl(db, a)
    db.commit()
    return _out(a)


class DisassembleIn(BaseModel):
    product_code: str
    sku_code: Optional[str] = None
    qty: int


@router.post("/disassemble-product")
def disassemble(
    payload: DisassembleIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """业务需求 9: 用户手动点 "拆 BOM" 把成品拆成物料."""
    from app.services import inventory_lock_service
    try:
        result = inventory_lock_service.disassemble_product_to_parts(
            db, product_code=payload.product_code, sku_code=payload.sku_code,
            qty=payload.qty, actor=getattr(user, "username", "user"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result


@router.get("/disassembly-logs")
def list_disassembly_logs(
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """拆 BOM 历史 (新→旧), 含是否已回撤。"""
    from sqlalchemy import select as _sel

    from app.models.disassembly_log import DisassemblyLog
    rows = db.execute(
        _sel(DisassemblyLog).order_by(DisassemblyLog.id.desc()).limit(min(limit, 500))
    ).scalars().all()
    return [{
        "id": r.id, "product_code": r.product_code, "sku_code": r.sku_code,
        "qty": float(r.qty or 0), "parts": r.parts_json or [], "actor": r.actor,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "undone_at": r.undone_at.isoformat() if r.undone_at else None,
        "undone_by": r.undone_by,
    } for r in rows]


@router.post("/disassembly-logs/{log_id}/undo")
def undo_disassembly_api(
    log_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """回撤一次拆 BOM: 成品加回、物料扣回 (物料不够扣会拒绝)。"""
    from app.services import inventory_lock_service
    try:
        result = inventory_lock_service.undo_disassembly(
            db, log_id, actor=getattr(user, "username", "user"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result


class AfterSalesPatch(BaseModel):
    return_tracking_no: Optional[str] = None
    refill_tracking_no: Optional[str] = None
    remark: Optional[str] = None


@router.patch("/{after_sales_id}", response_model=AfterSalesOut)
def update_aftersales(
    after_sales_id: int,
    payload: AfterSalesPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """补填/修改售后行的快递单号(退回/补发)与备注; 填了单号自动纳入物流追踪。"""
    a = db.get(AfterSales, after_sales_id)
    if a is None:
        raise HTTPException(404, "after sales record not found")
    data = {
        k: (v.strip() if isinstance(v, str) and v.strip() else None)
        for k, v in payload.model_dump(exclude_unset=True).items()
    }
    # 人工编辑 → 统一历史档案
    from app.services import field_change_service
    field_change_service.diff_and_apply(
        db, a, data, table="after_sales", pk=a.id,
        actor=getattr(_, "username", None), row_label=a.platform_order_no,
        field_labels={"return_tracking_no": "退回快递单号",
                      "refill_tracking_no": "补发快递单号", "remark": "备注"},
    )
    try:
        from app.services import shipment_service
        if a.return_tracking_no and "return_tracking_no" in data:
            shipment_service.upsert_shipment(db, "after_sales_return", a.id, a.return_tracking_no)
        if a.refill_tracking_no and "refill_tracking_no" in data:
            shipment_service.upsert_shipment(db, "after_sales_refill", a.id, a.refill_tracking_no)
    except Exception:  # pragma: no cover - 建追踪失败不阻断保存
        pass
    db.commit()
    return _out(a)


# -------- 批量 CSV 导入 --------

class AfterSalesImportResult(BaseModel):
    inserted: int
    skipped_invalid: int
    errors: list[str]


@router.post("/import-csv", response_model=AfterSalesImportResult)
async def import_aftersales_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """批量导入售后表 CSV (订单号必填; 其余字段按列名自动映射)。"""
    raw = await file.read()
    from app.services import tabular
    text = tabular.to_csv_text(raw, file.filename)
    r = bill_import_service.import_aftersales_csv(db, text)
    # Plan L4: 批量导入后全量回写赔付 → 订单 P&L 不滞后
    if r.inserted:
        try:
            from app.services import order_sync_service
            order_sync_service.backfill_compensation_from_aftersales(db)
        except Exception:  # pragma: no cover
            pass
    db.commit()
    return AfterSalesImportResult(inserted=r.inserted, skipped_invalid=r.skipped_invalid, errors=r.errors)


@router.get("/template.csv")
def aftersales_template():
    """下载售后表导入模板 (空白 CSV, 含正确列名)。"""
    buf = io.StringIO()
    buf.write("﻿")  # BOM for Excel
    csv.writer(buf).writerow([
        "订单号", "售后原因", "赔付费", "好评返", "平台内总", "直接赔付",
        "二次上门", "返厂运费", "平台外总", "补发SKU", "补发运单", "补发运费",
        "万师傅扣款", "工厂赔付", "物流赔偿", "支付宝流水", "处理日期", "状态", "备注",
    ])
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=aftersales_template.csv"},
    )
