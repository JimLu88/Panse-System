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
    competitor_coupon_rate: Optional[float] = None
    projection_type: Optional[str] = None
    projection_rate: Optional[float] = None
    packing: Optional[list[float]] = None
    freight: Optional[list[float]] = None
    install: Optional[list[float]] = None
    labor: Optional[dict[str, list[float]]] = None
    size_rules: Optional[dict[str, list[float]]] = None
    prices: Optional[dict[str, float]] = None


def _validate_quote_config(patch: dict) -> None:
    """报价参数范围校验, 防误填把全公司报价带歪。"""
    def _rate(k: str, lo: float, hi: float) -> None:
        if patch.get(k) is not None and not (lo <= float(patch[k]) <= hi):
            raise HTTPException(400, f"{k} 必须在 {lo}~{hi} 之间")
    _rate("factory_profit_rate", 0, 0.95)
    _rate("panse_profit_rate", 0, 0.95)
    _rate("competitor_coupon_rate", 0, 0.95)
    _rate("safety_rate", 1.0, 3.0)
    if patch.get("projection_rate") is not None and float(patch["projection_rate"]) <= 0:
        raise HTTPException(400, "projection_rate 必须 > 0")
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

# -------- 定制报价 AI 对话 (统一入口: 全定制 + 微定制 + 截图识别) -------- #

_CHAT_SONNET = "claude-sonnet-4-6"
_CHAT_OPUS = "claude-opus-4-8"

_CHAT_SYSTEM = """\
你是「畔色孚格 ERP」的定制报价顾问。用户用文字或图片描述想要的产品/改造需求。

【系统计价口径 — 你估价/解释必须按这个逻辑, 不要凭感觉】
- 标准款: 直接用下方目录里该 SKU 的「日常价」。
- 微定制(已有产品改尺寸/材质): **第一步永远是先在目录里找「目标材质或目标尺寸」的现成 SKU**,
  找到就用它的日常价(这是最准的)。找不到才在基础 SKU 价上调整: 材质升级按两材料价差估溢价
  (经验: 榉木→樱桃木约 +10~20%, →黑胡桃约 +30~50%); 尺寸放大按比例小幅上浮。
- 全定制(全新规格, 目录里没有同款): 逐板成本管线 =
  Σ板成本(材料单价×面积) + 人工费 → 厂内成本 → ×1.25(工厂利润) → +配件+打包+运费+安装
  → ×1.05(保守系数) → ÷0.85(畔色毛利) = 最终报价。
  你给不出精确板单时, 用同品类现成产品的价格区间给范围, 并建议走「AI拆板单 → 板单报价」出精确价。

你的任务(按序):
1. 理解需求: 产品类型/尺寸/材质/数量/特殊要求
2. 判类型: 微定制(目录里找得到基础或同款 SKU) / 全定制(找不到)
3. 报价方向: 严格按上面口径; 微定制必须引用具体 SKU 编码及其日常价, 不要拿不相干品类(如样块/茶几)凑数
4. 下一步操作建议

回答格式(严格遵守, 每段独占一行):
【需求理解】<2-3句>
【定制类型】<只写「全定制」或「微定制」其一>
【参考价格】<价格区间或具体 SKU 日常价, 并说明依据>
【推荐操作】<1-3 步, 每步动词开头>
【参考 SKU】<SKU编码(微定制时) 或 暂无匹配>

现有产品定价数据(供参考):
{pricing_context}
"""


def _build_pricing_context(db: Session, query: str = "") -> str:
    """喂给定制报价 AI 的产品定价上下文。

    旧版只取前 200 SKU / 前 30 产品 → AI 看不到全部品类(如床头柜排编码后段被截掉),
    误判"暂无X品类"乱估。改为: ①永远给【全部产品目录】(按类目, 每行 编码+名称+价格区间);
    ②对【与需求同类目】的产品再给 SKU/尺寸/价格明细。这样 AI 既知道有哪些品类, 又有相关品的细价。
    """
    from sqlalchemy import func, select
    from app.models.pricing import PricingSku
    from app.models.product import Product

    products = db.execute(select(Product).order_by(Product.category, Product.code)).scalars().all()
    if not products:
        return "（暂无产品数据）"
    agg = db.execute(
        select(PricingSku.product_code, func.min(PricingSku.daily_price),
               func.max(PricingSku.daily_price), func.count())
        .group_by(PricingSku.product_code)
    ).all()
    price_map = {r[0]: (r[1], r[2], r[3]) for r in agg}

    def _rng(code: str):
        mn, mx, n = price_map.get(code, (None, None, 0))
        if mn is None:
            return "无定价", 0
        if mn == mx:
            return f"日常价¥{float(mn):.0f}", n
        return f"日常价¥{float(mn):.0f}-{float(mx):.0f}", n

    lines = ["【全部产品目录(按类目; 每行: 编码 名称 (价格区间, SKU数))】"]
    cur = object()
    for p in products[:200]:   # token 预算: 产品过多时只列前200(与需求同类目的明细在下方补)
        if p.category != cur:
            cur = p.category
            lines.append(f"\n# 类目: {cur or '未分类'}")
        rng, n = _rng(p.code)
        lines.append(f"  {p.code} {p.name} ({rng}, {n}SKU)")
    if len(products) > 200:
        lines.append(f"\n…(共 {len(products)} 个产品, 上方列前 200; 与本次需求同类目的见下方明细)")

    # 与需求同类目(类目末段词出现在用户描述里)的产品 → 给 SKU/尺寸/价格明细
    q = query or ""
    rel_cats = {p.category for p in products
                if p.category and p.category.split("-")[-1] in q}
    # 类目末段越具体(越长)越优先, 防"柜"这种泛词把床头柜挤出截断上限
    relevant = sorted(
        [p for p in products if p.category in rel_cats],
        key=lambda p: -len((p.category or "").split("-")[-1]),
    )[:20]
    if relevant:
        lines.append("\n【与本次需求同类目的产品 — SKU/尺寸/价格明细】")
        nm = {p.code: p.name for p in relevant}
        skus = db.execute(
            select(PricingSku.product_code, PricingSku.sku_code, PricingSku.sku,
                   PricingSku.size_category, PricingSku.daily_price, PricingSku.list_price)
            .where(PricingSku.product_code.in_([p.code for p in relevant]))
            .order_by(PricingSku.product_code, PricingSku.sku_code)
        ).all()
        cur = object()
        for r in skus:
            if r.product_code != cur:
                cur = r.product_code
                lines.append(f"\n产品 {r.product_code} {nm.get(r.product_code, '')}:")
            price = f"¥{r.daily_price:.0f}" if r.daily_price else (f"¥{r.list_price:.0f}" if r.list_price else "—")
            lines.append(f"  {r.sku_code} [{r.sku or ''}] {r.size_category or ''} 日常价{price}")
    return "\n".join(lines)


class AiChatOut(BaseModel):
    text: str
    route_type: str  # "full_custom" | "micro_custom" | "unknown"
    suggested_sku: Optional[str] = None
    model: str
    ai_used: bool
    error: Optional[str] = None


@router.post("/ai-chat", response_model=AiChatOut)
async def ai_chat(
    message: str = Form(""),
    model_pref: str = Form("sonnet"),
    images: List[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    """定制报价 AI 对话：文字 + 最多5张图 → AI 分析并给出定制类型和报价方向。"""
    from app.services import settings_service
    from app.services import ai_provider as ai_mod
    from app.services.ai_provider import AiUnavailable

    if len(images) > 5:
        raise HTTPException(400, "最多上传5张图片")

    target_model = _CHAT_OPUS if model_pref == "opus" else _CHAT_SONNET
    pricing_ctx = await asyncio.to_thread(_build_pricing_context, db, message)
    system = _CHAT_SYSTEM.format(pricing_context=pricing_ctx)

    cfg = settings_service.get_ai_config(db, "ocr")
    if not cfg.get("api_key"):
        return AiChatOut(
            text="AI 未配置，请在后台管理 → AI 设置 中填写 API Key。",
            route_type="unknown", model="none", ai_used=False,
            error="AI 未配置",
        )

    cfg = {**cfg, "model": target_model}
    provider = ai_mod.build_provider(cfg)

    image_data: list[tuple[bytes, str]] = []
    for img in images:
        raw = await img.read()
        image_data.append((raw, img.content_type or "image/jpeg"))

    user_msg = message.strip() or "（用户上传了图片，请分析定制需求）"

    try:
        if image_data:
            resp = await asyncio.to_thread(
                provider.chat_with_images,
                system=system, user=user_msg, images=image_data, max_tokens=1500,
            )
        else:
            resp = await asyncio.to_thread(
                provider.chat, system=system, user=user_msg, max_tokens=1500,
            )
    except AiUnavailable as e:
        return AiChatOut(
            text=str(e), route_type="unknown", model=target_model,
            ai_used=False, error=str(e),
        )

    text = resp.text
    route_type = "unknown"
    suggested_sku = None
    import re as _re
    # 只解析【定制类型】那一行的值, 不再全文 substring(避免AI说"这不是微定制"被误判)
    _tm = _re.search(r"【定制类型】\s*([^\n]+)", text)
    _tval = _tm.group(1) if _tm else ""
    if "微定制" in _tval:
        route_type = "micro_custom"
    elif "全定制" in _tval:
        route_type = "full_custom"

    m = _re.search(r"【参考 SKU】\s*([A-Z0-9\-]+)", text)
    if m and m.group(1) != "暂无匹配":
        suggested_sku = m.group(1)

    _log_quote(db, source="ai_chat", user_message=user_msg, ai_response=text,
               model=resp.model, extra={"route_type": route_type, "suggested_sku": suggested_sku})
    return AiChatOut(
        text=text, route_type=route_type, suggested_sku=suggested_sku,
        model=resp.model, ai_used=True,
    )


# ───────────────────────── 定制报价 v2 (理顺 + 提速) ─────────────────────────
# 一个分类器前门 + 两条确定性管道 (见 docs/定制报价v2_理顺提速_落地方案.md)。
# 全部新增端点, 与旧端点影子并行(旧端点不动); 普通定制 0 次额外 AI, 纯算术。


class V2ClassifyIn(BaseModel):
    text: str = Field("", description="客户定制描述")
    image_count: int = 0


class V2QuoteLightIn(BaseModel):
    base_product_code: str = Field(..., min_length=1)
    target_length_m: Optional[float] = None
    target_material: Optional[str] = None
    add_parts: list[dict] = Field(default_factory=list)
    remove_parts: list[dict] = Field(default_factory=list)
    price_tier: str = "daily"


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

    image_data: list[tuple[bytes, str]] = []
    for img in (images or [])[:5]:
        raw = await img.read()
        image_data.append((raw, img.content_type or "image/jpeg"))

    result = None
    cfg = settings_service.get_ai_config(db, "custom")  # 定制报价专属槽; 没配回落 ocr
    if cfg.get("api_key"):
        try:
            prov = customization_ai_service._build_provider(db)
            result = await asyncio.to_thread(
                v2.classify_ai, db, text=message, images=image_data,
                provider=prov, model=cfg.get("model") or "",
            )
        except Exception:  # noqa: BLE001
            result = None
    if result is None:
        result = v2.classify(db, text=message, image_count=len(image_data))

    _log_quote(db, source="v2_classify", user_message=message,
               ai_response=result.get("reasoning", ""),
               extra={k: result.get(k) for k in ("customization_type", "base_product_code", "confidence", "ai_used")})
    return result


@router.post("/v2/quote-light")
def v2_quote_light(payload: V2QuoteLightIn, db: Session = Depends(get_db)) -> dict:
    """普通定制报价: 真实SKU锚点价 + 尺寸/材质/增减部位 delta (0 AI, 纯算术)。"""
    from app.services import custom_quote_v2_service as v2
    r = v2.quote_light(
        db, base_product_code=payload.base_product_code,
        target_length_m=payload.target_length_m, target_material=payload.target_material,
        add_parts=payload.add_parts, remove_parts=payload.remove_parts,
        price_tier=payload.price_tier,
    )
    _log_quote(
        db, source="v2_quote_light",
        user_message=f"{payload.base_product_code} L={payload.target_length_m} 料={payload.target_material}",
        extra={k: r.get(k) for k in ("final_price", "anchor", "material_delta", "addremove_delta")},
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
