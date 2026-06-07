import csv
import io
from datetime import date as _date
from decimal import Decimal as _Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.order import Order
from app.schemas.order import (
    CsvImportReport,
    OrderCreate,
    OrderOut,
    OrderStatusChange,
    OrderUpdate,
)
from app.services import (
    data_quality_service,
    exception_service,
    factory_sheet,
    order_cost_service,
    order_import,
    order_message_service,
    order_service,
    order_sync_service,
    taobao_order_import,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("", response_model=list[OrderOut])
def list_orders(
    q: Optional[str] = Query(None, description="搜索订单号/客户名"),
    status: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(Order)
    if q:
        stmt = stmt.where(or_(Order.order_no.ilike(f"%{q}%"), Order.customer_name.ilike(f"%{q}%")))
    if status:
        stmt = stmt.where(Order.status == status)
    if platform:
        stmt = stmt.where(Order.platform == platform)
    stmt = stmt.order_by(Order.order_date.desc().nulls_last(), Order.id.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


class CostLineOut(BaseModel):
    material_code: str
    material_name: Optional[str]
    qty_per_product: _Decimal
    unit_price: Optional[_Decimal]
    line_cost: Optional[_Decimal]
    missing_price: bool


class CostBreakdownOut(BaseModel):
    order_no: str
    sku_code: Optional[str]
    qty: int
    unit_cost: _Decimal
    total_cost: _Decimal
    resolved: bool
    missing_price_count: int
    cost_incomplete: bool = False
    note: Optional[str]
    lines: list[CostLineOut]


def _bd_to_out(bd: "order_cost_service.CostBreakdown") -> CostBreakdownOut:
    return CostBreakdownOut(
        order_no=bd.order_no,
        sku_code=bd.sku_code,
        qty=bd.qty,
        unit_cost=bd.unit_cost,
        total_cost=bd.total_cost,
        resolved=bd.resolved,
        missing_price_count=bd.missing_price_count,
        cost_incomplete=bd.cost_incomplete,
        note=bd.note,
        lines=[CostLineOut(**ln.__dict__) for ln in bd.lines],
    )


@router.post("/recompute-costs", response_model=dict)
def recompute_all_costs(only_missing: bool = True, db: Session = Depends(get_db)):
    """批量反推理论成本 (按 BOM × 物料单价). only_missing 仅补空值."""
    result = order_cost_service.recompute_all(db, only_missing=only_missing)
    db.commit()
    return result


@router.post("/backfill-theoretical-cost", response_model=dict)
def backfill_theoretical_cost(
    only_missing: bool = True, skip_closed: bool = True, db: Session = Depends(get_db),
):
    """用定价表会计总成本回填理论成本 (单一真值来源, 与 BOM 反推互补).

    适合冷启动/缺 BOM 的订单。skip_closed 跳过 cancelled 订单。
    """
    result = order_cost_service.backfill_theoretical_from_pricing(
        db, only_missing=only_missing, skip_closed=skip_closed,
    )
    db.commit()
    return result


@router.post("/rederive-refill-flags", response_model=dict)
def rederive_refill_flags(recompute_cost: bool = True, db: Session = Depends(get_db)):
    """以补单记录为准重判「是否补单」, 顺带重算改动订单的理论成本。返回明细供复核。"""
    r = order_sync_service.rederive_refill_flags(db, recompute_cost=recompute_cost)
    db.commit()
    return {
        "scanned": r.scanned, "flagged": r.flagged, "unflagged": r.unflagged,
        "flagged_orders": r.flagged_orders[:200],
        "unflagged_orders": r.unflagged_orders[:200],
    }


@router.get("/cost-completeness", response_model=dict)
def cost_completeness(db: Session = Depends(get_db)):
    """成本完整性体检: 列出定价表关键成本列为空(未知/待补)的 SKU, 供前端标「成本不完整」。"""
    return order_cost_service.cost_completeness_scan(db)


@router.post("/backfill-compensation", response_model=dict)
def backfill_compensation(db: Session = Depends(get_db)):
    """把售后表赔付按平台订单号聚合, 回写 Order.compensation_fee。"""
    r = order_sync_service.backfill_compensation_from_aftersales(db)
    db.commit()
    return {
        "aftersales_scanned": r.aftersales_scanned,
        "orders_updated": r.orders_updated,
        "total_compensation": str(r.total_compensation),
    }


@router.post("/backfill-warehouse", response_model=dict)
def backfill_warehouse(db: Session = Depends(get_db)):
    """对 warehouse 为空的存量订单自动填充仓库 (样块/补单→杭州, 其余→江西仓库)。幂等。"""
    n = order_sync_service.backfill_warehouse(db)
    db.commit()
    return {"updated": n, "message": f"已回填 {n} 条订单的发货仓库"}


@router.post("/mark-custom-sku", response_model=dict)
def mark_custom_sku(db: Session = Depends(get_db)):
    """微定制订单 SKU 追加「-改」后缀 (is_custom=True 且未标注的)。幂等。"""
    n = order_sync_service.mark_custom_sku_suffix(db)
    db.commit()
    return {"updated": n, "message": f"已为 {n} 条微定制订单 SKU 添加「-改」后缀"}


@router.get("/{order_id}/cost-breakdown", response_model=CostBreakdownOut)
def get_cost_breakdown(order_id: int, db: Session = Depends(get_db)):
    """反推过程可视化: 返回该订单理论成本的逐条物料明细 (不写库)."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    return _bd_to_out(order_cost_service.compute(db, o))


@router.post("/{order_id}/recompute-cost", response_model=CostBreakdownOut)
def recompute_cost(order_id: int, db: Session = Depends(get_db)):
    """反推单条订单理论成本并写回 theoretical_cost (不动 actual_cost)."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    bd = order_cost_service.recompute_and_save(db, o)
    db.commit()
    return _bd_to_out(bd)


@router.post("", response_model=OrderOut, status_code=201)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)):
    existing = db.execute(select(Order).where(Order.order_no == payload.order_no)).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"order {payload.order_no} already exists")
    o = Order(**payload.model_dump(), status="pending_payment")
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


@router.patch("/{order_id}", response_model=OrderOut)
def update_order(order_id: int, payload: OrderUpdate, db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(o, k, v)
    db.commit()
    db.refresh(o)
    return o


@router.post("/{order_id}/status", response_model=OrderOut)
def change_status(order_id: int, payload: OrderStatusChange, db: Session = Depends(get_db)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    try:
        order_service.transition(db, o, payload.status, actor=payload.actor, force=payload.force)
    except order_service.InvalidStatusTransition as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(o)
    return o


@router.post("/import-csv", response_model=CsvImportReport)
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    raw = await file.read()
    name = (file.filename or "").lower()
    if name.endswith(".xlsx") or name.endswith(".xls"):
        report = order_import.import_orders_from_xlsx(db, raw)
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")  # 中文 Excel 导出常见
        report = order_import.import_orders_from_csv(db, text)
    return CsvImportReport(
        inserted=report.inserted,
        backfilled=getattr(report, "backfilled", 0),
        skipped_duplicate=report.skipped_duplicate,
        skipped_invalid=report.skipped_invalid,
        errors=report.errors,
    )


class FactorySheetMaterialOut(BaseModel):
    material_code: str
    material_name: Optional[str]
    qty_per_product: _Decimal
    total_qty: _Decimal
    unit: Optional[str]
    spec: Optional[str]
    source: str = "bom"
    note: Optional[str] = None


class FactorySheetWarningOut(BaseModel):
    code: str
    message: str
    severity: str


class FactorySheetOut(BaseModel):
    order_no: str
    sheet_title: str
    order_date: Optional[_date]
    ship_date: Optional[_date]
    product_code: Optional[str]
    product_name: Optional[str]
    sku: Optional[str]
    sku_code: Optional[str]
    image_url: Optional[str]
    material_desc: Optional[str]
    dimension_desc: Optional[str]
    customer_name: Optional[str]
    customer_phone: Optional[str]
    customer_address: Optional[str]
    qty: int
    remark: Optional[str]
    materials: list[FactorySheetMaterialOut]
    is_custom_variant: bool
    dimension_changes: Optional[dict]
    warnings: list[FactorySheetWarningOut]


@router.post("/{order_id}/generate-factory-order")
def generate_factory_order(order_id: int, db: Session = Depends(get_db)):
    """业务需求 2/3: 从平台 Order 自动派生 FactoryOrder + 锁 BOM 库存.

    幂等. 已生成时返回已有的 FactoryOrder. 缺货时仍会创建工厂单, 但同时生成 critical Alert。
    """
    from app.services import factory_order_service
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    if o.is_historical:
        raise HTTPException(400, "历史订单不参与工厂派生")
    try:
        fo, lock = factory_order_service.generate_factory_order_for(db, o)
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {
        "factory_order_id": fo.id,
        "factory_order_no": fo.factory_order_no,
        "locked_lines": lock.locked_lines,
        "shortages": lock.shortages,
        "alerts_created": lock.alerts_created,
    }


class CreateFutureOrderIn(BaseModel):
    base_order_no: str
    activate_at: str   # ISO datetime
    product_code: Optional[str] = None
    sku: Optional[str] = None
    qty: int = 1
    customer_name: Optional[str] = None
    remark: Optional[str] = None
    platform: str = "淘宝"


class GenerateOrderDetailsIn(BaseModel):
    order_nos: Optional[list[str]] = None   # 留空 = 全部订单
    only_missing: bool = True


@router.post("/generate-order-details")
def generate_order_details(payload: GenerateOrderDetailsIn, db: Session = Depends(get_db)):
    """订单细节表自动生成 — 从 订单 + BOM 联表推导, 不再手工导入.

    order_nos 留空则处理全部订单; only_missing=True 时跳过已生成的行 (增量幂等)。
    """
    from app.services import order_detail_service
    report = order_detail_service.generate(
        db, order_nos=payload.order_nos, only_missing=payload.only_missing,
    )
    db.commit()
    return {
        "orders_scanned": report.orders_scanned,
        "orders_matched": report.orders_matched,
        "details_created": report.details_created,
        "details_skipped": report.details_skipped,
        "orders_no_bom": report.orders_no_bom[:50],   # 截断, 避免响应过大
        "orders_no_bom_count": len(report.orders_no_bom),
        "orders_no_product": report.orders_no_product,
    }


@router.post("/future")
def create_future_order(payload: CreateFutureOrderIn, db: Session = Depends(get_db)):
    """业务需求 10 选项 A: 派生一个 30 天后激活的远期订单."""
    from datetime import datetime as _dt
    from app.services import factory_order_service
    try:
        activate = _dt.fromisoformat(payload.activate_at)
    except ValueError:
        raise HTTPException(400, "activate_at 不是合法 ISO 时间")
    o = factory_order_service.create_future_order(
        db,
        base_order_no=payload.base_order_no,
        activate_at=activate,
        platform=payload.platform,
        product_code=payload.product_code,
        sku=payload.sku,
        qty=payload.qty,
        customer_name=payload.customer_name,
        remark=payload.remark,
    )
    db.commit()
    return {"id": o.id, "order_no": o.order_no, "activate_at": o.activate_at.isoformat()}


class VoidFactoryOrderIn(BaseModel):
    reason: str


class OrderEventOut(BaseModel):
    id: int
    order_id: int
    kind: str
    actor: Optional[str]
    summary: str
    detail: Optional[str]
    context_json: Optional[dict]
    created_at: str


@router.get("/{order_id}/timeline", response_model=list[OrderEventOut])
def get_order_timeline(order_id: int, db: Session = Depends(get_db)):
    """Phase 8 Tier 1 #2: 订单全生命周期时间轴 (状态变化 + 工厂单 + 库存锁定 + 退货 + 评论)."""
    from app.services import order_event_service
    events = order_event_service.list_for_order(db, order_id)
    return [
        OrderEventOut(
            id=e.id, order_id=e.order_id, kind=e.kind, actor=e.actor,
            summary=e.summary, detail=e.detail,
            context_json=e.context_json,
            created_at=e.created_at.isoformat(),
        )
        for e in events
    ]


class CommentIn(BaseModel):
    text: str


@router.post("/{order_id}/comments", response_model=OrderEventOut)
def add_comment(order_id: int, payload: CommentIn,
                db: Session = Depends(get_db)):
    """Phase 8: 在订单时间轴留评论."""
    if not payload.text.strip():
        raise HTTPException(400, "评论不能为空")
    from app.services import order_event_service
    e = order_event_service.record(
        db, order_id=order_id, kind="comment",
        actor="user", summary=payload.text.strip()[:200],
        detail=payload.text.strip(),
    )
    db.commit()
    return OrderEventOut(
        id=e.id, order_id=e.order_id, kind=e.kind, actor=e.actor,
        summary=e.summary, detail=e.detail, context_json=e.context_json,
        created_at=e.created_at.isoformat(),
    )


@router.post("/factory-orders/{factory_order_id}/void")
def void_factory_order(
    factory_order_id: int, payload: VoidFactoryOrderIn, db: Session = Depends(get_db),
):
    """业务需求 11: 作废一个工厂下单单 (会同时释放锁定库存)."""
    from app.services import factory_order_service
    fo = factory_order_service.void_factory_order(
        db, factory_order_id, reason=payload.reason,
    )
    if fo is None:
        raise HTTPException(404, "factory order not found")
    db.commit()
    return {
        "id": fo.id,
        "factory_order_no": fo.factory_order_no,
        "voided_at": fo.voided_at.isoformat() if fo.voided_at else None,
        "voided_reason": fo.voided_reason,
    }


@router.get("/{order_id}/factory-sheet", response_model=FactorySheetOut)
def get_factory_sheet(order_id: int, db: Session = Depends(get_db)):
    """业务需求 §1: 生成制单图数据 (前端渲染打印).

    自动拉 BOM 物料明细 + 加密地址检测 + 定制变更信息。
    """
    try:
        sheet = factory_sheet.build(db, order_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FactorySheetOut(
        order_no=sheet.order_no,
        sheet_title=sheet.sheet_title,
        order_date=sheet.order_date,
        ship_date=sheet.ship_date,
        product_code=sheet.product_code,
        product_name=sheet.product_name,
        sku=sheet.sku,
        sku_code=sheet.sku_code,
        image_url=sheet.image_url,
        material_desc=sheet.material_desc,
        dimension_desc=sheet.dimension_desc,
        customer_name=sheet.customer_name,
        customer_phone=sheet.customer_phone,
        customer_address=sheet.customer_address,
        qty=sheet.qty,
        remark=sheet.remark,
        materials=[FactorySheetMaterialOut(**m.__dict__) for m in sheet.materials],
        is_custom_variant=sheet.is_custom_variant,
        dimension_changes=sheet.dimension_changes,
        warnings=[FactorySheetWarningOut(**w.__dict__) for w in sheet.warnings],
    )


@router.post("/{order_id}/confirm-tracking", response_model=OrderOut)
def confirm_tracking(order_id: int, db: Session = Depends(get_db)):
    """双核对签收: 物流确认 (有物流单号 + 人工确认快递已派送)."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    o.tracking_confirmed = True
    _check_signoff(db, o)
    db.commit()
    db.refresh(o)
    return o


@router.post("/{order_id}/confirm-manual", response_model=OrderOut)
def confirm_manual(order_id: int, db: Session = Depends(get_db)):
    """双核对签收: 人工确认签收 (客户反馈/内部确认)."""
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    o.manual_confirmed = True
    _check_signoff(db, o)
    db.commit()
    db.refresh(o)
    return o


def _check_signoff(db, o: Order) -> None:
    """两个核对都完成 → 状态迁移到 signed + 自愈遗留异常; 缺一 → 标记有疑问并写异常(幂等)."""
    from datetime import datetime, timezone
    from app.models.exception import DataException

    open_q = db.query(DataException).filter_by(
        source_table="orders", source_pk=str(o.id),
        exception_type="signoff_questioned", status="open",
    )
    if o.tracking_confirmed and o.manual_confirmed:
        o.signoff_questioned = False
        # 自愈: 两核对补齐后解除该订单遗留的签收疑问异常
        for exc in open_q.all():
            exc.status = "resolved"
            exc.resolved_by = "auto_signoff"
            exc.resolved_at = datetime.now(timezone.utc).isoformat()
        if o.status == "shipped":
            try:
                order_service.transition(db, o, "signed", actor="auto_signoff")
            except order_service.InvalidStatusTransition:
                pass
    else:
        o.signoff_questioned = True
        if not open_q.first():  # 幂等: 已有 open 的不重复堆积
            exception_service.record(
                db,
                source_table="orders",
                source_pk=o.id,
                exception_type="signoff_questioned",
                severity="warning",
                description=f"订单 {o.order_no} 签收核对未完整: 物流确认={o.tracking_confirmed}, 人工确认={o.manual_confirmed}。",
                suggestion_action="请完成物流确认和人工确认两个核对环节。",
                context={"order_no": o.order_no, "tracking_confirmed": o.tracking_confirmed, "manual_confirmed": o.manual_confirmed},
            )


# -------- 粘贴消息转订单变更 --------

class ParseMessageIn(BaseModel):
    text: str


class ParseMessageOut(BaseModel):
    order_no: Optional[str]
    changes: dict
    confidence: float
    raw_text: str
    ai_available: bool


class ApplyMessageChangeIn(BaseModel):
    changes: dict
    actor: str = "operator"


@router.post("/parse-message-change", response_model=ParseMessageOut)
def parse_message_change(payload: ParseMessageIn, db: Session = Depends(get_db)):
    """将粘贴的自然语言消息解析为订单变更字段。

    AI 可用时使用 AI 解析; 不可用时正则兜底提取订单号。
    返回结果后由前端展示确认, 再调 /{order_id}/apply-message-change 落库。
    """
    result = order_message_service.parse_change(db, payload.text)
    return ParseMessageOut(**result)


@router.post("/{order_id}/apply-message-change", response_model=OrderOut)
def apply_message_change(
    order_id: int,
    payload: ApplyMessageChangeIn,
    db: Session = Depends(get_db),
):
    """将解析出的变更字段应用到指定订单 (写库 + 记录操作事件)。"""
    try:
        o = order_message_service.apply_change(
            db, order_id=order_id, changes=payload.changes, actor=payload.actor,
        )
    except ValueError as e:
        raise HTTPException(404, str(e))
    db.commit()
    db.refresh(o)
    return o


# -------- 千牛订单 Excel 快捷导入 --------

# 千牛后台导出的 Excel 固定列名 → Order 字段
_QIANNIU_COLUMN_MAP = {
    "订单编号": "order_no",
    "子订单编号": "order_no",   # fallback
    "买家昵称": "customer_name",
    "买家": "customer_name",
    "收货人姓名": "customer_name",
    "联系手机": "customer_phone",
    "手机": "customer_phone",
    "收货地址": "customer_address",
    "省": None,  # 忽略
    "市": None,
    "区": None,
    "下单时间": "order_date",
    "付款时间": "order_date",
    "宝贝名称": "product_name",
    "商品名称": "product_name",
    "商家编码": "product_code",
    "商品编码": "product_code",
    "购买数量": "qty",
    "数量": "qty",
    "实付金额": "paid_amount",
    "买家实付金额": "paid_amount",
    "快递公司": "carrier",
    "物流公司": "carrier",
    "运单号": "tracking_no",
    "快递单号": "tracking_no",
    "发货时间": "ship_date",
    "备注": "remark",
    "买家留言": "remark",
    "卖家备注": "remark",
}


class QianiuImportResult(BaseModel):
    inserted: int
    skipped_duplicate: int
    skipped_invalid: int
    errors: list[str]


@router.get("/import-qianniu/template.csv")
def qianniu_template():
    """下载千牛订单导入模板 (与千牛后台导出格式对齐的空白 CSV)。"""
    buf = io.StringIO()
    buf.write("﻿")  # BOM
    csv.writer(buf).writerow([
        "订单编号", "买家昵称", "联系手机", "收货地址",
        "下单时间", "宝贝名称", "商家编码", "购买数量",
        "实付金额", "快递公司", "运单号", "发货时间", "买家留言",
    ])
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=qianniu_orders_template.csv"},
    )


@router.post("/import-qianniu", response_model=QianiuImportResult)
async def import_qianniu_orders(
    file: UploadFile = File(...),
    platform: str = Query("淘宝", description="平台名称 (淘宝/天猫/…)"),
    db: Session = Depends(get_db),
):
    """千牛订单导出 CSV/Excel 快捷导入。

    列名与千牛后台批量导出格式自动匹配 (无需手动配置列映射)。
    重复订单号跳过, 自动设置 status=paid, platform=淘宝 (可覆盖)。
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")

    # 注入千牛列名到 order_import 的 COLUMN_ALIASES, 再调用通用导入
    from app.services import order_import as _oi
    original = dict(_oi.COLUMN_ALIASES)
    # 注入千牛列名
    for qn_col, field_name in _QIANNIU_COLUMN_MAP.items():
        if field_name and qn_col not in _oi.COLUMN_ALIASES:
            _oi.COLUMN_ALIASES[qn_col] = field_name
    # 确保 platform 列名可以被识别
    if "平台" not in _oi.COLUMN_ALIASES:
        _oi.COLUMN_ALIASES["平台"] = "platform"

    try:
        report = _oi.import_orders_from_csv(db, text)
    finally:
        # 恢复原始别名表 (避免并发污染)
        _oi.COLUMN_ALIASES.clear()
        _oi.COLUMN_ALIASES.update(original)

    db.commit()
    return QianiuImportResult(
        inserted=report.inserted,
        skipped_duplicate=report.skipped_duplicate,
        skipped_invalid=report.skipped_invalid,
        errors=report.errors,
    )


# -------- 淘宝订单多格式自动识别导入 --------

class TaobaoImportResult(BaseModel):
    detected_format: str
    inserted: int
    updated: int = 0
    skipped_duplicate: int
    skipped_invalid: int
    needs_review: int
    multi_line_orders: int
    errors: list[str]
    warnings: list[str]


@router.get("/import-taobao/detect")
async def detect_taobao_format(file: UploadFile = File(...)):
    """仅识别订单文件格式, 不入库 (上传前预检)。"""
    raw = await file.read()
    fmt = taobao_order_import.detect_format(file.filename or "", raw)
    labels = {
        "qianniu_multi": "千牛后台多表导出 (订单报表/销售明细/发货报表)",
        "sales_detail": "销售明细表 (子订单/主订单/商品属性)",
        "order_master": "订单总表格式 (请用智能导入/CSV导入)",
        "unknown": "无法识别",
    }
    return {"detected_format": fmt, "label": labels.get(fmt, fmt)}


@router.post("/import-taobao", response_model=TaobaoImportResult)
async def import_taobao(
    file: UploadFile = File(...),
    platform: str = Query("淘宝", description="平台名称"),
    force_format: Optional[str] = Query(None, description="强制格式: qianniu_multi/sales_detail"),
    db: Session = Depends(get_db),
):
    """淘宝订单自动识别导入 (两种导出格式)。

    - **千牛后台多表 Excel**: 订单报表 + 销售明细 + 发货报表, 三表按订单号关联
    - **销售明细 CSV/Excel**: 子订单编号/主订单编号/商品属性... (GBK 或 UTF-8)

    自动识别格式, 商家编码 PPS→P 还原, 商品属性提取 SKU,
    订单号科学计数法损坏自动标注, 重复订单号跳过。
    """
    raw = await file.read()
    rep = taobao_order_import.import_taobao_orders(
        db, file.filename or "", raw, platform=platform, force_format=force_format
    )
    return TaobaoImportResult(
        detected_format=rep.detected_format,
        inserted=rep.inserted,
        updated=rep.updated,
        skipped_duplicate=rep.skipped_duplicate,
        skipped_invalid=rep.skipped_invalid,
        needs_review=rep.needs_review,
        multi_line_orders=rep.multi_line_orders,
        errors=rep.errors,
        warnings=rep.warnings,
    )


# -------- 工厂单每日汇总 (手动触发) --------

@router.post("/factory-daily-summary")
def factory_daily_summary(db: Session = Depends(get_db)):
    """手动触发: 汇总今日待生产订单 (paid 状态且无工厂单) 并推送。"""
    from app.services import factory_summary_service
    return factory_summary_service.daily_summary(db)


@router.post("/scan-custom-specs")
def scan_custom_specs(db: Session = Depends(get_db)):
    """扫描"缺定制需求(尺寸/规格)"的定制订单 → 异常分类 custom_order_missing_spec。

    补全需求后这些单可用系统定制定价精确核算工厂成本(现金流工厂结算预估更准)。
    """
    from app.services import custom_order_spec_service
    result = custom_order_spec_service.scan(db)
    db.commit()
    return result


# ─────────────────────── 订单配件清单 + 物流追踪 ─────────────────────── #


class AccessoryItemOut(BaseModel):
    id: int
    order_id: int
    order_no: str
    material_code: str
    material_name: Optional[str]
    qty_required: _Decimal
    unit: Optional[str]
    is_factory_provided: bool
    source: str
    status: str
    tracking_no: Optional[str]
    carrier_code: Optional[str]
    carrier_name: Optional[str]
    tracking_last_status: Optional[str]
    tracking_updated_at: Optional[str] = None
    tracking_events: Optional[list] = None
    alert_level: Optional[str]
    alert_reason: Optional[str]
    remark: Optional[str]

    @classmethod
    def from_model(cls, m) -> "AccessoryItemOut":
        return cls(
            id=m.id, order_id=m.order_id, order_no=m.order_no,
            material_code=m.material_code, material_name=m.material_name,
            qty_required=m.qty_required, unit=m.unit,
            is_factory_provided=m.is_factory_provided, source=m.source, status=m.status,
            tracking_no=m.tracking_no, carrier_code=m.carrier_code, carrier_name=m.carrier_name,
            tracking_last_status=m.tracking_last_status,
            tracking_updated_at=m.tracking_updated_at.isoformat() if m.tracking_updated_at else None,
            tracking_events=m.tracking_events,
            alert_level=m.alert_level, alert_reason=m.alert_reason, remark=m.remark,
        )


@router.get("/{order_id}/accessories", response_model=list[AccessoryItemOut])
def list_accessories(order_id: int, db: Session = Depends(get_db)):
    """订单配件清单 (BOM 自动 + 客户备注新增)。首次访问自动按 BOM 生成。"""
    from app.services import accessory_checklist_service
    items = accessory_checklist_service.get_checklist(db, order_id)
    if not items:
        accessory_checklist_service.generate_for_order(db, order_id)
        items = accessory_checklist_service.get_checklist(db, order_id)
    return [AccessoryItemOut.from_model(m) for m in items]


@router.post("/{order_id}/accessories/regenerate", response_model=list[AccessoryItemOut])
def regenerate_accessories(order_id: int, db: Session = Depends(get_db)):
    """按当前 BOM 补全配件清单 (幂等, 不删已有行)。"""
    from app.services import accessory_checklist_service
    try:
        accessory_checklist_service.generate_for_order(db, order_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return [AccessoryItemOut.from_model(m)
            for m in accessory_checklist_service.get_checklist(db, order_id)]


class AccessoryItemUpdate(BaseModel):
    status: Optional[str] = None
    tracking_no: Optional[str] = None
    carrier_code: Optional[str] = None
    carrier_name: Optional[str] = None
    remark: Optional[str] = None
    part_purchase_id: Optional[int] = None


@router.patch("/accessories/{item_id}", response_model=AccessoryItemOut)
def update_accessory(item_id: int, payload: AccessoryItemUpdate, db: Session = Depends(get_db)):
    """更新配件行 (状态/快递单号/承运商/备注)。填快递单号会自动升级为运输中。"""
    from app.services import accessory_checklist_service
    try:
        item = accessory_checklist_service.update_item(
            db, item_id,
            status=payload.status, tracking_no=payload.tracking_no,
            carrier_code=payload.carrier_code, carrier_name=payload.carrier_name,
            remark=payload.remark, part_purchase_id=payload.part_purchase_id,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return AccessoryItemOut.from_model(item)


@router.post("/accessories/{item_id}/refresh-tracking", response_model=AccessoryItemOut)
def refresh_accessory_tracking(item_id: int, db: Session = Depends(get_db)):
    """实时查询该配件的物流并回写。未配置物流时返回当前数据 + 提示。"""
    from app.models.order import OrderAccessoryItem
    from app.services import logistics_tracking_service
    try:
        result = logistics_tracking_service.refresh_item(db, item_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    item = db.get(OrderAccessoryItem, item_id)
    out = AccessoryItemOut.from_model(item)
    if not result.get("ok"):
        # 把错误透出给前端 (不持久化), 提示改用手动状态
        out.alert_reason = result.get("error")
    return out


@router.get("/accessories/pending-summary")
def accessories_pending_summary(db: Session = Depends(get_db)):
    """跨订单配件待办汇总 (有未到货配件的订单, 按紧急程度排序)。"""
    from app.services import accessory_checklist_service
    accessory_checklist_service.refresh_all_alerts(db)
    return {"orders": accessory_checklist_service.get_summary(db)}
