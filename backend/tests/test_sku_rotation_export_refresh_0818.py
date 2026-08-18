from decimal import Decimal

import openpyxl
from io import BytesIO
from sqlalchemy import select

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import delisted_sku_service, sku_rotation_service as svc


def _workbook(rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["商品Id", "skuId", "宝贝标题", "商家编码", "商家编码", "销售属性"])
    for row in rows:
        ws.append(row)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _seed(db):
    db.add(PricingSku(
        product_code="PFG25250031226", sku_code="PFG2525003122611",
        sku="中古MCM柜-1.2米", daily_price=Decimal("8630"),
    ))
    db.add(PricingSkuPromo(
        sku_code="PFG2525003122611", taobao_item_id="1047741358718",
        taobao_sku_id="OLD-SID", alt_taobao_sku_ids=["OLDER-SID"],
        coupon_floor_price=Decimal("99"), enrolled_floor_price=Decimal("88"),
    ))
    db.commit()


def test_export_refresh_uses_only_exact_sku_merchant_code(db_session):
    _seed(db_session)
    file_bytes = _workbook([
        ["1047741358718", "NEW-SID", "MCM", "PFG25250031226",
         "PFG2525003122611", "1.2米"],
        # Old row stays in the export but no longer owns a merchant code.
        ["1047741358718", "OLD-SID", "MCM", "PFG25250031226", "", "1.2米"],
    ])
    preview = svc.apply_export_mapping_refresh(
        db_session, [file_bytes], item_ids=["1047741358718"], dry_run=True)
    assert preview["ok"] is True
    assert preview["explicit_mapping_rows"] == 1
    assert preview["changed_rows"] == 1
    promo = db_session.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code == "PFG2525003122611")).scalar_one()
    assert promo.taobao_sku_id == "OLD-SID"


def test_export_refresh_applies_mapping_and_clears_physical_history(db_session):
    _seed(db_session)
    file_bytes = _workbook([
        ["1047741358718", "NEW-SID", "MCM", "PFG25250031226",
         "PFG2525003122611", "1.2米"],
    ])
    result = svc.apply_export_mapping_refresh(
        db_session, [file_bytes], item_ids=["1047741358718"], dry_run=False)
    assert result["ok"] is True
    assert result["changed"][0]["new_sku_id"] == "NEW-SID"
    db_session.expire_all()
    promo = db_session.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code == "PFG2525003122611")).scalar_one()
    assert promo.taobao_sku_id == "NEW-SID"
    assert promo.alt_taobao_sku_ids == []
    assert promo.coupon_floor_price is None
    assert promo.enrolled_floor_price is None


def test_export_refresh_rejects_item_or_product_mismatch(db_session):
    _seed(db_session)
    bad_item = _workbook([
        ["9999999999999", "NEW-SID", "MCM", "PFG25250031226",
         "PFG2525003122611", "1.2米"],
    ])
    missing = svc.apply_export_mapping_refresh(
        db_session, [bad_item], item_ids=["1047741358718"], dry_run=True)
    assert missing["ok"] is False
    assert missing["missing_item_ids"] == ["1047741358718"]

    bad_product = _workbook([
        ["1047741358718", "NEW-SID", "MCM", "WRONG-PRODUCT",
         "PFG2525003122611", "1.2米"],
    ])
    mismatch = svc.apply_export_mapping_refresh(
        db_session, [bad_product], item_ids=["1047741358718"], dry_run=True)
    assert mismatch["ok"] is False
    assert "产品商家编码不符" in mismatch["error"]


def test_export_refresh_retires_short_numeric_marker_rows(db_session):
    _seed(db_session)
    db_session.add(PricingSku(
        product_code="PFG25250031226", sku_code="PFG2525003122698",
        sku="配件", daily_price=Decimal("100"),
    ))
    db_session.add(PricingSkuPromo(
        sku_code="PFG2525003122698", taobao_item_id="1047741358718",
        taobao_sku_id="OLD-ACCESSORY", alt_taobao_sku_ids=[],
    ))
    db_session.commit()
    file_bytes = _workbook([
        ["1047741358718", "NEW-SID", "MCM", "PFG25250031226",
         "PFG2525003122611", "1.2米"],
        ["1047741358718", "OLD-SID", "MCM", "PFG25250031226", "97", "旧1.2米"],
        ["1047741358718", "OLD-ACCESSORY", "MCM", "PFG25250031226", "73", "旧配件"],
    ])

    preview = svc.apply_export_mapping_refresh(
        db_session, [file_bytes], item_ids=["1047741358718"], dry_run=True)
    assert preview["ok"] is True
    assert preview["explicit_mapping_rows"] == 1
    assert preview["retired_mapping_rows"] == 2

    result = svc.apply_export_mapping_refresh(
        db_session, [file_bytes], item_ids=["1047741358718"], dry_run=False)
    assert result["retired_sku_ids"] == ["OLD-ACCESSORY", "OLD-SID"]
    assert delisted_sku_service.get_delisted(db_session) >= {
        "OLD-ACCESSORY", "OLD-SID",
    }
    main = db_session.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code == "PFG2525003122611")).scalar_one()
    accessory = db_session.execute(select(PricingSkuPromo).where(
        PricingSkuPromo.sku_code == "PFG2525003122698")).scalar_one()
    assert main.taobao_sku_id == "NEW-SID"
    assert accessory.taobao_sku_id == "OLD-ACCESSORY"


def test_export_refresh_still_rejects_unknown_real_merchant_code(db_session):
    _seed(db_session)
    file_bytes = _workbook([
        ["1047741358718", "NEW-SID", "MCM", "PFG25250031226",
         "PFG2525003122611", "1.2米"],
        ["1047741358718", "BAD-SID", "MCM", "PFG25250031226",
         "PFG25250031226TYPO", "错误编码"],
    ])
    result = svc.apply_export_mapping_refresh(
        db_session, [file_bytes], item_ids=["1047741358718"], dry_run=True)
    assert result["ok"] is False
    assert "ERP不存在SKU商家编码" in result["error"]


def test_export_refresh_can_limit_to_user_confirmed_sku_codes(db_session):
    _seed(db_session)
    file_bytes = _workbook([
        ["1047741358718", "NEW-SID", "MCM", "PFG25250031226",
         "PFG2525003122611", "1.2米"],
        ["1047741358718", "UNRELATED", "MCM", "PFG25250031226",
         "OTHER-PRODUCT-CODE", "非本次轮换"],
    ])
    result = svc.apply_export_mapping_refresh(
        db_session, [file_bytes], item_ids=["1047741358718"],
        sku_codes=["PFG2525003122611"], dry_run=True)
    assert result["ok"] is True
    assert result["requested_sku_codes"] == ["PFG2525003122611"]
    assert result["explicit_mapping_rows"] == 1
