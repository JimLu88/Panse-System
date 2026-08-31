"""One exact unsaved calibration for the lift-desk backup SKU option.

The destination and manifest are fixed.  It uploads only the official option
template and closes the browser before the product-save action.  It cannot
withdraw an activity, save a product, change inventory/price, or submit a
campaign.
"""
from __future__ import annotations

import json

from app.database import SessionLocal
from app.services import web_agent_service


MANIFEST = {
    "item_id": "793202812082",
    "source_merchant_code": "PPS2441004051311",
    "target_merchant_code": "PPS2441004051311B1",
    "source_option": "130cm 带高台",
    "new_option": "130cm 带高台（备用1）",
}


def main() -> int:
    with SessionLocal() as db:
        result = web_agent_service.product_sku_slot_stage(db, MANIFEST)
    # Keep the operator receipt bounded; the screenshot is already retained by
    # the Web-Agent output directory and base64 is not useful in the terminal.
    result.pop("screenshot_base64", None)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
