"""配件采购 API (Excel 表 7 → 业务需求: OCR 拍照入 + 历史发票留存可查看).

POST /api/purchases/upload-ocr        上传发票图 → OCR → 入库 (拍照自动入)
GET  /api/purchases                   列出采购记录
GET  /api/purchases/{id}/source-image 取发票原图 (带权限)
GET  /api/purchases/files             按年月列出已上传发票文件
GET  /api/purchases/files/{id}/image  取某发票文件原图
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.order import PartPurchase, PurchaseFile
from app.services import ocr_service, purchase_storage

router = APIRouter(prefix="/api/purchases", tags=["purchases"])

_MAX_UPLOAD = 15 * 1024 * 1024   # 15 MB


class PurchaseLineOut(BaseModel):
    item_name: str
    spec: str
    unit: str
    qty: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]


class PurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    purchase_no: str
    supplier: Optional[str]
    purchase_date: Optional[date]
    material_code: Optional[str]
    material_name: Optional[str]
    spec: Optional[str]
    qty: Decimal
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    tracking_no: Optional[str]
    freight: Optional[Decimal]
    total_amount: Optional[Decimal]
    payment_status: str
    source_file_id: Optional[int]
    ocr_warnings: list[str] = []
    ocr_model: Optional[str]


class UploadOcrResult(BaseModel):
    file_id: int
    supplier: Optional[str]
    purchase_date: Optional[date]
    tracking_no: Optional[str]
    freight: Optional[Decimal]
    total_amount: Optional[Decimal]
    confidence: float
    warnings: list[str]
    lines: list[PurchaseLineOut]
    created_purchase_ids: list[int]


def _next_purchase_no(db: Session) -> str:
    """生成采购单号: PUR{YYYYMMDD}{NN}."""
    today = date.today()
    prefix = f"PUR{today:%Y%m%d}"
    last = db.execute(
        select(PartPurchase.purchase_no).where(PartPurchase.purchase_no.like(f"{prefix}%"))
        .order_by(PartPurchase.purchase_no.desc()).limit(1)
    ).scalar_one_or_none()
    seq = 1
    if last:
        try:
            seq = int(last[len(prefix):]) + 1
        except (ValueError, IndexError):
            seq = 1
    return f"{prefix}{seq:02d}"


# 表格导入列名映射 (Excel/CSV — 用户要求: 不止图片/PDF, 表格也能直接导)
_TABLE_MAP = {
    "供应商": "supplier", "店铺": "supplier", "对手方": "supplier",
    "购买日期": "purchase_date", "日期": "purchase_date", "采购日期": "purchase_date",
    "配件名称": "material_name", "名称": "material_name", "商品名称": "material_name", "品名": "material_name",
    "规格": "spec",
    "数量": "qty",
    "单价": "unit_price",
    "金额": "amount", "总价": "amount", "合计": "amount", "总金额": "amount",
    "快递单号": "tracking_no", "运单号": "tracking_no", "物流单号": "tracking_no",
    "备注": "remark",
}


@router.post("/import-table", response_model=dict)
async def import_purchases_table(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """Excel/CSV 批量导入采购记录 (核心在 purchase_table_import, 与飞书文件路由共用)。"""
    from app.services import purchase_table_import

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    try:
        return purchase_table_import.import_purchases_table_core(db, raw, file.filename)
    except Exception as e:
        raise HTTPException(400, f"文件解析失败: {e}") from e


@router.post("/upload-ocr", response_model=UploadOcrResult)
async def upload_and_ocr(
    file: UploadFile = File(...),
    auto_commit: bool = Query(True, description="OCR 后是否直接入库 (每行明细一条采购记录)"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """上传配件采购发票图 → OCR 识别 → (可选) 入库. 原图永久留存可回看。"""
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    if len(content) > _MAX_UPLOAD:
        raise HTTPException(413, "文件过大 (上限 15MB)")

    saved = purchase_storage.save_upload(
        content=content, original_name=file.filename or "invoice.jpg",
    )
    pf = PurchaseFile(
        year=saved["year"], month=saved["month"], file_path=saved["file_path"],
        original_name=saved["original_name"], mime_type=saved["mime_type"],
        size_bytes=saved["size_bytes"], uploaded_by=user.username,
    )
    db.add(pf)
    db.flush()

    warnings: list[str] = []
    parsed = None
    try:
        parsed = ocr_service.ocr_purchase_invoice(
            db, image_bytes=content, mime=saved["mime_type"] or "image/jpeg",
        )
    except ocr_service.OcrUnavailable as e:
        warnings = [f"OCR 调用失败: {e}"]
    except ocr_service.OcrParseError as e:
        warnings = [f"OCR 返回无法解析: {e}"]

    created_ids: list[int] = []
    lines_out: list[PurchaseLineOut] = []
    if parsed is not None:
        warnings = parsed.warnings
        lines_out = [
            PurchaseLineOut(
                item_name=ln.item_name, spec=ln.spec, unit=ln.unit,
                qty=ln.qty, unit_price=ln.unit_price, amount=ln.amount,
            )
            for ln in parsed.lines
        ]
        if auto_commit and parsed.lines:
            for ln in parsed.lines:
                pp = PartPurchase(
                    purchase_no=_next_purchase_no(db),
                    supplier=parsed.supplier,
                    purchase_date=parsed.purchase_date,
                    material_name=ln.item_name,
                    spec=ln.spec,
                    qty=ln.qty or Decimal("1"),
                    unit_price=ln.unit_price,
                    amount=ln.amount,
                    tracking_no=parsed.tracking_no,
                    freight=parsed.freight,
                    total_amount=parsed.total_amount,
                    source_file_id=pf.id,
                    ocr_warnings=warnings,
                    ocr_model=parsed.model,
                )
                db.add(pp)
                db.flush()
                created_ids.append(pp.id)

    db.commit()
    return UploadOcrResult(
        file_id=pf.id,
        supplier=parsed.supplier if parsed else None,
        purchase_date=parsed.purchase_date if parsed else None,
        tracking_no=parsed.tracking_no if parsed else None,
        freight=parsed.freight if parsed else None,
        total_amount=parsed.total_amount if parsed else None,
        confidence=float(parsed.confidence) if parsed else 0.0,
        warnings=warnings,
        lines=lines_out,
        created_purchase_ids=created_ids,
    )


@router.get("", response_model=list[PurchaseOut])
def list_purchases(
    limit: int = Query(200, le=1000),
    supplier: Optional[str] = None,
    include_non_purchase: bool = False,   # 默认隐藏 代扣款/理财申购/服务费/淘天 等非采购
    db: Session = Depends(get_db),
):
    q = select(PartPurchase)
    if supplier:
        q = q.where(PartPurchase.supplier == supplier)
    if not include_non_purchase:
        from sqlalchemy import and_, or_
        bad = ("代扣", "代付", "资金扣回", "消费券", "理财", "申购",
               "服务费", "手续费", "余额宝", "转入", "转出", "单次转", "转账")
        q = q.where(
            or_(PartPurchase.material_name.is_(None),
                and_(*[PartPurchase.material_name.notlike(f"%{k}%") for k in bad])),
            or_(PartPurchase.supplier.is_(None), PartPurchase.supplier.notlike("%淘天%")),
        )
    rows = db.execute(
        q.order_by(PartPurchase.purchase_date.desc().nulls_last(), PartPurchase.id.desc())
        .limit(limit)
    ).scalars().all()
    out = []
    for p in rows:
        out.append(PurchaseOut(
            id=p.id, purchase_no=p.purchase_no, supplier=p.supplier,
            purchase_date=p.purchase_date, material_code=p.material_code,
            material_name=p.material_name, spec=p.spec, qty=p.qty,
            unit_price=p.unit_price, amount=p.amount, tracking_no=p.tracking_no,
            freight=p.freight, total_amount=p.total_amount,
            payment_status=p.payment_status, source_file_id=p.source_file_id,
            ocr_warnings=p.ocr_warnings or [], ocr_model=p.ocr_model,
        ))
    return out


@router.get("/{purchase_id}/source-image")
def get_source_image(purchase_id: int, db: Session = Depends(get_db)):
    """取某条采购记录关联的发票原图."""
    p = db.get(PartPurchase, purchase_id)
    if p is None or p.source_file_id is None:
        raise HTTPException(404, "无关联发票图")
    pf = db.get(PurchaseFile, p.source_file_id)
    if pf is None:
        raise HTTPException(404, "发票文件不存在")
    try:
        data = purchase_storage.read(pf.file_path)
    except (FileNotFoundError, PermissionError) as e:
        raise HTTPException(404, str(e))
    return Response(content=data, media_type=pf.mime_type or "application/octet-stream")


class PurchaseFileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    year: int
    month: int
    original_name: Optional[str]
    mime_type: Optional[str]
    size_bytes: Optional[int]
    uploaded_by: Optional[str]


@router.get("/files", response_model=list[PurchaseFileOut])
def list_files(
    year: Optional[int] = None,
    month: Optional[int] = None,
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    """按年月列出已上传的发票文件 (历史发票留存)."""
    q = select(PurchaseFile)
    if year:
        q = q.where(PurchaseFile.year == year)
    if month:
        q = q.where(PurchaseFile.month == month)
    rows = db.execute(
        q.order_by(PurchaseFile.year.desc(), PurchaseFile.month.desc(), PurchaseFile.id.desc())
        .limit(limit)
    ).scalars().all()
    return rows


@router.get("/files/{file_id}/image")
def get_file_image(file_id: int, db: Session = Depends(get_db)):
    pf = db.get(PurchaseFile, file_id)
    if pf is None:
        raise HTTPException(404, "发票文件不存在")
    try:
        data = purchase_storage.read(pf.file_path)
    except (FileNotFoundError, PermissionError) as e:
        raise HTTPException(404, str(e))
    return Response(content=data, media_type=pf.mime_type or "application/octet-stream")


# ── 配件成本对账 (配件 epic P2, 用户 2026-06-26) ────────────────────────────
@router.get("/bulk-material-recon", response_model=dict)
def bulk_material_recon(
    granularity: str = Query("month", description="周期粒度 (暂支持 month)"),
    db: Session = Depends(get_db),
):
    """大宗/消耗材料 × 采购周期 对账 (实际采购 vs 标准消耗 vs 差异%)。

    **消费窗口按订单发货日期 ship_date 圈定** (生产周期~30天, 料在发货前才裁切消耗)。
    """
    from app.services import parts_recon_service
    return parts_recon_service.bulk_material_recon(db, granularity=granularity)


@router.get("/sporadic-monthly-overlap", response_model=list[dict])
def sporadic_monthly_overlap(
    db: Session = Depends(get_db),
):
    """双算自检: 列出「月结分类却已走零星采购(支付宝现付)」的(订单×分类)。

    这些已从月结预估扣除、并在发货清单导出标红, 供人工核对、防工厂月结重复计费多付。空=干净。
    """
    from app.services import parts_recon_service
    return parts_recon_service.detect_sporadic_monthly_overlap(db)


@router.post("/aggregate-related-parts", response_model=dict)
def aggregate_related_parts(
    apply: bool = Query(False, description="True=写 Order.actual_parts 落库; False=只出预览"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """配件采购单(填了 related_order_no)→ 按订单汇总写 Order.actual_parts(逐项真实计价)。

    默认 dry-run 出预览(含每单 physical_cost 变化); apply=True 才落库。
    """
    from app.services import parts_recon_service
    return parts_recon_service.aggregate_related_purchases(db, apply=apply)


@router.post("/capture-accessory-remarks", response_model=dict)
def capture_accessory_remarks(
    apply: bool = Query(False, description="True=落库; False=只预览"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """配件采购"备注识别"(零星采购, 用户 2026-06-27): 从采购备注解析订单号 → 填 related_order_no
    (→ 汇总进 Order.actual_parts); 匿名付款码采购按备注里的人名改挂到真实供应商。
    默认 dry-run 预览, apply=True 才落库。认不出的仍由"待归类配件采购"异常兜底。"""
    from app.services import accessory_capture_service
    return accessory_capture_service.run_capture(db, apply=apply)


@router.post("/backfill-est-parts", response_model=dict)
def backfill_est_parts(
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """一次性回填 Order.est_parts(配件标准估值 = 定价 external_parts_cost × 真实计价件数)。

    est_parts 仅作大宗材料对账「标准消耗」基线 + P3 分摊基数, 不进 physical_cost, 零财务风险。
    """
    from app.services import order_cost_service
    res = order_cost_service.backfill_est_parts(db)
    db.commit()
    return res


# ── 工厂月度对账 + 当月发货清单导出 (用户 2026-06-26) ────────────────────────
class MonthlyReconIn(BaseModel):
    material_key: str
    year_month: str                  # 'YYYY-MM' (发货月)
    actual_total: Decimal            # 工厂返回的当月总额
    supplier: Optional[str] = None
    note: Optional[str] = None
    recon_id: Optional[int] = None   # 给了=更新该行, 否则新增


@router.get("/monthly-recon", response_model=list[dict])
def list_monthly_recon(
    material_key: Optional[str] = None,
    year_month: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """列出已录入的工厂月度对账总额。"""
    from app.services import parts_recon_service
    return parts_recon_service.list_monthly_recon(db, material_key=material_key, year_month=year_month)


@router.post("/monthly-recon", response_model=dict)
def save_monthly_recon(
    body: MonthlyReconIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """录入/更新 某材料某月工厂返回的对账总额(作为大宗材料对账的「实际」列)。"""
    from app.services import parts_recon_service
    try:
        return parts_recon_service.save_monthly_recon(
            db, material_key=body.material_key, year_month=body.year_month,
            actual_total=body.actual_total, supplier=body.supplier,
            note=body.note, recon_id=body.recon_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.delete("/monthly-recon/{recon_id}", response_model=dict)
def delete_monthly_recon(
    recon_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    from app.services import parts_recon_service
    ok = parts_recon_service.delete_monthly_recon(db, recon_id)
    if not ok:
        raise HTTPException(404, "对账记录不存在")
    return {"deleted": True, "id": recon_id}


@router.get("/shipped-orders-export", response_model=dict)
def shipped_orders_export(
    year_month: str = Query(..., description="发货月 'YYYY-MM'"),
    material_key: Optional[str] = Query(None, description="给了=按该材料筛+展开BOM明细; 空=全部发货单"),
    db: Session = Depends(get_db),
):
    """导出当月『已发货』成交订单清单(发给工厂对账)。

    material_key 空=全部发货单(基础列); 给了=只列用该材料的发货单 + 逐单 BOM 部位/预设尺寸明细。
    **按发货日期 ship_date 圈定。**
    """
    from app.services import parts_recon_service
    return parts_recon_service.export_shipped_orders(db, year_month=year_month, material_key=material_key)


@router.get("/shipped-orders-export.xlsx")
def shipped_orders_export_xlsx(
    year_month: Optional[str] = Query(None, description="发货月 'YYYY-MM'(与 date_from/to 二选一)"),
    date_from: Optional[str] = Query(None, description="发货日起 'YYYY-MM-DD'"),
    date_to: Optional[str] = Query(None, description="发货日止 'YYYY-MM-DD'"),
    material_key: Optional[str] = Query(None, description="给了=按分类逐件展开 BOM + 单价/总价"),
    db: Session = Depends(get_db),
):
    """导清单 xlsx 下载(单月 year_month 或发货日区间 date_from~date_to)。发给工厂对账用。"""
    import io
    from urllib.parse import quote

    from fastapi.responses import StreamingResponse

    from app.services import parts_recon_service
    wb, d = parts_recon_service.build_shipped_orders_xlsx(
        db, year_month=year_month, date_from=date_from, date_to=date_to, material_key=material_key)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"配件对账清单_{material_key or '全部发货单'}_{d.get('period') or year_month}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


@router.get("/bulk-recon-export.xlsx")
def bulk_recon_export_xlsx(
    date_from: Optional[str] = Query(None, description="发货日起 'YYYY-MM-DD'"),
    date_to: Optional[str] = Query(None, description="发货日止 'YYYY-MM-DD'"),
    year_month: Optional[str] = Query(None, description="单月 'YYYY-MM'(与区间二选一)"),
    db: Session = Depends(get_db),
):
    """一份多 sheet 对账工作簿(自己对账所有月结账户用): sheet「全部发货单」+ 每个月结账户
    (五金/电力轨道/岩板/玻璃)一页逐单展开 BOM + 系统预估单价/总价。按发货日期 ship_date 圈定。"""
    import io
    from urllib.parse import quote

    from fastapi.responses import StreamingResponse

    from app.services import parts_recon_service
    wb, meta = parts_recon_service.build_bulk_recon_workbook(
        db, date_from=date_from, date_to=date_to, year_month=year_month)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"月结对账_全部账户_{meta.get('period') or year_month or ''}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
