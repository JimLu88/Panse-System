"""Import current platform qualification evidence from an activity export.

The historical endpoint name is retained for compatibility, but this importer no
longer treats ``活动价`` as a price floor and never rewrites signup prices.  It
stores the export's actual ``最低标价`` and ``最低普惠券后价`` columns per SKUID,
with an observation timestamp, for campaign preflight only.
"""
from __future__ import annotations

from sqlalchemy.orm import Session


def import_from_xlsx_bytes(db: Session, raw: bytes) -> dict:
    from app.services import campaign_price_floor_service, campaign_recon_service

    records = campaign_recon_service.parse_activity_items_export(raw)
    if not records:
        return {
            "ok": False,
            "error": "未解析到活动商品行；请上传千牛“导出已报商品”的原始 xlsx",
        }
    result = campaign_price_floor_service.record_activity_export(
        db,
        records,
        source="manual_activity_items_export",
    )
    db.commit()
    missing = [
        {
            "item_id": row.get("item_id"),
            "sku_id": row.get("sku_id"),
            "missing": [
                key for key in ("min_list_price", "min_coupon_line")
                if row.get(key) is None
            ],
        }
        for row in records
        if row.get("min_list_price") is None or row.get("min_coupon_line") is None
    ]
    return {
        "ok": True,
        "file_rows": len(records),
        "evidence": result,
        "incomplete_evidence_count": len(missing),
        "incomplete_evidence_sample": missing[:20],
        "note": "仅更新报名资格证据；未修改活动报名价、ERP价格或单品立减",
    }
