"""淘宝商品链接追加/切主流程。

外部商品链接会更换，但 ERP 产品、定价 SKU、BOM 和历史订单继续使用稳定的
``product_code`` / ``sku_code``。本服务只维护外部链接别名与逐 SKU 对应关系：

- ``add``：导入新商品及 SKU 对应，加入产品备用商品 ID；活动仍使用旧主链接。
- ``activate``：在完整预检后把产品与活动报名映射切到新链接，旧商品 ID 和旧
  SKU 对应仍保留在 ``taobao_listings`` 供历史订单追溯。
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignPlan
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.product import Product
from app.models.taobao_listing import TaobaoListing
from app.services import taobao_listing_service


_ACTIVE_PLAN_STATUSES = ("draft", "precheck", "discount_pushed", "signup_pushed")


def canonical_taobao_url(item_id: str) -> str:
    return f"https://item.taobao.com/item.htm?id={item_id}"


def _norm(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _spec_key(value: Any) -> str:
    text = _norm(value).replace("：", ":").replace("；", ";")
    return re.sub(r"\s+", "", text).rstrip(";")


def _promo_sku_index(db: Session) -> dict[str, str]:
    out: dict[str, str] = {}
    for promo in db.execute(select(PricingSkuPromo)).scalars():
        for sku_id in [promo.taobao_sku_id, *(promo.alt_taobao_sku_ids or [])]:
            sid = _norm(sku_id)
            if sid:
                out.setdefault(sid, promo.sku_code)
    return out


def _legacy_spec_index(db: Session, product_code: str) -> dict[str, set[str]]:
    promo_by_sid = _promo_sku_index(db)
    out: dict[str, set[str]] = defaultdict(set)
    rows = db.execute(
        select(TaobaoListing).where(TaobaoListing.product_code == product_code)
    ).scalars()
    for row in rows:
        sku_code = row.sku_code or promo_by_sid.get(_norm(row.taobao_sku_id))
        key = _spec_key(row.sku_spec)
        if key and sku_code:
            out[key].add(sku_code)
    return out


def preview(
    db: Session,
    file_bytes: bytes,
    *,
    product_code: str,
    mode: str = "add",
) -> dict:
    """只读预检导出表，返回确定的逐 SKU 映射；任何歧义均拒绝继续。"""
    if mode not in {"add", "activate"}:
        raise ValueError("mode 只能是 add 或 activate")
    product_code = _norm(product_code)
    product = db.execute(
        select(Product).where(Product.code == product_code)
    ).scalar_one_or_none()
    if product is None:
        raise ValueError(f"产品不存在: {product_code}")

    records, warnings = taobao_listing_service.parse_rows(file_bytes)
    if not records:
        raise ValueError("导出表没有可迁移的商品行: " + "; ".join(warnings))
    item_ids = {_norm(row.get("taobao_item_id")) for row in records if row.get("taobao_item_id")}
    if len(item_ids) != 1:
        raise ValueError(f"一次只能迁移一个淘宝商品，当前识别到 {sorted(item_ids)}")
    item_id = next(iter(item_ids))

    target_skus = {
        row.sku_code: row
        for row in db.execute(
            select(PricingSku).where(PricingSku.product_code == product_code)
        ).scalars()
    }
    if not target_skus:
        raise ValueError(f"产品 {product_code} 没有定价 SKU")
    legacy_by_spec = _legacy_spec_index(db, product_code)

    mappings: list[dict] = []
    errors: list[str] = []
    for row in records:
        sku_id = _norm(row.get("taobao_sku_id"))
        raw_code = _norm(row.get("sku_code_raw"))
        merchant_code = _norm(row.get("merchant_code"))
        sku_code = raw_code if raw_code in target_skus else ""
        if not sku_code and merchant_code in target_skus:
            sku_code = merchant_code
        if not sku_code:
            candidates = legacy_by_spec.get(_spec_key(row.get("sku_spec")), set())
            if len(candidates) == 1:
                sku_code = next(iter(candidates))
        if not sku_id:
            errors.append(f"{row.get('sku_spec') or '(空规格)'} 缺少淘宝 skuId")
            continue
        if not sku_code:
            errors.append(f"skuId {sku_id} 无法唯一匹配 {product_code} 的内部 SKU")
            continue
        mappings.append({
            "taobao_item_id": item_id,
            "taobao_sku_id": sku_id,
            "sku_code": sku_code,
            "product_code": product_code,
            "title": row.get("title"),
            "merchant_code": row.get("merchant_code"),
            "sku_spec": row.get("sku_spec"),
            "category_name": row.get("category_name"),
            "list_price": row.get("list_price"),
            "sku_price": row.get("sku_price"),
            "stock": row.get("stock"),
        })

    duplicate_ids = sorted({
        sid for sid in (m["taobao_sku_id"] for m in mappings)
        if sum(1 for x in mappings if x["taobao_sku_id"] == sid) > 1
    })
    duplicate_codes = sorted({
        code for code in (m["sku_code"] for m in mappings)
        if sum(1 for x in mappings if x["sku_code"] == code) > 1
    })
    if duplicate_ids:
        errors.append(f"导出表存在重复 skuId: {duplicate_ids}")
    if duplicate_codes:
        errors.append(f"多个淘宝 SKU 指向同一内部 SKU: {duplicate_codes}")

    mapped_codes = {m["sku_code"] for m in mappings}
    missing_codes = sorted(set(target_skus) - mapped_codes)
    extra_codes = sorted(mapped_codes - set(target_skus))
    if missing_codes:
        errors.append(f"新链接缺少 {len(missing_codes)} 个内部 SKU: {missing_codes}")
    if extra_codes:
        errors.append(f"新链接包含其它产品 SKU: {extra_codes}")

    for mapping in mappings:
        existing_rows = db.execute(
            select(TaobaoListing).where(
                TaobaoListing.taobao_sku_id == mapping["taobao_sku_id"],
            )
        ).scalars().all()
        for existing in existing_rows:
            if (
                existing.taobao_item_id != item_id
                or existing.product_code != product_code
                or existing.sku_code != mapping["sku_code"]
            ):
                errors.append(
                    f"skuId {mapping['taobao_sku_id']} 已绑定到 "
                    f"{existing.taobao_item_id}/{existing.product_code}/{existing.sku_code}"
                )

    promos = {
        row.sku_code: row
        for row in db.execute(
            select(PricingSkuPromo).where(PricingSkuPromo.sku_code.in_(target_skus))
        ).scalars()
    }
    if mode == "activate":
        missing_promos = sorted(set(target_skus) - set(promos))
        if missing_promos:
            errors.append(f"活动映射缺少 {len(missing_promos)} 个 SKU: {missing_promos}")
        active_plans = db.execute(
            select(CampaignPlan.id, CampaignPlan.name, CampaignPlan.status).where(
                CampaignPlan.status.in_(_ACTIVE_PLAN_STATUSES)
            )
        ).all()
        if active_plans:
            errors.append(
                "存在未收口活动计划，切主会改变其商品映射: "
                + ", ".join(f"#{p.id} {p.name}({p.status})" for p in active_plans)
            )

    if errors:
        raise ValueError("；".join(errors))

    old_primary = _norm(product.taobao_id) or None
    alternatives = [str(x) for x in (product.alt_taobao_ids or []) if _norm(x)]
    future_aliases = list(dict.fromkeys([
        *alternatives,
        *([item_id] if mode == "add" and item_id != old_primary else []),
        *([old_primary] if mode == "activate" and old_primary and old_primary != item_id else []),
    ]))
    future_aliases = [value for value in future_aliases if value != item_id or mode == "add"]
    if len(future_aliases) > 5:
        raise ValueError("迁移后备用淘宝商品 ID 超过 5 个，请先人工整理")
    return {
        "mode": mode,
        "product_id": product.id,
        "product_code": product.code,
        "product_name": product.name,
        "new_item_id": item_id,
        "new_url": canonical_taobao_url(item_id),
        "old_primary_item_id": old_primary,
        "old_alternative_item_ids": alternatives,
        "sku_count": len(mappings),
        "internal_sku_count": len(target_skus),
        "warnings": warnings,
        "mappings": sorted(mappings, key=lambda x: x["sku_code"]),
        "effects": {
            "product_primary_changes": mode == "activate" and old_primary != item_id,
            "product_alias_added": item_id != old_primary and item_id not in alternatives,
            "campaign_mapping_changes": len(mappings) if mode == "activate" else 0,
            "bom_changes": 0,
            "historical_order_changes": 0,
        },
    }


def apply(
    db: Session,
    file_bytes: bytes,
    *,
    product_code: str,
    mode: str = "add",
    shop: str | None = None,
) -> dict:
    """执行已经通过 ``preview`` 的追加或切主，并在一个事务内提交。"""
    report = preview(db, file_bytes, product_code=product_code, mode=mode)
    product = db.get(Product, report["product_id"])
    inserted = updated = 0
    for mapping in report["mappings"]:
        row = db.execute(
            select(TaobaoListing).where(
                TaobaoListing.taobao_item_id == mapping["taobao_item_id"],
                TaobaoListing.taobao_sku_id == mapping["taobao_sku_id"],
            )
        ).scalar_one_or_none()
        payload = {**mapping, "matched": True}
        if shop:
            payload["shop"] = shop
        if row is None:
            row = TaobaoListing(**payload)
            db.add(row)
            inserted += 1
        else:
            for key, value in payload.items():
                setattr(row, key, value)
            updated += 1

    new_item_id = report["new_item_id"]
    old_primary = _norm(product.taobao_id)
    old_alts = [str(x) for x in (product.alt_taobao_ids or []) if _norm(x)]
    if mode == "add":
        if new_item_id != old_primary and new_item_id not in old_alts:
            old_alts.append(new_item_id)
        product.alt_taobao_ids = old_alts
    else:
        aliases = [old_primary, *old_alts]
        product.taobao_id = new_item_id
        product.alt_taobao_ids = list(dict.fromkeys(
            value for value in aliases if value and value != new_item_id
        ))
        if len(product.alt_taobao_ids) > 5:
            raise ValueError("切主后备用淘宝商品 ID 超过 5 个，请先人工整理")
        for mapping in report["mappings"]:
            promo = db.execute(
                select(PricingSkuPromo).where(
                    PricingSkuPromo.sku_code == mapping["sku_code"]
                )
            ).scalar_one()
            promo.taobao_item_id = new_item_id
            promo.taobao_url = report["new_url"]
            promo.taobao_sku_id = mapping["taobao_sku_id"]
            # 旧链接 skuId 不能作为新 item_id 的 alt 继续报名；历史对应保存在 taobao_listings。
            promo.alt_taobao_sku_ids = []
            # 平台价格线属于外部 skuId，换号后不得继承旧 skuId 的缓存证据。
            promo.enrolled_floor_price = None
            promo.coupon_floor_price = None

    db.commit()
    return {
        **{k: v for k, v in report.items() if k != "mappings"},
        "inserted": inserted,
        "updated": updated,
        "matched": len(report["mappings"]),
        "applied": True,
    }
