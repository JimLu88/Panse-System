"""Database-only SKU identity ledger bootstrap and read-only query."""
from __future__ import annotations

import argparse
import csv
import json
import sys

from app.database import SessionLocal
from app.services import sku_identity_service


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("backfill", "query"))
    parser.add_argument("--item-id")
    parser.add_argument("--merchant-code")
    parser.add_argument("--format", choices=("json", "csv"), default="json")
    args = parser.parse_args()
    with SessionLocal() as db:
        if args.action == "backfill":
            result = sku_identity_service.backfill_from_erp(db)
            result["lift_desk_proposal"] = sku_identity_service.ensure_lift_desk_proposal(
                db, authorization_ref="user:2026-08-31:lift-desk-pilot")
            db.commit()
        else:
            result = sku_identity_service.query(
                db, item_id=args.item_id, merchant_code=args.merchant_code)
    if args.format == "csv":
        fields = ["item_id", "taobao_sku_id", "merchant_code", "sku_spec", "sku_code",
                  "product_code", "placeholder", "daily_price", "sale_state",
                  "first_observed_at", "last_observed_at", "evidence_source",
                  "evidence_sha256", "identity_conflict"]
        writer = csv.DictWriter(sys.stdout, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(result["items"])
    else:
        print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
