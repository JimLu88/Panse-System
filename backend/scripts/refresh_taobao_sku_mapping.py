"""Refresh exact ERP SKU mappings from Taobao product-export workbooks."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.database import SessionLocal
from app.services import sku_rotation_service


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action="append", required=True)
    parser.add_argument("--item-id", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    workbooks = [Path(path).read_bytes() for path in args.file]
    db = SessionLocal()
    try:
        result = sku_rotation_service.apply_export_mapping_refresh(
            db,
            workbooks,
            item_ids=args.item_id,
            dry_run=not args.apply,
        )
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0 if result.get("ok") else 2
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
