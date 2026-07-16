"""定价总表 API — 读取 + 录入 + 编辑 + 成本重算."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_optional, require_role
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
    # 成本加成基数 (改系数): 前端主表编辑器内联展示/直改, 让 标价/小促/中促/大促 由基数联动派生。
    base_list: Optional[Decimal] = None
    base_small: Optional[Decimal] = None
    base_mid: Optional[Decimal] = None
    base_big: Optional[Decimal] = None


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
    # 有效期定价(工厂调价历史): 填了生效日 → 先把改前(旧)值封存为历史区间, 该日之前的订单仍按老价/老成本。
    effective_from: Optional[date] = None


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
    _: User = Depends(get_current_user_optional),  # 定价「读」放开(影子记录不拦): 供 review-program 排单计划生成器拉 taobao_title；写接口仍 get_current_user
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
        # 淘宝/小红书 ID 也能搜 (用户需求 2026-07-10): 商品ID / SKUID / 一码多SKU的alt / 小红书ID。
        # promo 是子表 → 用 IN 子查询挂回主表; alt_taobao_sku_ids 是 JSON 列表, cast 成文本做包含匹配。
        tq = q.strip()
        id_match = PricingSku.sku_code.in_(
            select(PricingSkuPromo.sku_code).where(or_(
                PricingSkuPromo.taobao_item_id.like(f"%{tq}%"),
                PricingSkuPromo.taobao_sku_id.like(f"%{tq}%"),
                cast(PricingSkuPromo.alt_taobao_sku_ids, String).like(f"%{tq}%"),
                PricingSkuPromo.xhs_item_id.like(f"%{tq}%"),
                PricingSkuPromo.xhs_sku_id.like(f"%{tq}%"),
            ))
        )
        filters.append(or_(fc, id_match) if fc is not None else id_match)
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

    # 报名价法真实报名价 (2026-07-17): 老列 taobao_activity_price(=日常价法)已废弃口径,
    # 前端「淘宝活动报名价」列改读派生的 signup_price_big(=大促到手锚反解, 与实际推送一致)。
    promo_params = pricing_calc_service.get_promo_params(db)

    items = []
    for r in rows:
        base = PricingSkuOut.model_validate(r).model_dump()
        base.update(_ext_dict(costs_map.get(r.sku_code)))
        base.update(_ext_dict(promo_map.get(r.sku_code)))
        _promo = promo_map.get(r.sku_code)
        if _promo is not None:
            _rp = pricing_calc_service.report_prices(_promo, promo_params)
            base["signup_price_big"] = _rp.get("signup_price_big")
            base["signup_price_mid"] = _rp.get("signup_price_mid")
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
    from datetime import date as _date
    from app.services import pricing_version_service
    for sku in skus:
        if body.sku:
            # ★方案B(2026-07-12): 覆盖全产品编辑也自动版本化(写新值前封存旧值, 老单老价)
            pricing_version_service.record_if_price_changed(
                db, sku, body.sku, actor=actor, note="编辑器覆盖全产品·自动版本化")
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
            # 配件成本改动会经 recompute_costs 汇入 sku.external_parts_cost/physical_cost →
            # 任一配件字段将变时, 先封存 sku 旧值(同日去重, 与上面重复调用无害)。
            if any(str(getattr(costs, k, None) or "") != str(v or "") for k, v in body.costs.items()):
                try:
                    pricing_version_service.record_dated_change(
                        db, sku, _date.today(), actor=actor, note="配件成本编辑·自动版本化")
                except ValueError:
                    pass
            field_change_service.diff_and_apply(
                db, costs, body.costs, table="pricing_sku_costs", pk=sku.sku_code,
                actor=actor, row_label=f"{sku.sku or sku.product_code} (覆盖全产品)")
            pricing_calc_service.recompute_costs(costs, sku)
        pricing_calc_service.recompute(sku)      # 先算成本→价格链, 再由价格倒推单品立减系数
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


@router.post("/recon")
async def reconcile_qn_prices(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """价格对账: 上传千牛「商品导出/发布模板」xlsx → 千牛现价 vs ERP应有值的漂移清单 (只读不改)。"""
    from app.services import pricing_recon_service
    raw = await file.read()
    return pricing_recon_service.reconcile(db, raw)


@router.post("/recon/fix-xlsx")
async def reconcile_fix_xlsx(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """价格对账·返修表: 上传千牛导出 → 「标价返修表」xlsx (漂移SKU的正确一口价=日常÷0.75)。"""
    from fastapi.responses import StreamingResponse
    from app.services import pricing_recon_service
    raw = await file.read()
    bio = pricing_recon_service.build_fix_xlsx(db, raw)
    return StreamingResponse(
        bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=biaojia_fix.xlsx"})


@router.post("/recon-coupon")
async def reconcile_coupon_prices(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """券后价对账: 上传千牛「超级立减已报商品列表」xlsx → 活动普惠券后价 vs ERP中促到手(mid_buyer_price)
    的漂移清单 (容差0.01, 一分钱不差; 只读不改)。"""
    from app.services import pricing_recon_service
    raw = await file.read()
    return pricing_recon_service.reconcile_coupon(db, raw)


@router.post("/recon-coupon/fix-xlsx")
async def reconcile_coupon_fix_xlsx(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """券后价对账·返修表: 上传千牛超级立减导出 → 「券后价返修表」xlsx
    (漂移SKU的正确券后价=ERP中促到手 + 应填单品立减金额)。"""
    from fastapi.responses import StreamingResponse
    from app.services import pricing_recon_service
    raw = await file.read()
    bio = pricing_recon_service.build_coupon_fix_xlsx(db, raw)
    return StreamingResponse(
        bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=quanhoujia_fix.xlsx"})


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


@router.post("/import-enrolled-floor")
async def import_enrolled_floor(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """上传千牛「活动商品导出」(已报商品列表) → 导入各 SKUID 的【已生效活动价】(校验底价)。

    用途(2026-07-12 用户: 第二场62件全失败): 淘宝要求活动券后价 ≤ 校验期最低普惠券后价,
    上一场已生效价就是硬底 → 占位SKU报名价自动封顶到它 + 预检超线红字。重复导入取更低值。"""
    from app.services import enrolled_floor_import_service
    raw = await file.read()
    try:
        res = enrolled_floor_import_service.import_from_xlsx_bytes(db, raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"解析失败: {type(e).__name__}: {e}") from e
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "导入失败"))
    return res


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
        "listed_marked": res.listed_marked,   # 顺带标记为「在售」的产品数
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
    eff = changes.pop("effective_from", None)   # 控制参数, 非 sku 列
    from app.services import pricing_version_service
    if eff is not None:
        # 调价"从 eff 起生效": 先把改前(旧)定价值封存为历史区间 → eff 之前的订单仍按老价/老成本(利润不追溯改)
        try:
            pricing_version_service.record_dated_change(db, sku, eff, actor=getattr(_, "username", None))
        except ValueError as e:
            raise HTTPException(400, str(e))
    else:
        # ★方案B(2026-07-12): 普通编辑没带生效日也自动版本化 —— 改价/成本字段将变 → 以今天为界封存旧值,
        #   老单老价新单新价, 不再"随便跳"。同日重复改自动去重。
        pricing_version_service.record_if_price_changed(
            db, sku, changes, actor=getattr(_, "username", None), note="定价编辑自动版本化")
    _record_price_changes(db, sku, changes, actor=getattr(_, "username", None))
    for k, v in changes.items():
        setattr(sku, k, v)
    if "factory_cost" in changes:
        # 填数值 → 上锁(保住手改值, recompute 不覆盖); 清空 → 解锁恢复"木作+包装+外配件"自动加总
        # (2026-07-17 修: 之前清空也上锁, 自动加总永久失灵, 页面表现为"联动失效")
        sku.factory_cost_override = changes["factory_cost"] is not None
    # 手动直接改某档价(没同时改该档基数) → 清该档基数, 让 recompute 不覆盖此手动值(手动/联动互斥)
    for tier_field, base_field in _TIER_TO_BASE.items():
        if tier_field in changes and changes.get(tier_field) is not None and base_field not in changes:
            setattr(sku, base_field, None)
    pricing_calc_service.recompute(sku)
    # 促价/日常价 一变 → 同步倒推单品立减系数 (2026-07-02 改回 Excel 倒推法), 让「改价台」/主表改价后系数即时更新
    if any(getattr(sku, f) is not None for f in ("small_promo", "mid_promo", "big_promo")):
        promo = db.query(PricingSkuPromo).filter(PricingSkuPromo.sku_code == sku.sku_code).first()
        if promo is None:
            promo = PricingSkuPromo(sku_code=sku.sku_code)
            db.add(promo)
            db.flush()
        pricing_calc_service.recompute_promo(promo, sku, pricing_calc_service.get_promo_params(db))
    db.commit()
    db.refresh(sku)
    return PricingSkuOut.model_validate(sku)


# ── 改价台 (2026-07-02): Excel 式改「定价基数」(0.86/0.88/0.9) → 价格=ROUNDUP(成本÷基数,10) 联动 ──
# 复刻用户 List 表: 促价由基数(系数)算出来; 用户改基数, 价格自动变。右侧附带反推的单品立减系数(填淘宝用)。
class ShopPriceRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_code: str
    product_name: Optional[str] = None
    sku: Optional[str] = None
    size_info: Optional[str] = None
    image: Optional[str] = None
    daily_price: Optional[Decimal] = None
    base_small: Optional[Decimal] = None        # 小促定价基数 (=Excel系数 0.86, 可改)
    base_mid: Optional[Decimal] = None           # 中促定价基数 (0.88)
    base_big: Optional[Decimal] = None           # 大促定价基数 (0.9)
    small_promo: Optional[Decimal] = None        # 小促价 = ROUNDUP(成本÷base_small,10) (算出来)
    mid_promo: Optional[Decimal] = None
    big_promo: Optional[Decimal] = None
    # 各档【买家到手】(目标价, = 促价÷(1−佣金)); 单品立减/报名价都对着它算
    mid_buyer_price: Optional[Decimal] = None    # 中促买家到手 (日常 10% 场目标)
    big_buyer_price: Optional[Decimal] = None    # 大促买家到手 (88VIP 12% / 618 15% 场目标)
    # 单品立减 (加法口径, 2026-07-06 用户附图核准): 淘宝该填的 折扣 + 立减金额, 三档场次力度 10/12/15%
    #   单品立减折 = 买家到手÷日常 + 官方力度 ; 立减金额 = 日常×(1−力度) − 买家到手
    mid_discount: Optional[Decimal] = None       # 中促(日常10%) 单品立减折 (0.79 = 7.9折)
    mid_deduct: Optional[Decimal] = None         # 中促 单品立减金额(元)
    big_discount: Optional[Decimal] = None       # 大促(88VIP 12%) 单品立减折
    big_deduct: Optional[Decimal] = None         # 大促 单品立减金额(元)
    big618_discount: Optional[Decimal] = None    # 超大促(618/双11 15%) 单品立减折
    big618_deduct: Optional[Decimal] = None      # 超大促 单品立减金额(元)
    physical_cost: Optional[Decimal] = None      # 物理成本(工厂+物流+安装), 大促利润的成本基
    big_promo_margin: Optional[Decimal] = None   # 大促利润 = 大促价 −(物理成本 + 平台费0.6% + 税2%) (recompute 口径)
    gross_margin_rate: Optional[Decimal] = None  # 大促利润率 = 大促利润 ÷ 大促价
    # 报名价模型 (2026-07-03: 大促锚不动, 只动中促) —— 派生, 不落库
    report_price: Optional[Decimal] = None       # 报名价 A = 大促到手 ÷ 0.88 (填淘宝超级立减)
    report_price_618: Optional[Decimal] = None   # 618/双11 报名价 = 大促到手 ÷ 0.85
    gap_floor: Optional[Decimal] = None          # 空档价红线 = 中促到手 (空档期单品立减不得低于此)
    compliance_g: Optional[Decimal] = None       # g = 中促到手 ÷ 大促到手
    report_compliant: Optional[bool] = None      # g≥0.90/0.88 → 绿; False → 需微升中促(红)


def _shop_price_row(sku: PricingSku, promo, name, image, params=None) -> "ShopPriceRow":
    rp = pricing_calc_service.report_prices(promo, params) if promo is not None else {}
    sid = (pricing_calc_service.single_item_discounts(promo, sku.daily_price, params)
           if promo is not None else {})
    return ShopPriceRow(
        id=sku.id, product_code=sku.product_code, product_name=name,
        sku=sku.sku, size_info=sku.size_info, image=image, daily_price=sku.daily_price,
        base_small=sku.base_small, base_mid=sku.base_mid, base_big=sku.base_big,
        small_promo=sku.small_promo, mid_promo=sku.mid_promo, big_promo=sku.big_promo,
        mid_buyer_price=getattr(promo, "mid_buyer_price", None),
        big_buyer_price=getattr(promo, "big_buyer_price", None),
        mid_discount=sid.get("mid_discount"), mid_deduct=sid.get("mid_deduct"),
        big_discount=sid.get("big_discount"), big_deduct=sid.get("big_deduct"),
        big618_discount=sid.get("big618_discount"), big618_deduct=sid.get("big618_deduct"),
        physical_cost=sku.physical_cost,
        big_promo_margin=sku.big_promo_margin,
        gross_margin_rate=sku.gross_margin_rate,
        report_price=rp.get("report_price"),
        report_price_618=rp.get("report_price_618"),
        gap_floor=rp.get("gap_floor"),
        compliance_g=rp.get("compliance_g"),
        report_compliant=rp.get("report_compliant"),
    )


@router.get("/shop-price-board", response_model=list[ShopPriceRow])
def shop_price_board(
    q: Optional[str] = Query(None, description="按 产品/SKU 模糊搜"),
    limit: int = Query(1000, ge=1, le=3000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """改价台取数: 产品图/名/SKU/日常价 + 三档定价基数(可改) + 三档促价(算出来) + 三档单品立减系数(反推)。"""
    from app.models.product import Product
    from app.services.gallery_lookup import sku_gallery_url_map
    stmt = select(PricingSku)
    if q:
        from app.services.fuzzy_search import fuzzy_clause
        fc = fuzzy_clause(q, like_cols=[
            PricingSku.product_code, PricingSku.sku_code, PricingSku.sku, PricingSku.product_name],
            gap_cols=[PricingSku.sku, PricingSku.product_name])
        if fc is not None:
            stmt = stmt.where(fc)
    rows = db.execute(
        stmt.order_by(PricingSku.product_code, PricingSku.sku_code).limit(limit)
    ).scalars().all()
    codes = [r.sku_code for r in rows if r.sku_code]
    promo_map = {p.sku_code: p for p in db.execute(
        select(PricingSkuPromo).where(PricingSkuPromo.sku_code.in_(codes))).scalars()} if codes else {}
    name_map = dict(db.execute(select(Product.code, Product.name)).all())
    gallery = sku_gallery_url_map([(r.product_code, r.sku_code, r.sku) for r in rows])
    return [
        _shop_price_row(r, promo_map.get(r.sku_code),
                        name_map.get(r.product_code) or r.product_name,
                        gallery.get(r.sku_code) or r.image_url)
        for r in rows
    ]


class ShopPricePatch(BaseModel):
    base_small: Optional[Decimal] = None    # 小促定价基数(除数, =Excel系数 0.86)
    base_mid: Optional[Decimal] = None       # 中促 0.88
    base_big: Optional[Decimal] = None       # 大促 0.9


def _validate_base_changes(changes: dict) -> None:
    if not changes:
        raise HTTPException(400, "无改动")
    for k, v in changes.items():
        if v is not None and Decimal(str(v)) <= 0:
            raise HTTPException(400, f"{k} 必须 > 0 (它是除数/定价基数)")


def _apply_shop_price_change(db: Session, sku: PricingSku, changes: dict, *, actor, promo_params):
    """改价台核心(单条/批量共用): 留痕 → 记工厂调价历史(先封存旧值) → 写新基数 → recompute(价格链)
    → recompute_promo(单品立减系数)。不 commit(由 caller 统一提交, 便于批量)。返回该 sku 的 promo 行。"""
    from datetime import date as _date
    from app.services import pricing_version_service
    _record_price_changes(db, sku, changes, actor=actor)
    # 记入「工厂调价历史」: 先把改前(旧)价封存为 [上边界, 今天); 同日重复改→ValueError忽略(不重复封存)
    try:
        pricing_version_service.record_dated_change(db, sku, _date.today(), actor=actor, note="改价台改基数")
    except ValueError:
        pass
    for k, v in changes.items():
        setattr(sku, k, v)
    pricing_calc_service.recompute(sku)      # 基数→价格链 (价格=ROUNDUP(成本÷基数,10))
    promo = db.query(PricingSkuPromo).filter(PricingSkuPromo.sku_code == sku.sku_code).first()
    if promo is None:
        promo = PricingSkuPromo(sku_code=sku.sku_code)
        db.add(promo)
        db.flush()
    pricing_calc_service.recompute_promo(promo, sku, promo_params)   # 价格→单品立减系数
    return promo


@router.patch("/{sku_id}/shop-price", response_model=ShopPriceRow)
def update_shop_price(
    sku_id: int,
    body: ShopPricePatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """改价台: 改某档【定价基数】(0.86/0.88/0.9) → recompute 按 价格=ROUNDUP(成本÷基数,10) 联动出促价
    → recompute_promo 反推单品立减系数 → 返回含最新价格+系数的行 (复刻用户 List 表口径)。"""
    sku = db.get(PricingSku, sku_id)
    if not sku:
        raise HTTPException(404, "Not found")
    changes = body.model_dump(exclude_unset=True)
    _validate_base_changes(changes)
    promo = _apply_shop_price_change(
        db, sku, changes, actor=getattr(_, "username", None),
        promo_params=pricing_calc_service.get_promo_params(db))
    db.commit()
    db.refresh(sku)
    db.refresh(promo)
    from app.models.product import Product
    from app.services.gallery_lookup import sku_gallery_url_map
    name = db.execute(select(Product.name).where(Product.code == sku.product_code)).scalar()
    img = sku_gallery_url_map([(sku.product_code, sku.sku_code, sku.sku)]).get(sku.sku_code) or sku.image_url
    return _shop_price_row(sku, promo, name or sku.product_name, img)


class BulkShopPricePatch(BaseModel):
    sku_ids: list[int]                       # 要批量套用的 SKU id 列表(筛选后全选)
    base_small: Optional[Decimal] = None
    base_mid: Optional[Decimal] = None
    base_big: Optional[Decimal] = None


@router.patch("/shop-price/bulk", response_model=list[ShopPriceRow])
def bulk_update_shop_price(
    body: BulkShopPricePatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """改价台批量: 把同一组定价基数(留空的档不改)套用到多个 SKU —— 筛选后全选一次改, 不用逐个点。
    每个 SKU 都走单条同样的口径: 记工厂调价历史(封存旧值) + recompute + 反推系数。"""
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k != "sku_ids"}
    _validate_base_changes(changes)
    ids = list(dict.fromkeys(body.sku_ids or []))
    if not ids:
        raise HTTPException(400, "未选中任何 SKU")
    if len(ids) > 2000:
        raise HTTPException(400, "一次最多批量 2000 个 SKU")
    params = pricing_calc_service.get_promo_params(db)
    actor = getattr(_, "username", None)
    skus = db.execute(select(PricingSku).where(PricingSku.id.in_(ids))).scalars().all()
    promo_by_code: dict = {}
    for sku in skus:
        promo_by_code[sku.sku_code] = _apply_shop_price_change(
            db, sku, dict(changes), actor=actor, promo_params=params)
    db.commit()
    from app.models.product import Product
    from app.services.gallery_lookup import sku_gallery_url_map
    name_map = dict(db.execute(select(Product.code, Product.name)).all())
    gallery = sku_gallery_url_map([(s.product_code, s.sku_code, s.sku) for s in skus])
    out: list[ShopPriceRow] = []
    for sku in skus:
        db.refresh(sku)
        promo = promo_by_code.get(sku.sku_code)
        if promo is not None:
            db.refresh(promo)
        out.append(_shop_price_row(
            sku, promo, name_map.get(sku.product_code) or sku.product_name,
            gallery.get(sku.sku_code) or sku.image_url))
    return out


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
    # ── 结构性系数 (成本加成定价, 写死在公式里) ──
    {"field": "list_margin", "label": "标价毛利基数", "scope": "global", "fixed": 0.4,
     "meaning": "标价 = 会计成本基准 ÷ 0.4（成本占标价 40%，留 60% 毛利空间）；会计成本基准 = 物理成本 ÷ (1 − 2.6%)"},
    {"field": "daily_factor", "label": "日常价系数", "scope": "global", "fixed": 0.75,
     "meaning": "日常价 = 标价 × 0.75（日常 75 折）"},
    {"field": "grossup_rate", "label": "成本加成率", "scope": "global", "fixed": 0.026,
     "meaning": "各档促价 = 进位到10( 物理成本 ÷ (1 − 2.6%) ÷ 定价基数 )；2.6% = 支付手续费 0.6% + 税 2%"},
    {"field": "pay_fee", "label": "支付手续费", "scope": "global", "fixed": 0.006,
     "meaning": "0.006 = 支付手续费 0.6%（算进会计总成本 = 大促价 × 0.6%）"},
    {"field": "struct_tax", "label": "税", "scope": "global", "fixed": 0.02,
     "meaning": "0.02 = 税 2%（算进会计总成本 = 大促价 × 2%）"},
    # ── 定价基数 (每个 SKU 可不同; 「改价台」改的就是它; 基数越小价越高) ──
    {"field": "base_small", "label": "小促定价基数", "scope": "per_sku", "model": "sku",
     "meaning": "小促价 = 进位到10( 会计成本基准 ÷ 小促基数 )；在「改价台」按 SKU 可改，基数越小价越高（右侧为全表众数）"},
    {"field": "base_mid", "label": "中促定价基数", "scope": "per_sku", "model": "sku",
     "meaning": "中促价 = 进位到10( 会计成本基准 ÷ 中促基数 )；在「改价台」按 SKU 可改，基数越小价越高（右侧为全表众数）"},
    {"field": "base_big", "label": "大促定价基数", "scope": "per_sku", "model": "sku",
     "meaning": "大促价 = 进位到10( 会计成本基准 ÷ 大促基数 )；在「改价台」按 SKU 可改，基数越小价越高（右侧为全表众数）"},
    # ── 活动价系数 (全局默认, 可在「活动参数」调整; 用于从促价倒推单品立减系数/到手价) ──
    {"field": "platform_discount", "label": "平台立减(力度)", "scope": "global", "fixed": 0.12,
     "meaning": "中/大促报名立减 12%（可在活动参数调）；买家到手 = 日常价 × (1 − 12%) × 单品立减系数"},
    {"field": "vip_commission", "label": "88VIP佣金", "scope": "global", "fixed": 0.02,
     "meaning": "88VIP 佣金 2%（可在活动参数调）；店铺到账 = 买家到手 × (1 − 2%)，而「大促价」本身即店铺到账(佣金后净额)"},
    {"field": "vip_coupon", "label": "88VIP消费券(阶梯)", "scope": "global",
     "meaning": "会员价 = 买家到手 − 阶梯消费券：到手 ≥1500 减150 / ≥800 减80 / ≥500 减50 / ≥200 减20（可在活动参数调）"},
    # ── 单品立减系数 (每个 SKU 可不同; 来自 pricing_sku_promo; 店铺宝已停用, 空档期用单品立减做价) ──
    {"field": "shop_promo_rate", "label": "小促单品立减系数", "scope": "per_sku", "model": "promo",
     "meaning": "空档期用单品立减把价做到此水平；小促价 = 日常价 × 小促单品立减系数（每个 SKU 可不同）"},
    {"field": "mid_shop_rate", "label": "中促单品立减系数", "scope": "per_sku", "model": "promo",
     "meaning": "空档期用单品立减把价做到此水平；中促买家到手 = 日常价 × (1 − 立减12%) × 中促单品立减系数（每个 SKU 可不同）"},
    {"field": "big_shop_rate", "label": "大促单品立减系数", "scope": "per_sku", "model": "promo",
     "meaning": "空档期用单品立减把价做到此水平；大促买家到手 = 日常价 × (1 − 立减12%) × 大促单品立减系数（每个 SKU 可不同）"},
    # ── 报名价模型 (超级立减报名价; 大促锚不动, 只动中促) ──
    {"field": "report_price", "label": "报名价 A", "scope": "per_sku", "model": "derived",
     "meaning": "填进淘宝超级立减报名表的数 = 大促到手 ÷ (1 − 大促力度12%)；中促场买家到手 = A × 0.90, 大促场 = A × 0.88, 618场 = A × 0.85"},
    {"field": "compliance_g", "label": "中促合规比 g", "scope": "per_sku", "model": "derived",
     "meaning": "g = 中促到手 ÷ 大促到手；g ≥ 0.90/0.88(=1.0227) 才能用同一报名价在中促场报得进；不足→「一键微升中促」抬中促(大促不动)"},
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
    pricing_calc_service.recompute(sku)      # 先算好 小/中/大促价, 再由它倒推单品立减系数
    pricing_calc_service.recompute_promo(promo, sku, pricing_calc_service.get_promo_params(db))
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
    pricing_calc_service.recompute(sku)      # 先算好 小/中/大促价, 再由它倒推单品立减系数
    pricing_calc_service.recompute_promo(promo, sku, pricing_calc_service.get_promo_params(db))
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


@router.post("/fix-mid-compliance", tags=["pricing"])
def fix_mid_compliance(
    apply: bool = Query(False, description="False=dry-run(只返回清单,不改库); True=落库并重算"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """一键微升中促合规 (2026-07-03 报名价模型): 扫全表, 把 中促到手÷大促到手 < 0.90/0.88 的 SKU
    抬【中促实收】令 g=g_min(报名价 A 才在中促场报得进), **大促价一分不动**。
    默认 dry-run 返回每条【中促前→后 + g】清单(=落库前给用户核对的验算); apply=true 才落库+重算促价链。"""
    params = pricing_calc_service.get_promo_params(db)
    skus = db.query(PricingSku).filter(
        PricingSku.mid_promo.isnot(None), PricingSku.big_promo.isnot(None)).all()
    codes = [s.sku_code for s in skus if s.sku_code]
    promo_map = {p.sku_code: p for p in db.query(PricingSkuPromo).filter(
        PricingSkuPromo.sku_code.in_(codes)).all()} if codes else {}
    # 铁律: 大促价一分不动。落库前逐条快照大促价, 落库后校验没被动过, 否则整体回滚。
    big_before = {s.sku_code: s.big_promo for s in skus}
    changes = []
    for sku in skus:
        r = pricing_calc_service.fix_mid_to_compliant(sku, params)
        if not r:
            continue
        changes.append(r)
        if apply:
            promo = promo_map.get(sku.sku_code)
            if promo is None:
                promo = PricingSkuPromo(sku_code=sku.sku_code)
                db.add(promo)
                db.flush()
            # 只刷单品立减系数(读新中促价)。绝不调 recompute(sku) —— 它在 base_big 有值的 SKU 上会按
            # cost-plus 重算 big_promo, 破「大促价一分不动」。fix 已设好 mid_promo, 利润链(依赖大促价)不变。
            pricing_calc_service.recompute_promo(promo, sku, params)
    if apply:
        touched_big = [s.sku_code for s in skus
                       if s.sku_code in big_before and s.big_promo != big_before[s.sku_code]]
        if touched_big:
            db.rollback()
            raise HTTPException(
                500, f"中止: 检测到 {len(touched_big)} 条大促价被改动(破铁律), 已整体回滚: {touched_big[:10]}")
        db.commit()
    else:
        db.rollback()   # dry-run: 丢弃内存改动, 生产库零变动
    return {
        "apply": apply, "scanned": len(skus), "changed": len(changes),
        "changes": sorted(changes, key=lambda x: x["g_before"]),
    }


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


@formula_router.get("/catalog", response_class=HTMLResponse)
def pricing_catalog(
    limit: int = Query(0, description="只取前 N 个产品 (预览用; 0=全部)"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """定价图册 (带图导出): 一产品一行, 左大图 + 右各 SKU 5 档售价。
    自包含 HTML (图片 base64 内嵌), 浏览器直接打开即好看, Ctrl+P 可存 PDF。"""
    from app.services import pricing_catalog_service
    return HTMLResponse(pricing_catalog_service.build_catalog_html(db, limit=limit or None))


@formula_router.get("/catalog.xlsx")
def pricing_catalog_xlsx(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """定价图册 (Excel, 带产品图): 一 SKU 一行, 首列产品图(同编码多 SKU 合并只放一张),
    全字段 + 中文表头 + 分类色带配色。用户 2026-07-01: 要 Excel 不要 HTML。"""
    from urllib.parse import quote
    from fastapi.responses import StreamingResponse
    from app.services import data_export_service
    bio = data_export_service.build_catalog_xlsx(db)
    fn = quote("畔色定价图册.xlsx")
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"},
    )


@formula_router.get("/signup-form.xlsx")
def activity_signup_form_xlsx(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """活动报名表 (Excel, 带产品图): 给同事填淘宝活动价用的精简表 —— 产品图/名/规格 + 一口价/日常价 +
    各档到手 + 报名价(88VIP大促/超大促618) + 单品立减(折 + 立减金额, 三档 10/12/15%, 加法口径)。
    只留填淘宝必要列, 去掉 ID/标题/编码/成本/小红书/配件/旧乘法系数 等 (用户 2026-07-06)。"""
    from urllib.parse import quote
    from fastapi.responses import StreamingResponse
    from app.services import data_export_service
    bio = data_export_service.build_signup_form_xlsx(db)
    fn = quote("畔色活动报名表.xlsx")
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"},
    )


@formula_router.get("/single-item-discount.xlsx")
def single_item_discount_upload_xlsx(
    tier: str = Query("big", description="档位: mid=超级立减10% / big=88VIP大促12% / big618=大促15%(618双11)"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """淘宝『单品立减』批量上传表 (SKU级别减钱口径, 表头与淘宝模板逐字一致, 可直接上传)。
    每档一张(力度 10/12/15%), 数值每次下载实时算(成本/售价变即变)。"""
    from urllib.parse import quote
    from fastapi.responses import StreamingResponse
    from app.services import data_export_service
    from app.services.data_export_service import _TB_DISCOUNT_TIERS
    if tier not in _TB_DISCOUNT_TIERS:
        raise HTTPException(400, f"未知档位 {tier}; 可选 {list(_TB_DISCOUNT_TIERS)}")
    bio, _stats = data_export_service.build_single_item_discount_upload_xlsx(db, tier)
    fn = quote(f"淘宝单品立减_{_TB_DISCOUNT_TIERS[tier][0]}.xlsx")
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"},
    )


@formula_router.get("/promo-signup.xlsx")
def promo_signup_upload_xlsx(
    tier: str = Query("big", description="档位: mid=超级立减10% / big=88VIP大促12% / big618=超级大促双11 15%"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """淘宝『大促活动报名』批量导入表 (千牛后台大促活动导入, SKU维度, 照模板生成可直接上传)。
    只填 商品ID/SKUID/活动价(=报名价); 库存/发货时间/官方立减折扣/官方立减金额 留空。
    超级立减10% 与 88VIP大促12% 用同一个报名价, 超级大促15% 用618报名价。实时算。"""
    from urllib.parse import quote
    from fastapi.responses import StreamingResponse
    from app.services import data_export_service
    from app.services.data_export_service import _PROMO_SIGNUP_TIERS
    if tier not in _PROMO_SIGNUP_TIERS:
        raise HTTPException(400, f"未知档位 {tier}; 可选 {list(_PROMO_SIGNUP_TIERS)}")
    bio, _stats = data_export_service.build_promo_signup_upload_xlsx(db, tier)
    fn = quote(f"大促活动报名_{_PROMO_SIGNUP_TIERS[tier][0]}.xlsx")
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"},
    )


@formula_router.get("/super-reduce-signup.xlsx")
def super_reduce_signup_upload_xlsx(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """淘宝『超级立减活动』批量报名表 (14列, SKU级只填补贴金额=报名价A×10%, 到手=中促到手)。
    坏价产品排除, 仅推送有淘宝SKUID的。用户拍板 2026-07-11。"""
    from urllib.parse import quote
    from fastapi.responses import StreamingResponse
    from app.services import data_export_service
    bio, _stats = data_export_service.build_super_reduce_signup_upload_xlsx(db)
    fn = quote("超级立减活动_补贴金额.xlsx")
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"},
    )


@formula_router.get("/product-price-quick-edit.xlsx")
def product_price_quick_edit_xlsx(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """Step1 商品价格快速编辑/核对表: 每个已映射SKU 现千牛标价 vs 应改一口价(=ERP日常价÷0.75)。
    供千牛「excel商品批量编辑」参考改价(ERP价为准, 2026-07-13)。需改的标红。"""
    from urllib.parse import quote
    from fastapi.responses import StreamingResponse
    from app.services import data_export_service
    bio, _stats = data_export_service.build_product_price_quick_edit_xlsx(db)
    fn = quote("商品价格快速编辑_ERP标准.xlsx")
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"},
    )


@formula_router.get("/activity-preflight")
def activity_preflight_endpoint(
    floor_days: int = Query(15, ge=1, le=90, description="15天最低价窗口天数"),
    skip_floor_check: bool = Query(False, description="本次按初始报价跳过15天最低价校验(未来仍照跑)"),
    tier: str = Query("big", description="场次力度: mid=中促10% / big=88VIP大促12% / big618=618双11 15%"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """活动报名『虚拟推送(三档核对)』: 不产文件不改数据, 返回生成活动表前的三档核对 + 问题清单。
    档1 商品价格核验(ERP日常价 vs 千牛标价快照) / 档2 单品立减 / 档3 报名价; 另含坏价产品 /
    缺淘宝映射 / 15天最低价冲突 / 券后超线 / 各步就绪计数。用户 2026-07-11、2026-07-13。
    skip_floor_check=True: 初始报价场景整体跳过15天冲突(首次立基准)。"""
    from app.services import activity_preflight_service
    return activity_preflight_service.activity_preflight(
        db, floor_days=floor_days, skip_floor_check=skip_floor_check, tier=tier)


@formula_router.get("/sku-rotation/preview")
def sku_rotation_preview(
    product_code: str = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """超大促 SKU 轮换计划预览(只算不改): 按尺寸阶梯出 千牛指令 + ERP新映射。见 价格体系设置.md §九。"""
    from app.services import sku_rotation_service
    return sku_rotation_service.plan_rotation(db, product_code)


@formula_router.post("/sku-rotation/apply")
def sku_rotation_apply(
    product_code: str = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """★一次性★ 千牛轮换完成后同步 ERP: 按当前轮换计划重写 PricingSkuPromo.taobao_sku_id。
    只在人工在千牛把规格/价格/编码轮换好之后点(否则映射会错位)。admin 限。"""
    from app.services import sku_rotation_service
    plan = sku_rotation_service.plan_rotation(db, product_code)
    if not plan.get("ok"):
        return plan
    em = [r for lad in plan["ladders"] for r in lad.get("erp_mapping", [])]
    res = sku_rotation_service.apply_mapping(db, product_code, em, dry_run=False)
    db.commit()
    return res


@formula_router.post("/activity-upload/{channel}/stage")
def activity_upload_stage(
    channel: str,
    tier: str = Query("big"),
    start_dt: str = Query(""),   # 单品立减: 选定档期 'YYYY-MM-DD HH:MM:SS' 填千牛活动时间
    end_dt: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """千牛上传·预演: 生成表→Web-Agent挂到千牛(不提交)→回比对表+校验+截图。用户 2026-07-11。"""
    from app.services import activity_upload_service
    return activity_upload_service.stage(db, channel, tier,
                                         start_dt=start_dt or None, end_dt=end_dt or None)


@formula_router.post("/activity-upload/{channel}/commit")
def activity_upload_commit(
    channel: str,
    tier: str = Query("big"),
    start_dt: str = Query(""),
    end_dt: str = Query(""),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """★不可逆★ 千牛上传·真提交。前端必须在用户看过比对表、点确认后才调 (admin 限)。
    super_reduce 返回 {async_job} → 前端轮询 /activity-upload/commit-status。"""
    from app.services import activity_upload_service
    return activity_upload_service.commit(db, channel, tier,
                                          start_dt=start_dt or None, end_dt=end_dt or None)


@formula_router.get("/activity-upload/commit-status")
def activity_upload_commit_status(
    job: str = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """轮询超级立减逐商品改价进度: {status: running|done|error, result?}。"""
    from app.services import activity_upload_service
    return activity_upload_service.commit_status(db, job)


@formula_router.post("/product-price-auto-push")
def product_price_auto_push_endpoint(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """★全自动推标价(2026-07-14): WA千牛导出→系统把一口价改成日常价÷0.75→WA上传千牛
    excel商品批量编辑→停在提交前(最终"提交"你点)。约2-3分钟(含异步导出等待)。"""
    from app.services import activity_upload_service
    return activity_upload_service.product_price_auto_push(db)


# ── 活动档期日历 (2026-07-13 用户: 报名/单品立减选具体档期 + 单品立减自动结束=下一档期前一刻) ──
@formula_router.get("/activity-calendar")
def get_activity_calendar(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """活动档期日历(报名/单品立减选档期用) + 当前/即将档期状态。"""
    from app.services import activity_calendar_service
    return {"periods": activity_calendar_service.get_calendar(db),
            "status": activity_calendar_service.status(db)}


@formula_router.put("/activity-calendar")
def put_activity_calendar(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """整表覆盖活动档期日历。payload = {periods: [{name, tier, start, end}]}。"""
    from app.services import activity_calendar_service
    periods = payload.get("periods") if isinstance(payload, dict) else payload
    saved = activity_calendar_service.set_calendar(db, periods or [])
    return {"periods": saved, "status": activity_calendar_service.status(db)}


@formula_router.get("/activity-calendar/auto-end")
def activity_calendar_auto_end(
    start: str = Query(..., description="本档单品立减开始日 YYYY-MM-DD"),
    this_name: str = Query("", description="本档名称(排除自身)"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """单品立减自动结束 = 下一档期开始前一刻(23:59:59)。无下一档 → end=None(提示无下次活动)。"""
    from app.services import activity_calendar_service
    return activity_calendar_service.auto_end_for(db, start, this_name=(this_name or None))


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


@formula_router.get("/version-history")
def list_price_versions(
    sku_code: Optional[str] = Query(None, description="按 SKU 编码筛选"),
    product_code: Optional[str] = Query(None, description="按产品编码筛选"),
    limit: int = Query(300, le=1000),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """工厂调价历史: 列出定价版本区间。每行 = 该 [period_start, period_end) 区间使用的**旧值**
    (即在 period_end 这天调价前的价), period_end = 调价生效日; 最新价见定价表本身。"""
    from app.models.pricing_version import PricingSkuVersion
    q = select(PricingSkuVersion)
    if sku_code:
        q = q.where(PricingSkuVersion.sku_code == sku_code)
    if product_code:
        q = q.where(PricingSkuVersion.product_code == product_code)
    q = q.order_by(PricingSkuVersion.created_at.desc()).limit(limit)
    rows = db.execute(q).scalars().all()
    codes = {r.sku_code for r in rows}
    names = (dict(db.execute(
        select(PricingSku.sku_code, PricingSku.sku).where(PricingSku.sku_code.in_(codes))).all())
        if codes else {})

    def _m(v):
        return float(v) if v is not None else None

    return [{
        "id": r.id, "sku_code": r.sku_code, "sku": names.get(r.sku_code), "product_code": r.product_code,
        "period_start": r.period_start.isoformat() if r.period_start else None,
        "period_end": r.period_end.isoformat() if r.period_end else None,
        "physical_cost": _m(r.physical_cost), "factory_cost": _m(r.factory_cost),
        "list_price": _m(r.list_price), "daily_price": _m(r.daily_price),
        "small_promo": _m(r.small_promo), "mid_promo": _m(r.mid_promo), "big_promo": _m(r.big_promo),
        "note": r.note, "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


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
