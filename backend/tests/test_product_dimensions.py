import json

import pytest
from fastapi import HTTPException

from app.api.products import ProductDimensionSave, save_product_dimension
from app.models.auth import User
from app.models.product import Product
from app.models.product_dimension import ProductDimensionAsset
from app.services import product_dimension_service


SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
  <g id="product-body"><path d="M10 10h80v80H10z"/></g>
  <g id="dimensions-editable">
    <text id="dim-1" x="50" y="8">1000</text>
  </g>
</svg>
"""


def _seed(db, tmp_path, *, mapping_status="confirmed"):
    product = Product(code="P-DIM-1", name="测试尺寸产品", size_detail="旧尺寸")
    user = User(username="editor", display_name="尺寸编辑员", password_hash="x", role="admin")
    db.add_all([product, user])
    db.flush()
    folder = tmp_path / "P-DIM-1" / "asset-a"
    folder.mkdir(parents=True)
    (folder / "current.svg").write_text(SVG, encoding="utf-8")
    asset = ProductDimensionAsset(
        product_code=product.code,
        asset_key="asset-a",
        title="正视图",
        svg_relpath="P-DIM-1/asset-a/current.svg",
        metadata_relpath="P-DIM-1/asset-a/metadata.json",
        dimension_data={"labels": [{"id": "dim-1", "value": "1000", "source": "psd_live_text"}]},
        erp_dimensions=[],
        sku_variants=[],
        mapping_status=mapping_status,
        version=1,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return product, user, asset


def test_save_svg_and_size_detail_are_versioned_together(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCT_DIMENSION_ROOT", str(tmp_path))
    product, user, asset = _seed(db_session, tmp_path)
    edited = SVG.replace(">1000<", ">1200<")
    result = save_product_dimension(
        product.code,
        asset.id,
        ProductDimensionSave(
            svg=edited,
            expected_version=1,
            size_detail="总长：1200mm",
            sync_size_detail=True,
            confirm_mapping=True,
        ),
        db_session,
        user,
    )
    assert result["version"] == 2
    assert result["size_detail"] == "总长：1200mm"
    assert result["backup"].startswith("P-DIM-1/asset-a/versions/")
    assert ">1200<" in (tmp_path / asset.svg_relpath).read_text(encoding="utf-8")
    metadata = json.loads((tmp_path / "P-DIM-1/asset-a/metadata.json").read_text(encoding="utf-8"))
    assert metadata["labels"][0]["value"] == "1200"
    assert metadata["labels"][0]["source"] == "erp.user_edit"
    assert metadata["labels"][0]["confidence"] == "user_confirmed"


def test_review_mapping_requires_explicit_confirmation(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCT_DIMENSION_ROOT", str(tmp_path))
    product, user, asset = _seed(db_session, tmp_path, mapping_status="review_required")
    with pytest.raises(HTTPException) as exc:
        save_product_dimension(
            product.code,
            asset.id,
            ProductDimensionSave(svg=SVG, expected_version=1, confirm_mapping=False),
            db_session,
            user,
        )
    assert exc.value.status_code == 409


def test_stale_editor_version_is_rejected(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("PRODUCT_DIMENSION_ROOT", str(tmp_path))
    product, user, asset = _seed(db_session, tmp_path)
    asset.version = 3
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        save_product_dimension(
            product.code,
            asset.id,
            ProductDimensionSave(svg=SVG, expected_version=1),
            db_session,
            user,
        )
    assert exc.value.status_code == 409
    assert "v3" in exc.value.detail


def test_svg_security_rejects_script():
    bad = SVG.replace("</svg>", "<script>alert(1)</script></svg>")
    with pytest.raises(HTTPException) as exc:
        product_dimension_service.validate_and_parse_svg(bad)
    assert exc.value.status_code == 400
