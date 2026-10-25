"""Import the old isolated FUGE catalogue into the full ERP database.

The legacy SQLite store is read-only input.  Historical prices and competitor
facts are deliberately not activated; products are marked as drafts and the
two confirmed legacy SKUs are inserted without prices.  The command is
idempotent and copies media into the dedicated FUGE gallery only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select

from app.database import SessionLocal
from app.models.pricing import PricingSku
from app.models.product import Product


def _code(source_key: str) -> str:
    return f"FG{source_key.zfill(3)}"


def _safe_name(value: str, limit: int = 96) -> str:
    value = " ".join((value or "").split()).replace("/", "／").replace("\\", "＼")
    return value[:limit].rstrip() or "未命名"


def _json(value: str | None) -> dict:
    try:
        obj = json.loads(value or "{}")
        return obj if isinstance(obj, dict) else {}
    except (TypeError, ValueError):
        return {}


def _copy_media(legacy_root: Path, gallery_root: Path, rows: list[sqlite3.Row], names: dict[str, str]) -> tuple[int, int]:
    copied = skipped = 0
    for row in rows:
        src = legacy_root / row["path"]
        if not src.is_file():
            raise FileNotFoundError(f"legacy media missing: {src}")
        raw_sha = hashlib.sha256(src.read_bytes()).hexdigest()
        if raw_sha != row["sha256"]:
            raise ValueError(f"legacy media hash mismatch: {src.name}")
        product_dir = gallery_root / f"{_code(names[row['catalog_id']][0])} {_safe_name(names[row['catalog_id']][1])}"
        category = row["category"] if row["category"] in {"主图", "SKU图", "详情页", "评价图"} else "其他"
        original = Path(row["original_name"])
        target = product_dir / category / original
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != row["sha256"]:
                stem, suffix = target.stem, target.suffix
                target = target.with_name(f"{stem}_{row['sha256'][:8]}{suffix}")
            else:
                skipped += 1
                continue
        shutil.copyfile(src, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != row["sha256"]:
            raise ValueError(f"copied media hash mismatch: {target}")
        copied += 1
    return copied, skipped


def run(legacy_root: Path, gallery_root: Path) -> dict[str, int]:
    legacy_db = legacy_root / "store.sqlite"
    if not legacy_db.is_file():
        raise FileNotFoundError(legacy_db)
    src = sqlite3.connect(legacy_db)
    src.row_factory = sqlite3.Row
    catalog = list(src.execute("SELECT id, source_key, title, metadata FROM catalog ORDER BY source_key"))
    media = list(src.execute("SELECT catalog_id, category, sha256, path, original_name FROM catalog_media ORDER BY id"))
    legacy_products = list(src.execute("SELECT code, name, specification FROM products ORDER BY code"))
    names = {row["id"]: (row["source_key"], row["title"]) for row in catalog}

    with SessionLocal() as db:
        for row in catalog:
            meta = _json(row["metadata"])
            code = _code(row["source_key"])
            product = db.execute(select(Product).where(Product.code == code)).scalar_one_or_none()
            if product is None:
                product = Product(code=code, name=row["title"], brand="孚格", listing_status="draft")
                db.add(product)
            product.name = row["title"]
            product.brand = product.brand or "孚格"
            product.listing_status = product.listing_status or "draft"
            product.main_material = meta.get("material") or product.main_material
            product.description = meta.get("description") or product.description
            product.remark = "旧孚格轻量系统资料导入；价格、库存和平台状态待核实"

        for row in legacy_products:
            product_code = row["code"].rsplit("-", 1)[0]
            sku = db.execute(select(PricingSku).where(PricingSku.sku_code == row["code"])).scalar_one_or_none()
            if sku is None:
                sku = PricingSku(
                    product_code=product_code,
                    product_name=row["name"],
                    sku_code=row["code"],
                    sku=row["specification"],
                    size_info=row["specification"],
                    remark="旧孚格确认 SKU 草稿；未迁移历史价格",
                )
                db.add(sku)
            else:
                sku.product_code = product_code
                sku.product_name = row["name"]
                sku.sku = row["specification"]
                sku.size_info = row["specification"]
                sku.remark = "旧孚格确认 SKU 草稿；未迁移历史价格"
        db.commit()

    copied, skipped = _copy_media(legacy_root, gallery_root, media, names)

    # Refresh product image_url from the first migrated main image, leaving SKU
    # prices and all platform fields untouched.
    with SessionLocal() as db:
        for row in catalog:
            code = _code(row["source_key"])
            product = db.execute(select(Product).where(Product.code == code)).scalar_one_or_none()
            if not product or product.image_url:
                continue
            candidate = next((m for m in media if m["catalog_id"] == row["id"] and m["category"] == "主图"), None)
            if candidate:
                rel = f"{code} {_safe_name(row['title'])}/主图/{candidate['original_name']}"
                product.image_url = f"/api/gallery/file?path={quote(rel)}&thumb=1"
        db.commit()
    return {"products": len(catalog), "legacy_skus": len(legacy_products), "media_copied": copied, "media_skipped": skipped}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", type=Path, required=True, help="mounted legacy store directory")
    parser.add_argument("--gallery", type=Path, required=True, help="dedicated FUGE gallery root")
    args = parser.parse_args()
    print(json.dumps(run(args.legacy, args.gallery), ensure_ascii=False, sort_keys=True))
