"""One exact unsaved calibration for the named lift-desk SKU option.

The destination and manifest are fixed.  It uploads only the official option
template and closes the browser before the product-save action.  It cannot
withdraw an activity, save a product, change inventory/price, or submit a
campaign.
"""
from __future__ import annotations

import json

from app.database import SessionLocal
from app.services import sku_identity_service, web_agent_service


MANIFEST = {
    "item_id": "793202812082",
    "source_merchant_code": "PPS2441004051311",
    "target_merchant_code": "PPS2441004051311B1",
    "source_option": "130cm 带高台",
    "new_option": "130cm 带高台升降桌",
    "erp_price_guard": {
        "list_price": "9100.00",
        "daily_price": "6825.00",
        "small_promo": "4190.00",
        "mid_promo": "4050.00",
        "big_promo": "3830.00",
    },
}


def main() -> int:
    with SessionLocal() as db:
        sku_identity_service.ensure_lift_desk_proposal(
            db, authorization_ref="user:2026-08-31:lift-desk-pilot")
        result = web_agent_service.product_sku_slot_stage(db, MANIFEST)
        if result.get("ok"):
            result["ledger_proposal"] = sku_identity_service.mark_lift_desk_staged_unsaved(
                db, result=result)
        else:
            result["ledger_proposal"] = sku_identity_service.mark_lift_desk_stage_failed(
                db, result=result)
        db.commit()
    # Keep the operator receipt bounded; the screenshot is already retained by
    # the Web-Agent output directory and base64 is not useful in the terminal.
    result.pop("screenshot_base64", None)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
