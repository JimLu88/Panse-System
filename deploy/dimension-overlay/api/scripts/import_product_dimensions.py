"""把 PSD 转换项目的 39 份 SVG/JSON/预览图导入畔色 ERP。

示例（API 容器内）：
  python scripts/import_product_dimensions.py --asset-root /tmp/dimension-assets
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path, PureWindowsPath

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.models.product_dimension import ProductDimensionAsset  # noqa: E402
from app.services import product_dimension_service  # noqa: E402


def _asset_key(source_psd: str) -> str:
    return "psd-" + hashlib.sha1(source_psd.encode("utf-8")).hexdigest()[:16]


def _source_file(asset_root: Path, value: str | None, fallback: str) -> Path:
    raw = value or fallback
    # The index is authored on Windows, while this importer runs in Linux.
    # Normalize Windows separators before resolving everything under the
    # explicit import root mounted in the API container.
    name = PureWindowsPath(raw).name if "\\" in raw else Path(raw).name
    return asset_root / name


def _publish_hash(paths: list[Path]) -> str:
    """Return a stable digest for one final product deliverable set."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="导入产品细节尺寸 SVG 到 ERP 持久化目录")
    parser.add_argument("--asset-root", required=True, type=Path, help="含 manual_asset_index.json 的目录")
    parser.add_argument("--storage-root", type=Path, help="覆盖 PRODUCT_DIMENSION_ROOT")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    asset_root = args.asset_root.resolve()
    if args.storage_root:
        os.environ["PRODUCT_DIMENSION_ROOT"] = str(args.storage_root.resolve())
    index_path = asset_root / "manual_asset_index.json"
    if not index_path.is_file():
        raise SystemExit(f"缺少 {index_path}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    products = index.get("products") or []
    if len(products) != 39:
        raise SystemExit(f"资产索引应为 39 份，实际 {len(products)}")

    prepared = []
    errors: list[str] = []
    db = SessionLocal()
    try:
        erp_codes = {p.get("erp_code") for p in products if p.get("erp_code")}
        existing_products = {
            p.code: p for p in db.execute(select(Product).where(Product.code.in_(erp_codes))).scalars().all()
        }
        for item in products:
            code = item.get("erp_code")
            source_psd = item.get("source_psd") or f"{item.get('product')}.psd"
            if not code or code not in existing_products:
                errors.append(f"{source_psd}: ERP 产品 {code or '(空)'} 不存在")
                continue
            svg = _source_file(asset_root, item.get("svg_path"), f"{item['product']}.svg")
            preview = _source_file(asset_root, item.get("preview_path"), f"{item['product']}_preview.png")
            dimensions = _source_file(
                asset_root, item.get("dimensions_path"), f"{item['product']}.dimensions.json"
            )
            final_text = asset_root / f"{item['product']}_说明.txt"
            for required in (svg, dimensions, preview, final_text):
                if not required.is_file():
                    errors.append(f"{source_psd}: 缺少 {required.name}")
            if not all(path.is_file() for path in (svg, dimensions, preview, final_text)):
                continue
            svg_text = svg.read_text(encoding="utf-8")
            try:
                product_dimension_service.validate_and_parse_svg(svg_text)
                dimension_data = json.loads(dimensions.read_text(encoding="utf-8"))
                note_text = final_text.read_text(encoding="utf-8").strip()
                if not note_text:
                    raise ValueError(f"{final_text.name} 为空")
            except Exception as exc:  # noqa: BLE001 - 汇总全部预检失败
                errors.append(f"{source_psd}: {exc}")
                continue
            publish_hash = _publish_hash([svg, dimensions, preview, final_text])
            dimension_data["final_text"] = note_text
            dimension_data["publish_hash"] = publish_hash
            dimension_data["published_at"] = datetime.now().astimezone().isoformat()
            prepared.append(
                (item, source_psd, svg, preview, dimensions, final_text, svg_text, dimension_data)
            )

        if errors:
            print(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2))
            return 2
        if args.dry_run:
            print(json.dumps({"ok": True, "dry_run": True, "validated": len(prepared)}, ensure_ascii=False))
            return 0

        imported = 0
        updated = 0
        changed = 0
        unchanged = 0
        affected_codes: set[str] = set()
        storage_root = product_dimension_service.get_root()
        storage_root.mkdir(parents=True, exist_ok=True)
        for item, source_psd, svg, preview, dimensions, final_text, svg_text, dimension_data in prepared:
            code = item["erp_code"]
            key = _asset_key(source_psd)
            rel_dir = Path(code) / key
            target_dir = storage_root / rel_dir
            target_dir.mkdir(parents=True, exist_ok=True)

            asset = db.execute(
                select(ProductDimensionAsset).where(
                    ProductDimensionAsset.product_code == code,
                    ProductDimensionAsset.asset_key == key,
                )
            ).scalar_one_or_none()
            if asset is None:
                asset = ProductDimensionAsset(product_code=code, asset_key=key, title=item["product"])
                db.add(asset)
                imported += 1
                asset.version = 1
                asset_changed = True
            else:
                updated += 1
                previous_hash = (asset.dimension_data or {}).get("publish_hash")
                asset_changed = previous_hash != dimension_data["publish_hash"]

            if asset_changed:
                current_version = int(asset.version or 1)
                product_dimension_service.save_versioned_svg(
                    (rel_dir / "current.svg").as_posix(),
                    svg_text,
                    version=current_version,
                    metadata=dimension_data,
                )
                shutil.copy2(preview, target_dir / "preview.png")
                shutil.copy2(final_text, target_dir / "final.txt")
                if asset.id is not None:
                    asset.version = current_version + 1
                changed += 1
            else:
                unchanged += 1

            asset.title = item["product"]
            asset.source_psd = source_psd
            asset.svg_relpath = (rel_dir / "current.svg").as_posix()
            asset.preview_relpath = (rel_dir / "preview.png").as_posix()
            asset.metadata_relpath = (rel_dir / "metadata.json").as_posix()
            if asset_changed:
                asset.dimension_data = dimension_data
            asset.erp_dimensions = item.get("erp_dimensions") or []
            asset.sku_variants = item.get("erp_variants") or []
            asset.mapping_status = "review_required" if item.get("review_required") else "confirmed"
            asset.match_confidence = item.get("erp_match_confidence")
            if asset_changed:
                asset.updated_by = "最终尺寸文件自动发布"
            affected_codes.add(code)
        db.flush()

        by_code: dict[str, list[ProductDimensionAsset]] = defaultdict(list)
        for asset in db.execute(
            select(ProductDimensionAsset).where(ProductDimensionAsset.product_code.in_(affected_codes))
        ).scalars().all():
            by_code[asset.product_code].append(asset)
        for assets in by_code.values():
            preferred = sorted(
                assets,
                key=lambda a: (
                    a.mapping_status != "confirmed",
                    a.match_confidence != "high",
                    a.id or 0,
                ),
            )[0]
            for asset in assets:
                asset.is_primary = asset is preferred
        db.commit()
        result = {
            "ok": True,
            "validated": len(prepared),
            "inserted": imported,
            "updated": updated,
            "changed": changed,
            "unchanged": unchanged,
            "unique_products": len(affected_codes),
            "review_required": sum(1 for p in products if p.get("review_required")),
            "storage_root": str(storage_root),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
