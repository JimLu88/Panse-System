"""定价总表 API — 读取 + 录入 + 编辑 + 成本重算."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.pricing_formula import PricingFormulaRule
from app.services import pricing_calc_service
from app.services import formula_engine_service

router = APIRouter(prefix="/api/pricing-skus", tags=["pricing"])

# ---------------------------------------------------------------------------
# Formula rule router (separate prefix /api/pricing)
# ---------------------------------------------------------------------------
formula_router = APIRouter(prefix="/api/pricing", tags=["pricing-formula"])


class PricingSkuOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_code: str
    sku: Optional[str]
    sku_code: str
    size_category: Optional[str]
    list_price: Optional[Decimal]
    daily_price: Optional[Decimal]
    small_promo: Optional[Decimal]
    mid_promo: Optional[Decimal]
    big_promo: Optional[Decimal]
    big_promo_margin: Optional[Decimal]
    gross_margin_rate: Optional[Decimal]
    accounting_cost: Optional[Decimal]
    physical_cost: Optional[Decimal]
    platform_fee_rate: Optional[Decimal]
    tax: Optional[Decimal]
    image_url: Optional[str] = None
    logistics_cost: Optional[Decimal] = None
    install_cost: Optional[Decimal] = None
    factory_cost: Optional[Decimal] = None
    wood_cost: Optional[Decimal] = None
    packaging_cost: Optional[Decimal] = None
    external_parts_cost: Optional[Decimal] = None


class PricingSkuListOut(BaseModel):
    total: int
    items: list[PricingSkuOut]


class PricingSkuIn(BaseModel):
    product_code: str
    sku_code: str
    sku: Optional[str] = None
    size_category: Optional[str] = None
    list_price: Optional[Decimal] = None
    daily_price: Optional[Decimal] = None
    small_promo: Optional[Decimal] = None
    mid_promo: Optional[Decimal] = None
    big_promo: Optional[Decimal] = None
    accounting_cost: Optional[Decimal] = None
    physical_cost: Optional[Decimal] = None
    platform_fee_rate: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    image_url: Optional[str] = None
    logistics_cost: Optional[Decimal] = None
    install_cost: Optional[Decimal] = None
    factory_cost: Optional[Decimal] = None
    wood_cost: Optional[Decimal] = None
    packaging_cost: Optional[Decimal] = None
    external_parts_cost: Optional[Decimal] = None


class PricingSkuPatch(BaseModel):
    sku: Optional[str] = None
    size_category: Optional[str] = None
    list_price: Optional[Decimal] = None
    daily_price: Optional[Decimal] = None
    small_promo: Optional[Decimal] = None
    mid_promo: Optional[Decimal] = None
    big_promo: Optional[Decimal] = None
    accounting_cost: Optional[Decimal] = None
    physical_cost: Optional[Decimal] = None
    platform_fee_rate: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    image_url: Optional[str] = None
    logistics_cost: Optional[Decimal] = None
    install_cost: Optional[Decimal] = None
    factory_cost: Optional[Decimal] = None
    wood_cost: Optional[Decimal] = None
    packaging_cost: Optional[Decimal] = None
    external_parts_cost: Optional[Decimal] = None


@router.get("", response_model=PricingSkuListOut)
def list_pricing_skus(
    q: Optional[str] = Query(None, description="按 product_code / sku_code / sku 模糊搜"),
    size_category: Optional[str] = Query(None),
    product_code: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(PricingSku)
    count_stmt = select(func.count(PricingSku.id))
    filters = []
    if q:
        like = f"%{q.strip()}%"
        filters.append(or_(
            PricingSku.product_code.ilike(like),
            PricingSku.sku_code.ilike(like),
            PricingSku.sku.ilike(like),
        ))
    if size_category:
        filters.append(PricingSku.size_category == size_category)
    if product_code:
        filters.append(PricingSku.product_code == product_code)
    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)
    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(PricingSku.product_code, PricingSku.sku_code).limit(limit).offset(offset)
    ).scalars().all()
    return PricingSkuListOut(
        total=total,
        items=[PricingSkuOut.model_validate(r) for r in rows],
    )


@router.post("", response_model=PricingSkuOut, status_code=201)
def create_pricing_sku(
    body: PricingSkuIn,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    existing = db.query(PricingSku).filter(PricingSku.sku_code == body.sku_code).first()
    if existing:
        raise HTTPException(400, f"sku_code '{body.sku_code}' already exists")
    sku = PricingSku(**body.model_dump(exclude_none=True))
    pricing_calc_service.recompute(sku)
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return PricingSkuOut.model_validate(sku)


@router.patch("/{sku_id}", response_model=PricingSkuOut)
def update_pricing_sku(
    sku_id: int,
    body: PricingSkuPatch,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    sku = db.get(PricingSku, sku_id)
    if not sku:
        raise HTTPException(404, "Not found")
    changes = body.model_dump(exclude_unset=True)
    _record_price_changes(db, sku, changes, actor=getattr(_, "username", None))
    for k, v in changes.items():
        setattr(sku, k, v)
    pricing_calc_service.recompute(sku)
    db.commit()
    db.refresh(sku)
    return PricingSkuOut.model_validate(sku)


_TRACKED_PRICE_FIELDS = {
    "list_price", "daily_price", "accounting_cost", "physical_cost",
    "logistics_cost", "install_cost", "factory_cost", "wood_cost",
    "packaging_cost", "external_parts_cost",
}


def _record_price_changes(db: Session, sku: PricingSku, changes: dict, *, actor) -> None:
    """价格/成本字段变更留痕 (优化 #5)。"""
    from app.models.price_change import PriceChangeLog
    for k, v in changes.items():
        if k not in _TRACKED_PRICE_FIELDS:
            continue
        old = getattr(sku, k, None)
        if old == v:
            continue
        db.add(PriceChangeLog(
            sku_code=sku.sku_code, field=k,
            old_value=None if old is None else str(old),
            new_value=None if v is None else str(v),
            actor=actor,
        ))


@router.get("/{sku_code}/price-history")
def price_history(
    sku_code: str,
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """某 SKU 的价格/成本变更历史 (优化 #5): 谁何时把哪个字段从多少改成多少。"""
    from sqlalchemy import select as _select
    from app.models.price_change import PriceChangeLog
    rows = db.execute(
        _select(PriceChangeLog).where(PriceChangeLog.sku_code == sku_code)
        .order_by(PriceChangeLog.id.desc()).limit(limit)
    ).scalars().all()
    return [
        {"field": r.field, "old": r.old_value, "new": r.new_value,
         "actor": r.actor, "at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]


@router.post("/{sku_id}/recompute", response_model=PricingSkuOut)
def recompute_pricing_sku(
    sku_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    try:
        sku = pricing_calc_service.recompute_and_save(db, sku_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return PricingSkuOut.model_validate(sku)


# ---------------------------------------------------------------------------
# 比例参考 — 新产品录入「大促到手价」填写时给历史分布
#   口径: 比例 = 成本 / 大促到手价 (成本÷比例=到手价, 故比例=成本÷到手价)
#   会计 / 物理 / 出厂 三种成本各算一组分布; 优先按类目, 数据不足回退全局
# ---------------------------------------------------------------------------

# 成本字段 → 展示名 (前端按这个 key 回填: 到手价 = 该口径成本 / 比例)
_RATIO_CALIBERS = {
    "accounting": ("accounting_cost", "会计总成本"),
    "physical": ("physical_cost", "物理总成本"),
    "factory": ("factory_cost", "总出厂成本"),
}

_MIN_SAMPLE = 5   # 同类目样本少于此数回退全局


def _ratio_distribution(db: Session, cost_attr: str, category: Optional[str]):
    """算某成本口径下 比例=成本/大促到手价 的分布. 返回 (top3, range, sample, used_global)."""
    from app.models.product import Product

    cost_col = getattr(PricingSku, cost_attr)

    def _query(cat: Optional[str]):
        stmt = (
            select(cost_col, PricingSku.big_promo)
            .where(
                cost_col.isnot(None), cost_col > 0,
                PricingSku.big_promo.isnot(None), PricingSku.big_promo > 0,
            )
        )
        if cat:
            stmt = stmt.join(
                Product, Product.code == PricingSku.product_code
            ).where(Product.category == cat)
        return db.execute(stmt).all()

    used_global = False
    rows = _query(category) if category else []
    if len(rows) < _MIN_SAMPLE:
        rows = _query(None)
        used_global = True

    ratios = []
    for cost, price in rows:
        try:
            r = float(cost) / float(price)
        except (ZeroDivisionError, TypeError):
            continue
        if 0 < r < 5:                 # 过滤脏数据
            ratios.append(round(r, 2))   # 取到 1% 精度做众数桶
    sample = len(ratios)
    if sample == 0:
        return [], None, 0, used_global

    from collections import Counter
    counter = Counter(ratios)
    top = [
        {"ratio": val, "pct": round(cnt / sample * 100), "count": cnt}
        for val, cnt in counter.most_common(3)
    ]
    ratios.sort()
    # 中间 80% 区间 (p10–p90) 给「区间」展示
    lo = ratios[int(sample * 0.1)]
    hi = ratios[min(sample - 1, int(sample * 0.9))]
    rng = {"low": lo, "high": hi, "pct": 80}
    return top, rng, sample, used_global


@router.get("/ratio-hints", tags=["pricing"])
def ratio_hints(
    category: Optional[str] = Query(None, description="类目名 (如 卧室-床), 优先按类目统计"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """新产品录入填「大促到手价」时的历史比例参考.

    比例 = 成本 / 大促到手价。前端点某条 → 到手价 = 该口径成本 / 比例。
    会计/物理/出厂 三口径各给 Top3 + 区间; 同类目样本不足自动回退全局。
    """
    calibers: dict[str, dict] = {}
    for key, (attr, label) in _RATIO_CALIBERS.items():
        top, rng, sample, used_global = _ratio_distribution(db, attr, category)
        calibers[key] = {
            "label": label,
            "cost_field": attr,
            "sample": sample,
            "used_global": used_global,
            "top": top,
            "range": rng,
        }

    fields: dict[str, dict] = {}
    for name, (anchor_attr, label, mode) in _RATIO_FIELDS.items():
        top, rng, sample, used_global = _field_ratio_distribution(
            db, name, anchor_attr, category
        )
        fields[name] = {
            "anchor": anchor_attr,
            "anchor_label": label,
            "mode": mode,
            "sample": sample,
            "used_global": used_global,
            "top": top,
            "range": rng,
        }

    return {"category": category, "calibers": calibers, "fields": fields}


# ---------------------------------------------------------------------------
# 通用「智能基准」比例分布 — 各价格/成本字段相对某锚字段的历史比例
#   field -> (anchor_field, anchor_label, mode)  mode: "pct" 或 "multiplier"
# ---------------------------------------------------------------------------
_RATIO_FIELDS = {
    "list_price":      ("big_promo",  "大促到手价", "multiplier"),  # 标价是到手价的 N 倍
    "daily_price":     ("list_price", "标价",       "pct"),
    "small_promo":     ("list_price", "标价",       "pct"),
    "mid_promo":       ("list_price", "标价",       "pct"),
    "accounting_cost": ("big_promo",  "大促到手价", "pct"),
    "physical_cost":   ("big_promo",  "大促到手价", "pct"),
}


def _field_ratio_distribution(
    db: Session, field_attr: str, anchor_attr: str, category: Optional[str]
):
    """算 比例=field/anchor 的分布. 返回 (top3, range, sample, used_global)."""
    from app.models.product import Product

    field_col = getattr(PricingSku, field_attr)
    anchor_col = getattr(PricingSku, anchor_attr)

    def _query(cat: Optional[str]):
        stmt = (
            select(field_col, anchor_col)
            .where(
                field_col.isnot(None), field_col > 0,
                anchor_col.isnot(None), anchor_col > 0,
            )
        )
        if cat:
            stmt = stmt.join(
                Product, Product.code == PricingSku.product_code
            ).where(Product.category == cat)
        return db.execute(stmt).all()

    used_global = False
    rows = _query(category) if category else []
    if len(rows) < _MIN_SAMPLE:
        rows = _query(None)
        used_global = True

    ratios = []
    for fval, aval in rows:
        try:
            r = float(fval) / float(aval)
        except (ZeroDivisionError, TypeError):
            continue
        if 0 < r < 100:               # 过滤脏数据 (倍数口径可能 >1)
            ratios.append(round(r, 2))
    sample = len(ratios)
    if sample == 0:
        return [], None, 0, used_global

    from collections import Counter
    counter = Counter(ratios)
    top = [
        {"ratio": val, "pct": round(cnt / sample * 100), "count": cnt}
        for val, cnt in counter.most_common(3)
    ]
    ratios.sort()
    lo = ratios[int(sample * 0.1)]
    hi = ratios[min(sample - 1, int(sample * 0.9))]
    rng = {"low": lo, "high": hi, "pct": 80}
    return top, rng, sample, used_global


# ---------------------------------------------------------------------------
# 通用「常见值」分布 — 配件成本 / 活动价格 各字段历史录入值
# ---------------------------------------------------------------------------
def _numeric_field_whitelist(model) -> set[str]:
    """该 model 的数值列名集合 (防止属性注入)."""
    from sqlalchemy import Numeric, Integer, Float
    cols = set()
    for col in model.__table__.columns:
        if isinstance(col.type, (Numeric, Integer, Float)):
            cols.add(col.name)
    return cols


@router.get("/value-hints", tags=["pricing"])
def value_hints(
    table: str = Query(..., description="costs | promo"),
    field: str = Query(...),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """返回某扩展表某字段的历史「常见值」分布 (Top3 常见值 + 区间), 供配件成本/活动价格小灯泡参考."""
    model = {"costs": PricingSkuCosts, "promo": PricingSkuPromo}.get(table)
    if model is None:
        raise HTTPException(400, "table 必须是 costs 或 promo")
    whitelist = _numeric_field_whitelist(model)
    if field not in whitelist:
        raise HTTPException(400, f"字段 {field} 不允许")

    col = getattr(model, field)

    def _query(cat: Optional[str]):
        stmt = select(col).where(col.isnot(None), col > 0)
        if cat:
            from app.models.product import Product
            stmt = (
                stmt.join(PricingSku, PricingSku.sku_code == model.sku_code)
                .join(Product, Product.code == PricingSku.product_code)
                .where(Product.category == cat)
            )
        return [row[0] for row in db.execute(stmt).all()]

    used_global = False
    vals = _query(category) if category else []
    if len(vals) < _MIN_SAMPLE:
        vals = _query(None)
        used_global = True

    # 是否系数 (Numeric(10,6)) — 系数保留原值, 货币圆整 2 位
    is_rate = field.endswith("_rate") or field.endswith("_discount")
    nums = []
    for v in vals:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        nums.append(f if is_rate else round(f, 2))
    sample = len(nums)
    if sample == 0:
        return {"sample": 0, "used_global": used_global, "top": [], "range": None}

    from collections import Counter
    counter = Counter(nums)
    top = [
        {"value": val, "pct": round(cnt / sample * 100), "count": cnt}
        for val, cnt in counter.most_common(3)
    ]
    nums.sort()
    lo = nums[int(sample * 0.1)]
    hi = nums[min(sample - 1, int(sample * 0.9))]
    rng = {"low": lo, "high": hi, "pct": 80}
    return {"sample": sample, "used_global": used_global, "top": top, "range": rng}


# ---------------------------------------------------------------------------
# 淘宝批量操作模板下载 — 一键模板按钮用
# ---------------------------------------------------------------------------

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets" / "taobao_templates"

# key: 文件名(不含路径), label/desc: 给前端展示
_TEMPLATES: list[dict[str, str]] = [
    {
        "key": "product_publish.xlsx",
        "label": "产品批量发布模板",
        "desc": "宝贝标题 / 商家编码 / 一口价 / SKU 价格批量发布",
    },
    {
        "key": "single_item_discount.xlsx",
        "label": "单品立减批量模板",
        "desc": "按 商品ID + SKU_ID 设置立减 / 打折优惠",
    },
    {
        "key": "promo_signup.xlsx",
        "label": "大促活动批量报名模板",
        "desc": "按商品ID报名大促 / 联报活动 (含一口价、大促价参考)",
    },
    {
        "key": "product_id_export.xlsx",
        "label": "商品ID导入模板",
        "desc": "仅商品ID一列, 用于批量勾选 / 导出商品",
    },
    {
        "key": "product_export_mapping.xlsx",
        "label": "淘宝商品导出对应表",
        "desc": "商品Id / 宝贝标题 / 商家编码 等字段对应关系",
    },
]


@router.get("/templates", tags=["pricing"])
def list_pricing_templates():
    """列出可下载的淘宝批量操作模板."""
    return [
        {"key": t["key"], "label": t["label"], "desc": t["desc"]}
        for t in _TEMPLATES
        if (_TEMPLATE_DIR / t["key"]).exists()
    ]


@router.get("/templates/{key}/download", tags=["pricing"])
def download_pricing_template(key: str):
    """下载指定模板. key 必须在白名单内, 防目录穿越."""
    meta = next((t for t in _TEMPLATES if t["key"] == key), None)
    if meta is None:
        raise HTTPException(404, "template not found")
    path = _TEMPLATE_DIR / key
    if not path.exists():
        raise HTTPException(404, "template file missing")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{meta['label']}.xlsx",
    )


# ---------------------------------------------------------------------------
# 配件成本拆分 — /api/pricing-skus/{sku_code}/costs
# ---------------------------------------------------------------------------

class PricingSkuCostsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku_code: str
    rock_slab: Optional[Decimal] = None
    drawer_rail: Optional[Decimal] = None
    led_strip: Optional[Decimal] = None
    glass: Optional[Decimal] = None
    electric_rail: Optional[Decimal] = None
    packing_sheet: Optional[Decimal] = None
    iron_pin: Optional[Decimal] = None
    connector: Optional[Decimal] = None
    aluminum_rail: Optional[Decimal] = None
    plastic_rail: Optional[Decimal] = None
    mini_handle: Optional[Decimal] = None
    nail_free_glue: Optional[Decimal] = None
    engraving: Optional[Decimal] = None
    acrylic_strip: Optional[Decimal] = None
    embedded_sleeve: Optional[Decimal] = None
    cable_mgmt: Optional[Decimal] = None
    back_panel: Optional[Decimal] = None
    stainless_trim: Optional[Decimal] = None
    leg: Optional[Decimal] = None
    soft_pack: Optional[Decimal] = None
    bed_board: Optional[Decimal] = None
    other_cost: Optional[Decimal] = None
    other_desc: Optional[str] = None
    parts_remark: Optional[str] = None


class PricingSkuCostsIn(BaseModel):
    rock_slab: Optional[Decimal] = None
    drawer_rail: Optional[Decimal] = None
    led_strip: Optional[Decimal] = None
    glass: Optional[Decimal] = None
    electric_rail: Optional[Decimal] = None
    packing_sheet: Optional[Decimal] = None
    iron_pin: Optional[Decimal] = None
    connector: Optional[Decimal] = None
    aluminum_rail: Optional[Decimal] = None
    plastic_rail: Optional[Decimal] = None
    mini_handle: Optional[Decimal] = None
    nail_free_glue: Optional[Decimal] = None
    engraving: Optional[Decimal] = None
    acrylic_strip: Optional[Decimal] = None
    embedded_sleeve: Optional[Decimal] = None
    cable_mgmt: Optional[Decimal] = None
    back_panel: Optional[Decimal] = None
    stainless_trim: Optional[Decimal] = None
    leg: Optional[Decimal] = None
    soft_pack: Optional[Decimal] = None
    bed_board: Optional[Decimal] = None
    other_cost: Optional[Decimal] = None
    other_desc: Optional[str] = None
    parts_remark: Optional[str] = None


def _get_sku_or_404(db: Session, sku_code: str) -> PricingSku:
    sku = db.query(PricingSku).filter(PricingSku.sku_code == sku_code).first()
    if not sku:
        raise HTTPException(404, f"PricingSku '{sku_code}' not found")
    return sku


@router.get("/{sku_code}/costs", response_model=PricingSkuCostsOut)
def get_sku_costs(
    sku_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    row = db.query(PricingSkuCosts).filter(PricingSkuCosts.sku_code == sku_code).first()
    if not row:
        raise HTTPException(404, f"No costs record for '{sku_code}'")
    return PricingSkuCostsOut.model_validate(row)


@router.post("/{sku_code}/costs", response_model=PricingSkuCostsOut, status_code=201)
def create_sku_costs(
    sku_code: str,
    body: PricingSkuCostsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    existing = db.query(PricingSkuCosts).filter(PricingSkuCosts.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"costs record for '{sku_code}' already exists, use PATCH")
    sku = _get_sku_or_404(db, sku_code)
    costs = PricingSkuCosts(sku_code=sku_code, **body.model_dump(exclude_none=True))
    db.add(costs)
    pricing_calc_service.recompute_costs(costs, sku)
    pricing_calc_service.recompute(sku)
    db.commit()
    db.refresh(costs)
    return PricingSkuCostsOut.model_validate(costs)


@router.patch("/{sku_code}/costs", response_model=PricingSkuCostsOut)
def upsert_sku_costs(
    sku_code: str,
    body: PricingSkuCostsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    sku = _get_sku_or_404(db, sku_code)
    costs = db.query(PricingSkuCosts).filter(PricingSkuCosts.sku_code == sku_code).first()
    if not costs:
        costs = PricingSkuCosts(sku_code=sku_code)
        db.add(costs)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(costs, k, v)
    pricing_calc_service.recompute_costs(costs, sku)
    pricing_calc_service.recompute(sku)
    db.commit()
    db.refresh(costs)
    return PricingSkuCostsOut.model_validate(costs)


# ---------------------------------------------------------------------------
# 活动价格表 — /api/pricing-skus/{sku_code}/promo
# ---------------------------------------------------------------------------

class PricingSkuPromoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    sku_code: str
    taobao_item_id: Optional[str] = None
    taobao_sku_id: Optional[str] = None
    taobao_activity_price: Optional[Decimal] = None
    shop_promo_rate: Optional[Decimal] = None
    shop_internal_promo: Optional[Decimal] = None
    shop_internal_final: Optional[Decimal] = None
    mid_shop_rate: Optional[Decimal] = None
    mid_buyer_price: Optional[Decimal] = None
    mid_shop_receipt: Optional[Decimal] = None
    mid_vip_final: Optional[Decimal] = None
    big_shop_rate: Optional[Decimal] = None
    big_buyer_price: Optional[Decimal] = None
    big_shop_receipt: Optional[Decimal] = None
    big_vip_final: Optional[Decimal] = None
    xhs_item_id: Optional[str] = None
    xhs_sku_name: Optional[str] = None
    xhs_sku_id: Optional[str] = None
    xhs_list_price: Optional[Decimal] = None
    xhs_activity_price: Optional[Decimal] = None
    xhs_promo_discount: Optional[Decimal] = None
    xhs_promo_price: Optional[Decimal] = None


class PricingSkuPromoIn(BaseModel):
    taobao_item_id: Optional[str] = None
    taobao_sku_id: Optional[str] = None
    shop_promo_rate: Optional[Decimal] = None
    shop_internal_promo: Optional[Decimal] = None
    mid_shop_rate: Optional[Decimal] = None
    big_shop_rate: Optional[Decimal] = None
    xhs_item_id: Optional[str] = None
    xhs_sku_name: Optional[str] = None
    xhs_sku_id: Optional[str] = None
    xhs_activity_price: Optional[Decimal] = None
    xhs_promo_discount: Optional[Decimal] = None


@router.get("/{sku_code}/promo", response_model=PricingSkuPromoOut)
def get_sku_promo(
    sku_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    row = db.query(PricingSkuPromo).filter(PricingSkuPromo.sku_code == sku_code).first()
    if not row:
        raise HTTPException(404, f"No promo record for '{sku_code}'")
    return PricingSkuPromoOut.model_validate(row)


@router.post("/{sku_code}/promo", response_model=PricingSkuPromoOut, status_code=201)
def create_sku_promo(
    sku_code: str,
    body: PricingSkuPromoIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    existing = db.query(PricingSkuPromo).filter(PricingSkuPromo.sku_code == sku_code).first()
    if existing:
        raise HTTPException(400, f"promo record for '{sku_code}' already exists, use PATCH")
    sku = _get_sku_or_404(db, sku_code)
    promo = PricingSkuPromo(sku_code=sku_code, **body.model_dump(exclude_none=True))
    db.add(promo)
    pricing_calc_service.recompute_promo(promo, sku)
    pricing_calc_service.recompute(sku)
    db.commit()
    db.refresh(promo)
    return PricingSkuPromoOut.model_validate(promo)


@router.patch("/{sku_code}/promo", response_model=PricingSkuPromoOut)
def upsert_sku_promo(
    sku_code: str,
    body: PricingSkuPromoIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    sku = _get_sku_or_404(db, sku_code)
    promo = db.query(PricingSkuPromo).filter(PricingSkuPromo.sku_code == sku_code).first()
    if not promo:
        promo = PricingSkuPromo(sku_code=sku_code)
        db.add(promo)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(promo, k, v)
    pricing_calc_service.recompute_promo(promo, sku)
    pricing_calc_service.recompute(sku)
    db.commit()
    db.refresh(promo)
    return PricingSkuPromoOut.model_validate(promo)


# ===========================================================================
# Formula Rule CRUD endpoints
# ===========================================================================

class FormulaRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    field_name: str
    display_name: Optional[str]
    expression: str
    description: Optional[str]
    enabled: bool
    sort_order: int
    is_builtin: bool


class FormulaRulePatch(BaseModel):
    expression: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    sort_order: Optional[int] = None


class FormulaValidateIn(BaseModel):
    expression: str
    sample_values: Optional[dict[str, float]] = None


class FormulaValidateOut(BaseModel):
    ok: bool
    error: Optional[str] = None
    detected_inputs: list[str] = []
    sample_result: Optional[float] = None


@formula_router.get("/formula-rules", response_model=list[FormulaRuleOut])
def list_formula_rules(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return db.query(PricingFormulaRule).order_by(PricingFormulaRule.sort_order).all()


@formula_router.put("/formula-rules/{rule_id}", response_model=FormulaRuleOut)
def update_formula_rule(
    rule_id: int,
    body: FormulaRulePatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    rule = db.get(PricingFormulaRule, rule_id)
    if not rule:
        raise HTTPException(404, "公式规则不存在")
    updates = body.model_dump(exclude_unset=True)
    if "expression" in updates:
        try:
            formula_engine_service.eval_safe(updates["expression"], {})
        except ValueError:
            pass  # May fail due to missing fields; just check syntax
        try:
            import ast
            ast.parse(updates["expression"], mode="eval")
        except SyntaxError as e:
            raise HTTPException(422, f"公式语法错误: {e}")
    for k, v in updates.items():
        setattr(rule, k, v)
    db.commit()
    db.refresh(rule)
    return rule


@formula_router.post("/formula-rules/validate", response_model=FormulaValidateOut)
def validate_formula(
    body: FormulaValidateIn,
    _: User = Depends(get_current_user),
):
    import ast as _ast
    try:
        _ast.parse(body.expression, mode="eval")
    except SyntaxError as e:
        return FormulaValidateOut(ok=False, error=f"语法错误: {e}")

    inputs = formula_engine_service.extract_field_names(body.expression)
    result = None
    if body.sample_values:
        from decimal import Decimal as D
        ctx = {k: D(str(v)) for k, v in body.sample_values.items()}
        try:
            val = formula_engine_service.eval_safe(body.expression, ctx)
            result = float(val) if val is not None else None
        except Exception as e:
            return FormulaValidateOut(ok=False, error=str(e), detected_inputs=inputs)

    return FormulaValidateOut(ok=True, detected_inputs=inputs, sample_result=result)


@formula_router.post("/formula-rules/seed")
def seed_formula_rules(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    count = formula_engine_service.seed_builtin_rules(db)
    return {"inserted": count, "message": f"已插入 {count} 条内置公式规则"}


@formula_router.post("/recompute-all")
def recompute_all_skus(
    force: bool = Query(False, description="强制覆盖已有值"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    rules = (
        db.query(PricingFormulaRule)
        .filter(PricingFormulaRule.enabled.is_(True))
        .all()
    )
    skus = db.query(PricingSku).all()
    updated = 0
    for sku in skus:
        costs = db.query(PricingSkuCosts).filter(PricingSkuCosts.sku_code == sku.sku_code).first()
        promo = db.query(PricingSkuPromo).filter(PricingSkuPromo.sku_code == sku.sku_code).first()
        formula_engine_service.compute_all(db, sku, costs, promo, rules=rules, force=force)
        updated += 1
    db.commit()
    return {"updated": updated, "message": f"已重算 {updated} 个 SKU"}
