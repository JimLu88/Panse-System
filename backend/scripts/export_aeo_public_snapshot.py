"""Export a read-only, privacy-safe product snapshot for the Panse AEO project.

Only product-facing fields are exported. The script never queries orders,
customers, costs, suppliers or factory records and never commits a transaction.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

_SOURCE = globals().get("__file__", "")
BACKEND = (
    Path.cwd()
    if str(_SOURCE).startswith("<")
    else Path(str(_SOURCE)).resolve().parents[1]
)
sys.path.insert(0, str(BACKEND))

from app.database import SessionLocal, engine  # noqa: E402
from app.models.pricing import PricingSku  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.product_dimension import ProductDimensionAsset  # noqa: E402


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def _read_only(session: Session) -> None:
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))
    elif dialect == "sqlite":
        session.execute(text("PRAGMA query_only = ON"))


def build_snapshot(session: Session) -> dict[str, Any]:
    _read_only(session)
    products = list(session.scalars(select(Product).order_by(Product.code)))
    pricing = list(
        session.scalars(
            select(PricingSku)
            .where(PricingSku.is_custom_placeholder.is_(False))
            .order_by(PricingSku.product_code, PricingSku.sku_code)
        )
    )
    dimensions = list(
        session.scalars(
            select(ProductDimensionAsset)
            .where(ProductDimensionAsset.mapping_status == "confirmed")
            .order_by(ProductDimensionAsset.product_code, ProductDimensionAsset.asset_key)
        )
    )
    return {
        "schemaVersion": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "read-only-public-product-snapshot",
        "source": {
            "system": "Panse ERP",
            "databaseBackend": session.get_bind().dialect.name,
        },
        "scope": {
            "included": [
                "product identity",
                "public materials",
                "public dimensions",
                "daily selling price",
                "public images",
                "listing status",
            ],
            "excluded": ["orders", "customers", "costs", "suppliers", "factory records"],
        },
        "products": [
            {
                "code": row.code,
                "skuCode": row.sku_code,
                "name": row.name,
                "subName": row.sub_name,
                "brand": row.brand,
                "category": row.category,
                "priority": row.priority,
                "imageUrl": row.image_url,
                "customScope": row.custom_scope,
                "sizeDetail": row.size_detail,
                "sizeValue": row.size_value,
                "sizeConfirmed": row.size_confirmed,
                "mainMaterial": row.main_material,
                "auxMaterial": row.aux_material,
                "description": row.description,
                "listingStatus": row.listing_status,
                "updatedAt": _json_value(row.updated_at),
            }
            for row in products
        ],
        "pricing": [
            {
                "productCode": row.product_code,
                "skuCode": row.sku_code,
                "productName": row.product_name,
                "sku": row.sku,
                "sizeInfo": row.size_info,
                "dailyPrice": _json_value(row.daily_price),
                "imageUrl": row.image_url,
                "updatedAt": _json_value(row.updated_at),
            }
            for row in pricing
        ],
        "dimensions": [
            {
                "productCode": row.product_code,
                "assetKey": row.asset_key,
                "title": row.title,
                "dimensionData": row.dimension_data,
                "erpDimensions": row.erp_dimensions,
                "skuVariants": row.sku_variants,
                "version": row.version,
                "updatedAt": _json_value(row.updated_at),
            }
            for row in dimensions
        ],
        "counts": {
            "products": len(products),
            "pricing": len(pricing),
            "dimensions": len(dimensions),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export AEO-safe public product snapshot")
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--out", type=Path)
    destination.add_argument(
        "--stdout",
        action="store_true",
        help="emit the privacy-safe JSON to stdout for an SSH read-only bridge",
    )
    args = parser.parse_args()
    session = SessionLocal()
    try:
        try:
            snapshot = build_snapshot(session)
        except SQLAlchemyError as exc:
            print(
                "[blocked] 当前 ERP 数据连接不可读取公开产品表；未生成快照。"
                f" error={type(exc).__name__}",
                file=sys.stderr,
            )
            return 2
    finally:
        session.rollback()
        session.close()
    if args.stdout:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0
    assert args.out is not None
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_suffix(args.out.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(args.out)
    print(json.dumps(snapshot["counts"], ensure_ascii=False))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
