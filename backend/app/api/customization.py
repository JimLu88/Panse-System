"""尺寸微定制 API (业务需求 §2)."""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import customization_ai_service, customization_service

router = APIRouter(prefix="/api/customization", tags=["customization"])


class DiffLineOut(BaseModel):
    material_code: str
    material_name: Optional[str]
    original_qty: Decimal
    new_qty: Decimal
    note: Optional[str]
    requires_new_material: bool = False


class PreviewOut(BaseModel):
    base_sku_code: str
    proposed_custom_sku_code: str
    dimension_changes: dict
    diff_lines: list[DiffLineOut]


class PreviewIn(BaseModel):
    base_sku_code: str = Field(..., min_length=3)
    dimension_changes: dict = Field(
        ..., description="如 {长: 2000, 宽: 400} (单位 mm), 不变的可不传"
    )


@router.post("/preview", response_model=PreviewOut)
def preview(payload: PreviewIn, db: Session = Depends(get_db)):
    try:
        r = customization_service.preview(
            db,
            base_sku_code=payload.base_sku_code,
            dimension_changes=payload.dimension_changes,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return PreviewOut(
        base_sku_code=r.base_sku_code,
        proposed_custom_sku_code=r.proposed_custom_sku_code,
        dimension_changes=r.dimension_changes,
        diff_lines=[DiffLineOut(**d.__dict__) for d in r.diff_lines],
    )


class ConfirmIn(BaseModel):
    base_sku_code: str
    dimension_changes: dict
    order_no: Optional[str] = None
    note: Optional[str] = None
    qty_overrides: Optional[dict[str, Decimal]] = None  # material_code → 新数量


class ConfirmOut(BaseModel):
    custom_variant_id: int
    custom_sku_code: str
    cloned_bom_lines: int


class PriceBreakdownItemOut(BaseModel):
    label: str
    amount: float
    note: str = ""


class AiQuoteOut(BaseModel):
    base_product: Optional[str]
    base_sku: Optional[str]
    base_size: Optional[str]
    changes: list[str]
    est_price: Optional[float]
    breakdown: list[PriceBreakdownItemOut]
    ai_used: bool
    model: Optional[str]
    error: Optional[str]


@router.post("/ai-quote", response_model=AiQuoteOut)
async def ai_quote(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    data = await image.read()
    mime = image.content_type or "image/jpeg"
    result = customization_ai_service.ai_quote(db, data, mime)
    return AiQuoteOut(
        base_product=result.base_product,
        base_sku=result.base_sku,
        base_size=result.base_size,
        changes=result.changes,
        est_price=result.est_price,
        breakdown=[PriceBreakdownItemOut(**b.__dict__) for b in result.breakdown],
        ai_used=result.ai_used,
        model=result.model,
        error=result.error,
    )


@router.post("/confirm", response_model=ConfirmOut, status_code=201)
def confirm(payload: ConfirmIn, db: Session = Depends(get_db)):
    try:
        r = customization_service.confirm(
            db,
            base_sku_code=payload.base_sku_code,
            dimension_changes=payload.dimension_changes,
            order_no=payload.order_no,
            note=payload.note,
            qty_overrides=payload.qty_overrides,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return ConfirmOut(**r.__dict__)


# -------- 全定制报价参数 (后台可调) --------

@router.get("/quote-config", response_model=dict)
def get_quote_config(db: Session = Depends(get_db)):
    """读全定制报价参数 (利润系数/人工表/大小规则/单价/打包/投影)."""
    from app.services import custom_quote_config_service as cfg
    return cfg.get_config(db)


class QuoteConfigPatch(BaseModel):
    factory_profit_rate: Optional[float] = None
    panse_profit_rate: Optional[float] = None
    safety_rate: Optional[float] = None
    projection_type: Optional[str] = None
    projection_rate: Optional[float] = None
    packing: Optional[list[float]] = None
    freight: Optional[list[float]] = None
    install: Optional[list[float]] = None
    labor: Optional[dict[str, list[float]]] = None
    size_rules: Optional[dict[str, list[float]]] = None
    prices: Optional[dict[str, float]] = None


@router.put("/quote-config", response_model=dict)
def update_quote_config(payload: QuoteConfigPatch, db: Session = Depends(get_db)):
    """改报价参数 (只更新传入的键), 返回完整配置."""
    from app.services import custom_quote_config_service as cfg
    patch = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    result = cfg.save_config(db, patch)
    db.commit()
    return result


# -------- 全定制: 板单 → 报价 + 工厂对比 + 投影对照 --------

class BoardIn(BaseModel):
    part: str
    material: str
    length_cm: float = 0
    width_cm: float = 0
    qty: float = 1
    unit: str = "平方米"
    is_accessory: bool = False
    is_drawer_rail: bool = False


class BoardQuoteIn(BaseModel):
    product_type: str
    length_m: float
    overall_width_m: Optional[float] = None
    overall_height_m: Optional[float] = None
    boards: list[BoardIn]
    factory_quote: Optional[float] = None   # 工厂报价(可填, 用于显示差额)


class BoardQuoteOut(BaseModel):
    wood_cost: float
    labor_fee: float
    factory_in_cost: float
    factory_profit: float
    factory_wood_total: float
    accessory_total: float
    drawer_rail_total: float
    packing_fee: float
    freight: float
    install_fee: float
    panse_cost: float
    final_quote: float
    factory_quote_compare: float
    factory_quote_conservative: float       # 工厂对比×安全系数(宁高不低)
    safety_rate: float
    projection_estimate: Optional[float]
    projection_area_m2: Optional[float]
    factory_quote: Optional[float] = None
    factory_diff: Optional[float] = None     # 工厂报价 − 我的保守对比价
    size_class: str
    wood_lines: list[dict]
    accessory_lines: list[dict]


@router.post("/board-quote", response_model=BoardQuoteOut)
def board_quote(payload: BoardQuoteIn, db: Session = Depends(get_db)):
    """按板单实时算价 (单价/人工/系数全从后台配置读)."""
    from app.services import custom_quote_service as q
    from app.services import custom_quote_config_service as cfg_svc
    specs = [q.BoardSpec(**b.model_dump()) for b in payload.boards]
    r = q.quote_from_spec(
        db, product_type=payload.product_type, length_m=payload.length_m,
        boards=specs, overall_width_m=payload.overall_width_m,
        overall_height_m=payload.overall_height_m,
    )
    cfg = cfg_svc.get_config(db)
    fq = payload.factory_quote
    return BoardQuoteOut(
        wood_cost=float(r.wood_cost), labor_fee=float(r.labor_fee),
        factory_in_cost=float(r.factory_in_cost), factory_profit=float(r.factory_profit),
        factory_wood_total=float(r.factory_wood_total), accessory_total=float(r.accessory_total),
        drawer_rail_total=float(r.drawer_rail_total), packing_fee=float(r.packing_fee),
        freight=float(r.freight), install_fee=float(r.install_fee),
        panse_cost=float(r.panse_cost), final_quote=float(r.final_quote),
        factory_quote_compare=float(r.factory_quote_compare),
        factory_quote_conservative=float(r.factory_quote_conservative),
        safety_rate=float(r.safety_rate),
        projection_estimate=float(r.projection_estimate) if r.projection_estimate is not None else None,
        projection_area_m2=float(r.projection_area_m2) if r.projection_area_m2 is not None else None,
        factory_quote=fq,
        factory_diff=(fq - float(r.factory_quote_conservative)) if fq is not None else None,
        size_class=cfg_svc.classify_size(cfg, payload.product_type, payload.length_m),
        wood_lines=r.wood_lines, accessory_lines=r.accessory_lines,
    )


@router.post("/extract-boards", response_model=dict)
async def extract_boards(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """上传设计图 → AI 拆板单 (AI 不可用时返回空, 前端转手动)."""
    raw = await file.read()
    return customization_ai_service.extract_boards(db, raw, file.content_type or "image/jpeg")


# -------- 竞品 Top-10 (按匹配度) --------

class CompetitorOut(BaseModel):
    store: Optional[str]
    category: Optional[str]
    product: Optional[str]
    link: Optional[str]
    wood: Optional[str]
    sku_name: Optional[str]
    daily_price: Optional[float]
    confidence: float


@router.get("/competitors", response_model=list[CompetitorOut])
def competitors_top(q: str = "", limit: int = 10, db: Session = Depends(get_db)):
    """按查询词(产品名/SKU)返回竞品 Top-N, 匹配度从高到低 (中文友好相似度)."""
    from sqlalchemy import select
    from app.models.competitor import CompetitorPrice
    from app.services.product_match_service import _similarity

    if not q.strip():
        return []
    rows = db.execute(select(CompetitorPrice)).scalars().all()
    scored = []
    for r in rows:
        target = " ".join(filter(None, [r.product, r.sku_name, r.wood]))
        conf = _similarity(q, target)
        if conf > 0:
            scored.append((conf, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        CompetitorOut(
            store=r.store, category=r.category, product=r.product, link=r.link,
            wood=r.wood, sku_name=r.sku_name,
            daily_price=float(r.daily_price) if r.daily_price is not None else None,
            confidence=round(conf, 2),
        )
        for conf, r in scored[:limit]
    ]
