"""Auditable whole-item exclusions for campaign enrollment.

An explicit item record is authoritative.  The only automatic derivation is an
item whose every mapped ERP SKU is already marked ``is_custom_placeholder``.
Names and free text are never used, preventing mixed normal-product links from
being excluded because one SKU contains words such as 定制 or 差价.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignItemExclusion
from app.services import no_sales_service


# User-owned permanent scope rule.  This is intentionally code-backed rather
# than a mutable database row: warehouse merchandise is not a campaign product,
# and an operator must not be able to reactivate it through the generic item-
# exclusion deactivate endpoint.
WAREHOUSE_USER_RULE_ITEM_ID = "1038725569412"
WAREHOUSE_USER_RULE_SKU_ID = "6060112621275"


def fixed_user_rule_items(mapped_pairs: list[tuple]) -> dict[str, dict]:
    mapped_item_ids = {
        str(getattr(promo, "taobao_item_id", "") or "").strip()
        for _sku, promo in mapped_pairs
    }
    if WAREHOUSE_USER_RULE_ITEM_ID not in mapped_item_ids:
        return {}
    return {
        WAREHOUSE_USER_RULE_ITEM_ID: {
            "taobao_item_id": WAREHOUSE_USER_RULE_ITEM_ID,
            "taobao_sku_id": WAREHOUSE_USER_RULE_SKU_ID,
            "reason": "用户明确：仓库商品永久不报名、不改价",
            "source": "user_rule_20260901",
            "mode": "fixed_user_rule",
        },
    }


def explicit_items(db: Session) -> dict[str, dict]:
    rows = db.execute(
        select(CampaignItemExclusion).where(CampaignItemExclusion.active.is_(True))
    ).scalars().all()
    return {
        row.taobao_item_id: {
            "taobao_item_id": row.taobao_item_id,
            "reason": row.reason,
            "source": row.source,
            "mode": "explicit_item_marker",
        }
        for row in rows
        if no_sales_service.is_valid_item_id(row.taobao_item_id)
    }


def derived_all_placeholder_items(mapped_pairs: list[tuple]) -> dict[str, dict]:
    by_item: dict[str, list] = {}
    for sku, promo in mapped_pairs:
        item_id = str(getattr(promo, "taobao_item_id", "") or "").strip()
        if not no_sales_service.is_valid_item_id(item_id):
            continue
        by_item.setdefault(item_id, []).append(sku)
    return {
        item_id: {
            "taobao_item_id": item_id,
            "reason": "该商品全部已映射 SKU 均为 is_custom_placeholder",
            "source": "erp_all_mapped_skus_placeholder",
            "mode": "derived_authoritative_field",
        }
        for item_id, skus in by_item.items()
        if skus and all(bool(getattr(sku, "is_custom_placeholder", False)) for sku in skus)
    }


def effective_items(db: Session, mapped_pairs: list[tuple]) -> dict[str, dict]:
    result = derived_all_placeholder_items(mapped_pairs)
    result.update(explicit_items(db))
    # Fixed user rules win over both derived and mutable explicit markers.
    result.update(fixed_user_rule_items(mapped_pairs))
    return result


def upsert(db: Session, *, item_id: str, reason: str,
           source: str = "operator_confirmed") -> CampaignItemExclusion:
    item_id = str(item_id or "").strip()
    if not no_sales_service.is_valid_item_id(item_id):
        raise ValueError("淘宝商品号必须是4至20位数字")
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("整条链接排除必须填写可审计原因")
    row = db.execute(select(CampaignItemExclusion).where(
        CampaignItemExclusion.taobao_item_id == item_id)).scalar_one_or_none()
    if row is None:
        row = CampaignItemExclusion(
            taobao_item_id=item_id, reason=reason, source=source, active=True)
        db.add(row)
    else:
        row.reason = reason
        row.source = source
        row.active = True
    db.commit()
    db.refresh(row)
    return row


def deactivate(db: Session, item_id: str) -> bool:
    row = db.execute(select(CampaignItemExclusion).where(
        CampaignItemExclusion.taobao_item_id == str(item_id).strip())).scalar_one_or_none()
    if row is None or not row.active:
        return False
    row.active = False
    db.commit()
    return True
