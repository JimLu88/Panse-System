"""定价总表 API — 读取 + 录入 + 编辑 + 成本重算."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.pricing_custom import PricingCustomField, PricingCustomValue
from app.models.pricing_formula import PricingFormulaRule
from app.services import pricing_calc_service
from app.services import formula_engine_service

router = APIRouter(prefix="/api/pricing-skus", tags=["pricing"])

# ---------------------------------------------------------------------------
# Formula rule router (separate prefix /api/pricing)
# ---------------------------------------------------------------------------
formula_router = APIRouter(prefix="/api/pricing", tags=["pricing-formula"])


class PricingSkuOut(BaseModel):
    # extra="allow": 列表接口会把配件成本(costs)、活动价(promo)、自定义列(cf_<id>)
    # 平铺合并进来一起返回, 这些"额外字段"需要透传给前端做可选列。
    model_config = ConfigDict(from_attributes=True, extra="allow")
    id: int
    product_code: str
    sku: Optional[str]
    sku_code: str
    taobao_title: Optional[str] = None
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
    taobao_title: Optional[str] = None
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
    taobao_title: Optional[str] = None
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
    # 成本加成基数 (改系数): 填了 → recompute 按 各档价=ROUNDUP(物理÷(1−2.6%)÷基数,−1) 联动派生。
    base_list: Optional[Decimal] = None
    base_small: Optional[Decimal] = None
    base_mid: Optional[Decimal] = None
    base_big: Optional[Decimal] = None


# 档价 → 该档基数: 手动直接改档价(没同时改基数) → 清该档基数, 让 recompute 不再派生此档(手动值锁定)。
# 改基数(base_*)则相反: recompute 按基数派生该档价(联动)。二者互斥, 由前端"手动改值/改系数"决定发哪个。
_TIER_TO_BASE = {
    "list_price": "base_list", "daily_price": "base_list",
    "small_promo": "base_small", "mid_promo": "base_mid", "big_promo": "base_big",
}


_EXT_SKIP = {"id", "sku_code", "created_at", "updated_at"}


def _ext_dict(obj) -> dict:
    """扩展表(costs/promo) ORM 对象 → {列: 值}, 跳过主键/外键/时间戳。"""
    if obj is None:
        return {}
    return {c.key: getattr(obj, c.key) for c in obj.__table__.columns if c.key not in _EXT_SKIP}


@router.get("", response_model=PricingSkuListOut)
def list_pricing_skus(
    q: Optional[str] = Query(None, description="按 product_code / sku_code / sku 模糊搜"),
    size_category: Optional[str] = Query(None),
    category: Optional[str] = Query(None, description="按产品类目筛 (join 产品总表 category)"),
    product_code: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    from app.models.product import Product
    stmt = select(PricingSku)
    count_stmt = select(func.count(PricingSku.id))
    if category:
        stmt = stmt.join(Product, Product.code == PricingSku.product_code)
        count_stmt = count_stmt.join(Product, Product.code == PricingSku.product_code)
    filters = []
    if q:
        # 全站统一模糊搜索: 空格分词 + SKU名/产品名 字符间隙 ("榉木餐桌"中"榉木岩板餐桌")
        from app.services.fuzzy_search import fuzzy_clause
        fc = fuzzy_clause(q, like_cols=[
            PricingSku.product_code, PricingSku.sku_code,
            PricingSku.sku, PricingSku.product_name,
        ], gap_cols=[PricingSku.sku, PricingSku.product_name])
        if fc is not None:
            filters.append(fc)
    if size_category:
        filters.append(PricingSku.size_category == size_category)
    if category:
        filters.append(Product.category == category)
    if product_code:
        filters.append(PricingSku.product_code == product_code)
    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)
    total = db.execute(count_stmt).scalar_one()
    rows = db.execute(
        stmt.order_by(PricingSku.product_code, PricingSku.sku_code).limit(limit).offset(offset)
    ).scalars().all()

    # 平铺合并: 配件成本(costs) / 活动价(promo) / 自定义列(cf_<id>) 一起返回, 供前端做可选列。
    sku_codes = [r.sku_code for r in rows if r.sku_code]
    costs_map: dict[str, PricingSkuCosts] = {}
    promo_map: dict[str, PricingSkuPromo] = {}
    cv_map: dict[str, dict[int, PricingCustomValue]] = {}
    if sku_codes:
        for cr in db.execute(select(PricingSkuCosts).where(PricingSkuCosts.sku_code.in_(sku_codes))).scalars():
            costs_map[cr.sku_code] = cr
        for pr in db.execute(select(PricingSkuPromo).where(PricingSkuPromo.sku_code.in_(sku_codes))).scalars():
            promo_map[pr.sku_code] = pr
        for cv in db.execute(select(PricingCustomValue).where(PricingCustomValue.sku_code.in_(sku_codes))).scalars():
            cv_map.setdefault(cv.sku_code, {})[cv.field_id] = cv
    custom_fields = db.execute(
        select(PricingCustomField).order_by(PricingCustomField.sort_order, PricingCustomField.id)
    ).scalars().all()

    # SKU 图片图库优先 (用户拍板 2026-06-12): 图库有就用图库的, 前端没有才回退淘宝 image_url
    from app.services.gallery_lookup import sku_gallery_url_map
    gallery_urls = sku_gallery_url_map(
        [(r.product_code, r.sku_code, r.sku) for r in rows])

    items = []
    for r in rows:
        base = PricingSkuOut.model_validate(r).model_dump()
        base.update(_ext_dict(costs_map.get(r.sku_code)))
        base.update(_ext_dict(promo_map.get(r.sku_code)))
        base["gallery_image_url"] = gallery_urls.get(r.sku_code)
        cvs = cv_map.get(r.sku_code, {})
        for fdef in custom_fields:
            v = cvs.get(fdef.id)
            base[f"cf_{fdef.id}"] = None if v is None else (
                v.num_value if fdef.value_kind == "number" else v.text_value
            )
        items.append(PricingSkuOut.model_validate(base))
    return PricingSkuListOut(total=total, items=items)


class ByProductPatch(BaseModel):
    """一键覆盖同产品全部 SKU: 三段可选 — 主表字段 / 配件成本 / 渠道系数。"""
    sku: Optional[dict] = None      # PricingSku 字段 (价格/成本)
    costs: Optional[dict] = None    # PricingSkuCosts 字段 (22 配件)
    promo: Optional[dict] = None    # PricingSkuPromo 字段 (渠道价/系数)


@router.patch("/by-product/{product_code}", response_model=dict)
def update_pricing_by_product(
    product_code: str,
    body: ByProductPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """编辑器「保存并覆盖同产品全部 SKU」: 把给定字段铺到该产品所有 SKU + 重算 + 留痕。"""
    from app.services import field_change_service
    skus = db.query(PricingSku).filter(PricingSku.product_code == product_code).all()
    if not skus:
        raise HTTPException(404, f"产品 {product_code} 没有定价行")
    actor = getattr(_, "username", None)
    updated = 0
    for sku in skus:
        if body.sku:
            _record_price_changes(db, sku, body.sku, actor=actor)
            for k, v in body.sku.items():
                if hasattr(sku, k):
                    setattr(sku, k, v)
            if "factory_cost" in body.sku:
                sku.factory_cost_override = True   # 覆盖全产品时手填工厂成本 → 标覆盖
        if body.costs:
            costs = db.query(PricingSkuCosts).filter(
                PricingSkuCosts.sku_code == sku.sku_code).first()
            if costs is None:
                costs = PricingSkuCosts(sku_code=sku.sku_code)
                db.add(costs)
                db.flush()
            field_change_service.diff_and_apply(
                db, costs, body.costs, table="pricing_sku_costs", pk=sku.sku_code,
                actor=actor, row_label=f"{sku.sku or sku.product_code} (覆盖全产品)")
            pricing_calc_service.recompute_costs(costs, sku)
        if body.promo:
            promo = db.query(PricingSkuPromo).filter(
                PricingSkuPromo.sku_code == sku.sku_code).first()
            if promo is None:
                promo = PricingSkuPromo(sku_code=sku.sku_code)
                db.add(promo)
                db.flush()
            field_change_service.diff_and_apply(
                db, promo, body.promo, table="pricing_sku_promo", pk=sku.sku_code,
                actor=actor, row_label=f"{sku.sku or sku.product_code} (覆盖全产品)")
            pricing_calc_service.recompute_promo(promo, sku, pricing_calc_service.get_promo_params(db))
        pricing_calc_service.recompute(sku)
        updated += 1
    db.commit()
    return {"product_code": product_code, "updated": updated,
            "message": f"已覆盖 {updated} 个 SKU 并重算"}


@router.post("/bom-sync-check")
def bom_sync_check(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """已停用 (用户拍板 2026-06-12): BOM 单价只用于预估/定制报价, 不与批量定价对照。

    保留路由防旧客户端 404; 不再执行任何检查。
    """
    return {"disabled": True, "checked": 0, "stale_count": 0,
            "note": "BOM漂移检查已按拍板停用 (BOM单价只用于预估/定制报价)"}


# ---------------------------------------------------------------------------
# Plan F1: 活动报名价 — 导入 / 截图OCR双步 / 列表 / 对照检查
# ---------------------------------------------------------------------------

@router.post("/campaign-signups/import-csv")
async def import_campaign_signups(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """导入活动报名价 CSV/Excel, 入库后立即对照定价渠道价。"""
    raw = await file.read()
    from app.services import bill_import_service, promo_price_check_service, tabular
    text = tabular.to_csv_text(raw, file.filename)
    rep = bill_import_service.import_campaign_signup_csv(db, text)
    check = promo_price_check_service.check_all(db)
    db.commit()
    return {"inserted": rep.inserted, "updated": rep.skipped_duplicate,
            "skipped_invalid": rep.skipped_invalid,
            "unmapped_columns": rep.unmapped_columns, "check": check}


@router.post("/campaign-signups/ocr-parse")
async def ocr_parse_campaign_signup(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """报名结果截图 → OCR 解析 (双步第一步: 返回 rows 供人工确认, 不入库)。"""
    from app.services import vision_ocr_service
    from app.services.ai_provider import AiUnavailable
    raw = await image.read()
    try:
        return vision_ocr_service.parse_promo_signup(
            db, raw, mime=image.content_type or "image/jpeg")
    except AiUnavailable as e:
        raise HTTPException(503, str(e)) from e


class CampaignSignupRowIn(BaseModel):
    sku_code: str
    channel: str = "taobao"
    campaign_name: Optional[str] = None
    signup_price: Decimal
    effective_date: Optional[str] = None
    remark: Optional[str] = None


@router.post("/campaign-signups/commit")
def commit_campaign_signups(
    rows: list[CampaignSignupRowIn] = Body(..., embed=True),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """OCR 双步第二步: 确认后的行入库 (source=ocr), 同键 upsert, 入库后立即对照。"""
    from datetime import date as _date_cls
    from app.models.campaign_signup import CampaignSignupPrice
    from app.services import promo_price_check_service
    n_new = n_upd = 0
    for r in rows:
        ch = r.channel if r.channel in ("taobao", "xhs") else "taobao"
        camp = (r.campaign_name or "").strip() or None
        eff = None
        if r.effective_date:
            try:
                eff = _date_cls.fromisoformat(r.effective_date)
            except ValueError:
                eff = None
        existing = db.execute(
            select(CampaignSignupPrice).where(
                CampaignSignupPrice.sku_code == r.sku_code,
                CampaignSignupPrice.channel == ch,
                CampaignSignupPrice.campaign_name == camp,
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.signup_price = r.signup_price
            existing.source = "ocr"
            existing.effective_date = eff or existing.effective_date
            existing.remark = r.remark or existing.remark
            n_upd += 1
        else:
            db.add(CampaignSignupPrice(
                sku_code=r.sku_code, channel=ch, campaign_name=camp,
                signup_price=r.signup_price, source="ocr",
                effective_date=eff, remark=r.remark,
            ))
            n_new += 1
    db.flush()
    check = promo_price_check_service.check_all(db)
    db.commit()
    return {"inserted": n_new, "updated": n_upd, "check": check}


@router.get("/campaign-signups")
def list_campaign_signups(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """活动报名价列表 (近 500 条)。"""
    from app.models.campaign_signup import CampaignSignupPrice
    rows = db.execute(
        select(CampaignSignupPrice).order_by(CampaignSignupPrice.id.desc()).limit(500)
    ).scalars().all()
    return [{
        "id": r.id, "sku_code": r.sku_code, "channel": r.channel,
        "campaign_name": r.campaign_name,
        "signup_price": float(r.signup_price),
        "source": r.source,
        "effective_date": r.effective_date.isoformat() if r.effective_date else None,
        "remark": r.remark,
    } for r in rows]


@router.post("/promo-price-check")
def run_promo_price_check(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """手动跑一次 报名价 vs 定价渠道价 对照 (超差记异常 + critical 告警)。"""
    from app.services import promo_price_check_service
    r = promo_price_check_service.check_all(db)
    db.commit()
    return r


@router.post("/import-taobao-titles")
async def import_taobao_titles(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """上传淘宝商品导出 xlsx → 回填定价表 taobao_title, 再把无编码订单按标题对回编码+重算成本。

    用户拍板 2026-06-18: 解决「只带宝贝长标题、没商家编码」的订单对不到定价表、只能按百分比估成本。
    """
    from app.services import taobao_title_import_service, order_sync_service
    raw = await file.read()
    try:
        res = taobao_title_import_service.import_from_xlsx_bytes(db, raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"解析失败: {type(e).__name__}: {e}") from e
    db.commit()
    # 标题入库后立即把无编码订单按标题回填编码并重算成本
    backfilled = order_sync_service.backfill_code_from_taobao_title(db)
    db.commit()
    return {
        "parsed_rows": res.parsed_rows,
        "filled_by_sku_code": res.by_sku_code,
        "filled_by_product_code": res.by_product_code,
        "distinct_titles": res.distinct_titles,
        "unmatched_titles": res.unmatched_titles[:50],
        "orders_code_backfilled": backfilled,
    }


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
    if "factory_cost" in changes:
        sku.factory_cost_override = True   # 手改工厂成本 → 标覆盖, recompute 不再自动派生(保住手改值)
    # 手动直接改某档价(没同时改该档基数) → 清该档基数, 让 recompute 不覆盖此手动值(手动/联动互斥)
    for tier_field, base_field in _TIER_TO_BASE.items():
        if tier_field in changes and changes.get(tier_field) is not None and base_field not in changes:
            setattr(sku, base_field, None)
    pricing_calc_service.recompute(sku)
    db.commit()
    db.refresh(sku)
    return PricingSkuOut.model_validate(sku)


_TRACKED_PRICE_FIELDS = {
    "list_price", "daily_price", "accounting_cost", "physical_cost",
    "logistics_cost", "install_cost", "factory_cost", "wood_cost",
    "packaging_cost", "external_parts_cost",
}


def _record_price_changes(db: Session, sku: PricingSku, changes: dict, *, actor,
                          source: str = "web") -> None:
    """价格/成本字段变更留痕 (优化 #5 + 统一编辑历史档案)。"""
    from app.models.price_change import PriceChangeLog
    from app.services import field_change_service
    for k, v in changes.items():
        old = getattr(sku, k, None)
        if old == v:
            continue
        # 统一档案: 所有字段都记 (字段悬浮历史 / 修改档案中心)
        field_change_service.record(
            db, table="pricing_skus", pk=sku.sku_code, field=k,
            old=old, new=v, actor=actor, source=source,
            row_label=sku.sku or sku.product_code,
        )
        if k not in _TRACKED_PRICE_FIELDS:
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
# 系数目录(中文标识 + 含义) + 每个「按 SKU 系数」的众数(全局默认)
#   给定价页「系数中文标识 + 含义 + 三色覆盖标识」用:
#   某行系数 == 众数 → 跟随全局(灰); ≠ 众数 → 单行覆盖(橙)。
#   只读不改算法 —— 所有价格数字保持与定价总表一致。
# ---------------------------------------------------------------------------
COEFFICIENT_CATALOG: list[dict] = [
    # ── 结构性系数 (全表统一, 写死在公式里) ──
    {"field": "list_margin", "label": "标价毛利基数", "scope": "global", "fixed": 0.4,
     "meaning": "标价 = 物理总成本 ÷ 0.4（成本占标价 40%，留 60% 毛利空间）"},
    {"field": "daily_factor", "label": "日常价系数", "scope": "global", "fixed": 0.75,
     "meaning": "日常价 = 标价 × 0.75（日常 75 折）"},
    {"field": "platform_base", "label": "平台到手基数", "scope": "global", "fixed": 0.855,
     "meaning": "小/中/大促分母里的 0.855 = 扣完隐性后平台到手净额基数 85.5%"},
    {"field": "platform_commission", "label": "平台抽佣", "scope": "global", "fixed": 0.02,
     "meaning": "0.02 = 平台抽佣 2%"},
    {"field": "struct_tax", "label": "税", "scope": "global", "fixed": 0.006,
     "meaning": "0.006 = 税 0.6%"},
    {"field": "promo_88", "label": "88券力度", "scope": "global", "fixed": 0.88,
     "meaning": "0.88 = 中/大促 88 券活动折扣"},
    {"field": "big_extra", "label": "大促额外折", "scope": "global", "fixed": 0.95,
     "meaning": "0.95 = 大促(双11) 在中促价基础上再 95 折"},
    {"field": "vip_coupon", "label": "88VIP券", "scope": "global", "fixed": 150,
     "meaning": "150 = 中/大促会员价 = 到手价 − 150 元 88VIP 券"},
    # ── 经营性系数 (每个 SKU 可不同; 来自 pricing_sku_promo) ──
    {"field": "shop_promo_rate", "label": "店铺宝系数", "scope": "per_sku", "model": "promo",
     "meaning": "小促到手价 = 日常价 × 店铺宝系数（每个 SKU 可不同）"},
    {"field": "mid_shop_rate", "label": "中促系数", "scope": "per_sku", "model": "promo",
     "meaning": "中促到手价 = 日常价 × 88券 × 中促系数（每个 SKU 可不同）"},
    {"field": "big_shop_rate", "label": "大促系数", "scope": "per_sku", "model": "promo",
     "meaning": "大促到手价 = 日常价 × 88券 × 大促系数（每个 SKU 可不同）"},
    {"field": "xhs_promo_discount", "label": "小红书折扣率", "scope": "per_sku", "model": "promo",
     "meaning": "小红书促销价 = 活动价 × (1 − 折扣率)，默认 0.15"},
]


def _coeff_mode(db: Session, model, field: str):
    """某「按 SKU 系数」字段的众数 + 不同取值数 + 样本量 (全表)。"""
    col = getattr(model, field)
    vals = [r[0] for r in db.execute(select(col).where(col.isnot(None))).all() if r[0] is not None]
    nums = []
    for v in vals:
        try:
            nums.append(round(float(v), 6))
        except (TypeError, ValueError):
            continue
    if not nums:
        return None, 0, 0
    from collections import Counter
    mode_val = Counter(nums).most_common(1)[0][0]
    return mode_val, len(set(nums)), len(nums)


@router.get("/coefficient-stats", tags=["pricing"])
def coefficient_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """系数目录(中文标识 + 含义) + 每个「按 SKU 系数」的众数(全局默认)。

    前端定价页用它做三色覆盖标识: 某行系数 == 众数 → 灰(跟随全局); ≠ → 橙(单行覆盖)。
    只读统计, 不改任何价格算法。
    """
    model_map = {"promo": PricingSkuPromo, "sku": PricingSku}
    out: list[dict] = []
    for c in COEFFICIENT_CATALOG:
        entry = {k: c[k] for k in ("field", "label", "scope", "meaning")}
        if "fixed" in c:
            entry["fixed"] = c["fixed"]
        if c["scope"] == "per_sku":
            mode_val, distinct, sample = _coeff_mode(db, model_map.get(c.get("model", "promo")), c["field"])
            entry.update(mode=mode_val, distinct=distinct, sample=sample)
        out.append(entry)
    return {"coefficients": out}


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
        db.flush()
    from app.services import field_change_service
    field_change_service.diff_and_apply(
        db, costs, body.model_dump(exclude_unset=True),
        table="pricing_sku_costs", pk=sku_code,
        actor=getattr(_, "username", None), row_label=sku.sku or sku.product_code,
    )
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
    pricing_calc_service.recompute_promo(promo, sku, pricing_calc_service.get_promo_params(db))
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
        db.flush()
    from app.services import field_change_service
    field_change_service.diff_and_apply(
        db, promo, body.model_dump(exclude_unset=True),
        table="pricing_sku_promo", pk=sku_code,
        actor=getattr(_, "username", None), row_label=sku.sku or sku.product_code,
    )
    pricing_calc_service.recompute_promo(promo, sku, pricing_calc_service.get_promo_params(db))
    pricing_calc_service.recompute(sku)
    db.commit()
    db.refresh(promo)
    return PricingSkuPromoOut.model_validate(promo)


class PromoParamsIn(BaseModel):
    mid_platform_discount: Optional[Decimal] = None   # 中促 平台立减(力度)
    mid_vip_commission: Optional[Decimal] = None       # 中促 88VIP佣金
    big_platform_discount: Optional[Decimal] = None    # 大促 平台立减(力度)
    big_vip_commission: Optional[Decimal] = None        # 大促 88VIP佣金
    mid_coupon_tiers: Optional[list] = None            # 中促消费券阶梯 [[阈值,减额],...]
    big_coupon_tiers: Optional[list] = None            # 大促消费券阶梯


def _serialize_promo_params(p: dict) -> dict:
    out: dict = {}
    for k, v in p.items():
        if k.endswith("_coupon_tiers"):
            out[k] = [[float(a), float(b)] for a, b in v]
        else:
            out[k] = float(v)
    return out


@router.get("/promo-params")
def get_promo_params_ep(db: Session = Depends(get_db)):
    """活动价全局参数(按档): 平台立减(力度) + 88VIP佣金 + 消费券阶梯。"""
    return _serialize_promo_params(pricing_calc_service.get_promo_params(db))


@router.put("/promo-params")
def set_promo_params_ep(
    body: PromoParamsIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """设活动价全局参数 + 用新参数重算全部活动价(到手/店铺到账/会员价)。只更新传入的字段。"""
    import json
    from app.services import settings_service
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is None:
            continue
        val = json.dumps(v) if k.endswith("_coupon_tiers") else str(v)
        settings_service.set_value(db, f"promo_{k}", val,
                                   description="活动价全局参数(平台立减/88VIP佣金/消费券阶梯)")
    params = pricing_calc_service.get_promo_params(db)
    sku_map = {s.sku_code: s for s in db.query(PricingSku).all()}
    n = 0
    for pr in db.query(PricingSkuPromo).all():
        sku = sku_map.get(pr.sku_code)
        if sku is not None:
            pricing_calc_service.recompute_promo(pr, sku, params)
            n += 1
    db.commit()
    return {"params": _serialize_promo_params(params), "recomputed": n}


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


# ===========================================================================
# 定价表自定义列 (EAV) — 用户自建任意列(数值/文本)、可改名, 按 SKU 填值
# ===========================================================================

class CustomFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    label: str
    value_kind: str
    sort_order: int


class CustomFieldIn(BaseModel):
    label: str
    value_kind: str = "number"   # number | text


class CustomFieldPatch(BaseModel):
    label: Optional[str] = None
    sort_order: Optional[int] = None


class CustomValueIn(BaseModel):
    value: Optional[Any] = None   # 数值列填数字, 文本列填字符串; None/空清空


@formula_router.get("/custom-fields", response_model=list[CustomFieldOut])
def list_custom_fields(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return (
        db.query(PricingCustomField)
        .order_by(PricingCustomField.sort_order, PricingCustomField.id)
        .all()
    )


@formula_router.post("/custom-fields", response_model=CustomFieldOut, status_code=201)
def create_custom_field(
    body: CustomFieldIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    label = (body.label or "").strip() or "自定义列"
    kind = body.value_kind if body.value_kind in ("number", "text") else "number"
    max_order = db.query(func.coalesce(func.max(PricingCustomField.sort_order), 0)).scalar() or 0
    f = PricingCustomField(label=label, value_kind=kind, sort_order=int(max_order) + 1)
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


@formula_router.patch("/custom-fields/{field_id}", response_model=CustomFieldOut)
def update_custom_field(
    field_id: int,
    body: CustomFieldPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    f = db.get(PricingCustomField, field_id)
    if not f:
        raise HTTPException(404, "自定义列不存在")
    updates = body.model_dump(exclude_unset=True)
    if "label" in updates and updates["label"]:
        f.label = updates["label"].strip()
    if "sort_order" in updates and updates["sort_order"] is not None:
        f.sort_order = int(updates["sort_order"])
    db.commit()
    db.refresh(f)
    return f


@formula_router.delete("/custom-fields/{field_id}")
def delete_custom_field(
    field_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    f = db.get(PricingCustomField, field_id)
    if not f:
        raise HTTPException(404, "自定义列不存在")
    n = db.query(PricingCustomValue).filter(PricingCustomValue.field_id == field_id).delete(
        synchronize_session=False
    )
    db.delete(f)
    db.commit()
    return {"deleted_field": field_id, "deleted_values": n}


@router.patch("/{sku_code}/custom/{field_id}")
def set_custom_value(
    sku_code: str,
    field_id: int,
    body: CustomValueIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """给某 SKU 在某自定义列上填值 (数值列写 num_value, 文本列写 text_value)。"""
    fdef = db.get(PricingCustomField, field_id)
    if not fdef:
        raise HTTPException(404, "自定义列不存在")
    cv = (
        db.query(PricingCustomValue)
        .filter(PricingCustomValue.sku_code == sku_code, PricingCustomValue.field_id == field_id)
        .first()
    )
    if cv is None:
        cv = PricingCustomValue(sku_code=sku_code, field_id=field_id)
        db.add(cv)
    val = body.value
    if val in (None, ""):
        cv.num_value = None
        cv.text_value = None
    elif fdef.value_kind == "number":
        try:
            cv.num_value = Decimal(str(val))
        except (InvalidOperation, ValueError):
            raise HTTPException(422, f"该列为数值列, 无法解析: {val}")
        cv.text_value = None
    else:
        cv.text_value = str(val)
        cv.num_value = None
    db.commit()
    return {"ok": True, "sku_code": sku_code, "field_id": field_id}
