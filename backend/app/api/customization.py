"""尺寸微定制 API (业务需求 §2)."""
import asyncio
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import customization_ai_service, customization_service

router = APIRouter(prefix="/api/customization", tags=["customization"])


def _log_quote(db: Session, *, source: str, user_message: str = "",
               ai_response: str = "", model: Optional[str] = None,
               extra: Optional[dict] = None) -> None:
    """报价留痕: 每次定制报价(AI对话/截图/板单)记一条 ai_chat_logs(action_type=custom_quote),
    可复盘"为啥报这价"/审计/统计 AI 准确率。复用现有表, 最佳努力, 失败绝不影响报价返回。"""
    try:
        from app.models.ai import AiChatLog
        db.add(AiChatLog(
            action_type="custom_quote",
            user_message=(user_message or "")[:4000],
            ai_response=(ai_response or "")[:8000],
            model=model,
            extra={"source": source, **(extra or {})},
        ))
        db.commit()
    except Exception:  # noqa: BLE001 - 留痕失败不能影响报价
        try:
            db.rollback()
        except Exception:
            pass


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
    # Plan F5: 库存预检 {in_stock, need_purchase, need_new_material, has_shortage}
    stock_check: Optional[dict] = None


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
        stock_check=customization_service.precheck_stock(db, r.diff_lines),
    )


class ConfirmIn(BaseModel):
    base_sku_code: str
    dimension_changes: dict
    order_no: Optional[str] = None
    note: Optional[str] = None
    qty_overrides: Optional[dict[str, Decimal]] = None  # material_code → 新数量
    # Plan F5: 缺料时必须显式确认才放行 (前端分组弹窗确认后置 True)
    acknowledge_shortage: bool = False


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
    result = await asyncio.to_thread(customization_ai_service.ai_quote, db, data, mime)
    _log_quote(db, source="ai_quote", user_message=f"{result.base_product or ''} {result.changes}",
               ai_response=" | ".join(f"{b.label}:{b.amount}" for b in result.breakdown),
               model=result.model, extra={"est_price": result.est_price, "base_sku": result.base_sku})
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
    # Plan F5: 缺料前置检查 — 未确认缺料 → 409 返回分组明细, 前端弹窗确认后重发
    try:
        pre = customization_service.preview(
            db, base_sku_code=payload.base_sku_code,
            dimension_changes=payload.dimension_changes,
        )
        check = customization_service.precheck_stock(db, pre.diff_lines)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if check["has_shortage"] and not payload.acknowledge_shortage:
        raise HTTPException(409, detail={
            "shortage_unacknowledged": True,
            "message": "存在缺料/需新开料, 请确认后再下定制单",
            "stock_check": check,
        })
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
    platform_fee_rate: Optional[float] = None
    tax_rate: Optional[float] = None
    style_labor_ratio: Optional[float] = None
    style_remove_credit: Optional[float] = None
    paint_table_base: Optional[float] = None
    paint_sideboard_base: Optional[float] = None
    paint_fixed_ratio: Optional[float] = None
    competitor_coupon_rate: Optional[float] = None
    projection_type: Optional[str] = None
    projection_rate: Optional[float] = None
    packing: Optional[list[float]] = None
    freight: Optional[list[float]] = None
    install: Optional[list[float]] = None
    labor: Optional[dict[str, list[float]]] = None
    size_rules: Optional[dict[str, list[float]]] = None
    size_sanity_factor: Optional[float] = None
    prices: Optional[dict[str, float]] = None


def _validate_quote_config(patch: dict) -> None:
    """报价参数范围校验, 防误填把全公司报价带歪。"""
    def _rate(k: str, lo: float, hi: float) -> None:
        if patch.get(k) is not None and not (lo <= float(patch[k]) <= hi):
            raise HTTPException(400, f"{k} 必须在 {lo}~{hi} 之间")
    _rate("factory_profit_rate", 0, 0.95)
    _rate("panse_profit_rate", 0, 0.95)
    _rate("competitor_coupon_rate", 0, 0.95)
    _rate("platform_fee_rate", 0, 0.95)
    _rate("tax_rate", 0, 0.95)
    _rate("style_labor_ratio", 0, 5.0)
    _rate("style_remove_credit", 0, 1.0)
    _rate("paint_fixed_ratio", 0.5, 0.95)
    _rate("safety_rate", 1.0, 3.0)
    if patch.get("projection_rate") is not None and float(patch["projection_rate"]) <= 0:
        raise HTTPException(400, "projection_rate 必须 > 0")
    if patch.get("size_sanity_factor") is not None and float(patch["size_sanity_factor"]) <= 0:
        raise HTTPException(400, "size_sanity_factor 必须 > 0")
    for k in ("paint_table_base", "paint_sideboard_base"):
        if patch.get(k) is not None and float(patch[k]) < 0:
            raise HTTPException(400, f"{k} 不能为负")
    for k in ("packing", "freight", "install"):
        arr = patch.get(k)
        if arr is not None and not (isinstance(arr, list) and len(arr) == 3 and all(float(x) >= 0 for x in arr)):
            raise HTTPException(400, f"{k} 必须是 3 个非负数 [小,中,大]")
    if patch.get("prices") is not None:
        for mat, p in patch["prices"].items():
            if float(p) < 0:
                raise HTTPException(400, f"价格「{mat}」不能为负")


@router.put("/quote-config", response_model=dict)
def update_quote_config(payload: QuoteConfigPatch, db: Session = Depends(get_db)):
    """改报价参数 (只更新传入的键, 带范围校验), 返回完整配置."""
    from app.services import custom_quote_config_service as cfg
    patch = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    _validate_quote_config(patch)
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
    _log_quote(db, source="board_quote",
               user_message=f"{payload.product_type} {payload.length_m}m {len(payload.boards)}板",
               extra={"final_quote": float(r.final_quote),
                      "factory_compare": float(r.factory_quote_conservative)})
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
    return await asyncio.to_thread(customization_ai_service.extract_boards, db, raw, file.content_type or "image/jpeg")


# -------- 竞品价库 API 已拆到 app/api/competitor.py (2026-06-16 结构拆分, 逻辑不变) --------

# ───────────────────────── 定制报价 v2 (理顺 + 提速) ─────────────────────────
# 一个分类器前门 + 两条确定性管道 (见 docs/定制报价v2_理顺提速_落地方案.md)。
# 全部新增端点, 与旧端点影子并行(旧端点不动); 普通定制 0 次额外 AI, 纯算术。


class V2ClassifyIn(BaseModel):
    text: str = Field("", description="客户定制描述")
    image_count: int = 0


class V2QuoteLightIn(BaseModel):
    base_product_code: str = Field(..., min_length=1)
    target_length_m: Optional[float] = None
    target_width_cm: Optional[float] = None
    target_height_cm: Optional[float] = None
    target_material: Optional[str] = None
    add_parts: list[dict] = Field(default_factory=list)
    remove_parts: list[dict] = Field(default_factory=list)
    modify_parts: list[dict] = Field(default_factory=list)
    price_tier: str = "big"
    base_sku_code: Optional[str] = None
    category: Optional[str] = None      # quote-both 用: 纯定制口径出板单的品类(空则从产品取)
    lower_cabinet_height_cm: Optional[float] = None   # quote-both 用: 下柜高(门/玻璃取尺寸)
    description: Optional[str] = None                  # quote-both 用: 选组合SKU的BOM


class V2BoardIn(BaseModel):
    part: str = ""
    material: str = ""
    length_cm: float = 0
    width_cm: float = 0
    qty: float = 1
    unit: str = "平方米"
    is_accessory: bool = False
    is_drawer_rail: bool = False


class V2QuoteHeavyIn(BaseModel):
    product_type: str = Field(..., min_length=1)
    length_m: float = Field(..., gt=0)
    boards: List[V2BoardIn] = Field(default_factory=list)
    overall_width_m: Optional[float] = None
    overall_height_m: Optional[float] = None
    auto_hardware: bool = True


@router.post("/v2/classify")
async def v2_classify(
    message: str = Form(""),
    images: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
) -> dict:
    """前门分类器: 自由文字/图 → AI 结构化(类型+产品+尺寸+材质+增减); AI 不可用回落确定性匹配。"""
    from app.services import custom_quote_v2_service as v2
    from app.services import customization_ai_service, settings_service

    def _shrink(raw: bytes) -> tuple[bytes, str]:
        """大图压到最长边1024/JPEG85 再喂视觉模型: 1MB原图→~150KB, vision token 大减
        (2026-07-12: 原图直接喂 7B 模型推理 >120s 必超时 → 图片等于白传)。失败原样返回。"""
        try:
            import io as _io
            from PIL import Image
            im = Image.open(_io.BytesIO(raw))
            im = im.convert("RGB")
            im.thumbnail((1024, 1024))
            buf = _io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
        except Exception:  # noqa: BLE001
            return raw, "image/jpeg"

    image_data: list[tuple[bytes, str]] = []
    for img in (images or [])[:5]:
        raw = await img.read()
        image_data.append(_shrink(raw) if len(raw) > 300_000 else (raw, img.content_type or "image/jpeg"))

    result = None
    ai_note = None
    cfg = settings_service.get_ai_config(db, "custom")  # 定制报价专属槽; 没配回落 ocr
    # 报错里直接点名当前实际打的 provider/model/接口地址, 免得再对着"请查 Ollama"猜(2026-07-18)。
    _tgt = (f"{cfg.get('provider')}·{cfg.get('model') or '未填模型'}·"
            f"{cfg.get('base_url') or 'OpenAI官方(接口地址没填→千问/阿里云会打错!)'}")
    if cfg.get("api_key") or cfg.get("base_url"):        # 本地Ollama可无key, 有地址就试
        try:
            # 用 custom 槽自己的配置建 provider —— 旧 _build_provider 固定读 ocr 槽(本地Ollama),
            # 导致配了云端 custom 也照旧打本地 (2026-07-12 用户"不要调用本地"后揪出)。
            from app.services import ai_provider as _ai_mod
            prov = _ai_mod.build_provider(cfg)
            result = await asyncio.to_thread(
                v2.classify_ai, db, text=message, images=image_data,
                provider=prov, model=cfg.get("model") or "",
            )
            if result is None:
                ai_note = f"AI已调用但没返回可解析结果(超时/非JSON/或该模型不支持读图) — 当前定制报价AI: {_tgt}; 已回落规则解析"
        except Exception as e:  # noqa: BLE001
            result = None
            ai_note = f"AI调用失败({type(e).__name__}: {str(e)[:80]}) — 当前定制报价AI: {_tgt}; 已回落规则解析"
    else:
        ai_note = "AI 未配置(设置里没配定制报价AI), 走规则解析"
    if result is None:
        result = v2.classify(db, text=message, image_count=len(image_data))
    if ai_note:
        result["ai_note"] = ai_note    # 不再静默降级(2026-07-12: AI挂了页面空白连原因都不给)
    # 顶柜自动拆分提示合入 reasoning(前端已展示 reasoning, 无需改前端)
    _tc = result.get("top_cabinet_hint")
    if _tc and _tc not in (result.get("reasoning") or ""):
        result["reasoning"] = ((result.get("reasoning") or "") + "; " + _tc).lstrip("; ")

    # A6 尺寸合理性校验(防 1.5m 判成床头柜): 不合理→清空选定+降权, 交候选下拉手选纠正
    from app.services import custom_quote_config_service as ccfg
    result = v2.apply_size_sanity(db, ccfg.get_config(db), result)

    # 匹配产品 Top-10 候选 (带匹配度%): 匹配不一定对 → 前端下拉让用户手选纠正
    result["candidates"] = v2.product_candidates(
        db, message, matched_code=result.get("base_product_code"),
        matched_name=result.get("base_product_name"), matched_conf=result.get("confidence"),
        length_m=result.get("target_length_m"))
    if result.get("base_product_code"):
        result["sku_candidates"] = v2.sku_candidates(db, message, result["base_product_code"])

    _log_quote(db, source="v2_classify", user_message=message,
               ai_response=result.get("reasoning", ""),
               extra={k: result.get(k) for k in ("customization_type", "base_product_code", "confidence", "ai_used")})
    return result


@router.get("/v2/sku-candidates")
def v2_sku_candidates(
    product_code: str,
    text: str = "",
    db: Session = Depends(get_db),
) -> dict:
    """切换匹配产品时实时返回该产品的真实 SKU，避免沿用上一个产品的候选。"""
    from app.services import custom_quote_v2_service as v2

    items = v2.sku_candidates(db, text, product_code, limit=100)
    return {"product_code": product_code, "items": items}


@router.post("/v2/quote-light")
def v2_quote_light(payload: V2QuoteLightIn, db: Session = Depends(get_db)) -> dict:
    """普通定制报价: 真实SKU锚点价 + 尺寸/材质/增减部位 delta (0 AI, 纯算术)。"""
    from app.services import custom_quote_v2_service as v2
    r = v2.quote_light(
        db, base_product_code=payload.base_product_code,
        target_length_m=payload.target_length_m,
        target_width_cm=payload.target_width_cm, target_height_cm=payload.target_height_cm,
        target_material=payload.target_material,
        add_parts=payload.add_parts, remove_parts=payload.remove_parts,
        modify_parts=payload.modify_parts, price_tier=payload.price_tier,
        base_sku_code=payload.base_sku_code,
    )
    _log_quote(
        db, source="v2_quote_light",
        user_message=f"{payload.base_product_code} L={payload.target_length_m} 料={payload.target_material}",
        extra={k: r.get(k) for k in ("final_price", "anchor", "material_delta", "addremove_delta")},
    )
    return r


@router.post("/v2/quote-both")
def v2_quote_both(payload: V2QuoteLightIn, db: Session = Depends(get_db)) -> dict:
    """命中标准产品时并排两种口径: spec=按我们的规格(锚点) + custom=纯定制方向(板单引擎), 用户拍板选用。"""
    from app.services import custom_quote_v2_service as v2
    r = v2.quote_both(
        db, base_product_code=payload.base_product_code, category=payload.category,
        target_length_m=payload.target_length_m,
        target_width_cm=payload.target_width_cm, target_height_cm=payload.target_height_cm,
        target_material=payload.target_material,
        add_parts=payload.add_parts, remove_parts=payload.remove_parts,
        modify_parts=payload.modify_parts, price_tier=payload.price_tier,
        base_sku_code=payload.base_sku_code,
        lower_h_cm=payload.lower_cabinet_height_cm, description=payload.description or "",
    )
    _log_quote(
        db, source="v2_quote_both",
        user_message=f"{payload.base_product_code} L={payload.target_length_m} (双口径)",
        extra={"spec": (r.get("spec") or {}).get("final_price"),
               "custom": (r.get("custom") or {}).get("final_price")},
    )
    return r


@router.post("/v2/quote-heavy")
def v2_quote_heavy(payload: V2QuoteHeavyIn, db: Session = Depends(get_db)) -> dict:
    """特殊定制报价: 板单 → quote_from_spec 引擎 + 自动推五金。"""
    from app.services import custom_quote_v2_service as v2
    boards = [
        {"part": b.part, "material": b.material, "length_cm": b.length_cm,
         "width_cm": b.width_cm, "qty": b.qty, "unit": b.unit,
         "is_accessory": b.is_accessory, "is_drawer_rail": b.is_drawer_rail}
        for b in payload.boards
    ]
    try:
        r = v2.quote_heavy(
            db, product_type=payload.product_type, length_m=payload.length_m, boards=boards,
            overall_width_m=payload.overall_width_m, overall_height_m=payload.overall_height_m,
            auto_hardware=payload.auto_hardware,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"板单报价失败: {e}")
    _log_quote(db, source="v2_quote_heavy",
               user_message=f"{payload.product_type} L={payload.length_m}",
               extra={"final_price": r.get("final_price")})
    return r


@router.get("/v2/part-template")
def v2_part_template(category: str, db: Session = Depends(get_db)) -> dict:
    """品类部位模板 (从自有 BOM 聚合): 选品类带出标准部位骨架, 供特殊定制预填板单。"""
    from app.services import custom_quote_v2_service as v2
    return {"category": category, "parts": v2.suggest_part_template(db, category)}


@router.get("/v2/part-options")
def v2_part_options(category: str = "", db: Session = Depends(get_db)) -> dict:
    """A3 增减部位下拉数据源: 常用部位 + 品类BOM部位 + 物料表料名(替代手输, 防判错)。"""
    from app.services import custom_quote_v2_service as v2
    return v2.part_options(db, category=category)


@router.get("/v2/quote-logs")
def v2_quote_logs(limit: int = 50, db: Session = Depends(get_db)) -> dict:
    """定制报价留痕 (灰度对账): 最近 N 条 v2/旧报价记录, 供新旧口径对比复盘。"""
    from app.models.ai import AiChatLog
    rows = (db.query(AiChatLog)
            .filter(AiChatLog.action_type == "custom_quote")
            .order_by(AiChatLog.id.desc()).limit(min(max(limit, 1), 200)).all())
    return {"logs": [{
        "id": r.id,
        "source": (r.extra or {}).get("source"),
        "message": r.user_message,
        "extra": r.extra,
        "model": r.model,
        "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
    } for r in rows]}


class V2TemplateIn(BaseModel):
    category: str = Field(..., min_length=1)
    length_cm: float = Field(..., gt=0)
    depth_cm: Optional[float] = None
    height_cm: Optional[float] = None
    cols: Optional[int] = None
    drawers: Optional[int] = None
    doors: Optional[int] = None
    shelves: Optional[int] = None
    main_material: Optional[str] = None
    back_material: Optional[str] = None
    drawer_material: Optional[str] = None


@router.post("/v2/quote-from-template")
def v2_quote_from_template(payload: V2TemplateIn, db: Session = Depends(get_db)) -> dict:
    """品类 + 外形尺寸(长深高 cm) → 自动出板单 → 引擎报价 + 自动推五金。返回报价 + 生成的板单(前端可改后再走 /v2/quote-heavy)。"""
    from app.services import custom_board_template as tpl
    kw: dict = {}
    for k in ("depth_cm", "height_cm", "cols", "drawers", "doors", "shelves",
              "main_material", "back_material", "drawer_material"):
        v = getattr(payload, k)
        if v is not None:
            kw[k] = v
    try:
        r = tpl.quote_from_template(db, payload.category, payload.length_cm, **kw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"模板报价失败: {e}")
    _log_quote(db, source="v2_template",
               user_message=f"{payload.category} {payload.length_cm}cm",
               extra={k: r.get(k) for k in ("final_price", "factory_quote_compare", "wood_cost", "labor_fee")})
    return r
