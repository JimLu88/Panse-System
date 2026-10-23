from decimal import Decimal as D
import pytest
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.product import Product
from app.models.material import Material
from app.services import custom_quote_v2_service as v
from app.services import custom_quote_service as q
from app.services import custom_quote_config_service as cfg


@pytest.fixture
def reference(db_session):
    db = db_session
    db.add(Product(code="BED", name="榉木柔光床", category="卧室-床", main_material="榉木"))
    s = PricingSku(product_code="BED", sku_code="BED14", sku="榉木柔光床-1.8米-榉木铺板",
        size_info="宽度：1850mm；长度：2100mm；高度：880mm", wood_cost=D(2210),
        packaging_cost=D(150), logistics_cost=D(300), install_cost=D(50),
        external_parts_cost=D(750), physical_cost=D(3460), big_promo=D(3950))
    db.add(s)
    db.add(PricingSkuPromo(sku_code="BED14", big_buyer_price=D("4030.61")))
    db.add(Material(code="MW-TEST", name="榉木-2.2cm", price=D(240), unit="平方米"))
    cfg.save_config(db, {"safety_rate": 1.05, "prices": {"榉木-2.2cm": 280}})
    db.commit()
    return db, s


def light(db, **kw):
    return v.quote_light(db, base_product_code="BED", base_sku_code="BED14", price_tier="big_buyer", **kw)


def test_outer_smaller_is_not_above_range(reference):
    db, s = reference
    before = {c.name: getattr(s, c.name) for c in s.__table__.columns}
    r = light(db, outer_dimensions={"length_cm": 200, "width_cm": 170})
    assert r["calculation_basis"]["relation"] == "below_range"
    assert r["calculation_basis"]["points"][0]["coordinate"] == 3.885
    assert r["calculation_basis"]["target"]["length_m"] == 2
    assert r["final_price"] < 4030.61
    assert r["safety_delta"] == 0
    assert r["outer_dimension_basis"]["fees"] == dict(packaging_cost=150, logistics_cost=300, install_cost=50)
    assert not db.dirty
    assert before == {c.name: getattr(s, c.name) for c in s.__table__.columns}


def test_same_outer_size_keeps_exact_buyer_anchor(reference):
    db, _ = reference
    r = light(db, outer_dimensions={"length_cm": 210, "width_cm": 185})
    assert r["anchor"] == r["final_price"] == 4030.61


def test_source_dimension_conflict_is_visible(reference):
    db, s = reference
    r = light(db, outer_dimensions={"length_cm": 200, "width_cm": 170,
        "reference_length_cm": 208, "reference_width_cm": 185, "reference_source": "user drawing"})
    assert r["outer_dimension_basis"]["dimension_conflict"] is True
    assert r["calculation_basis"]["points"][0]["coordinate"] == 3.848
    assert "2100" in s.size_info


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_invalid_dimension_blocks(reference, bad):
    db, _ = reference
    assert light(db, outer_dimensions={"length_cm": bad, "width_cm": 170})["final_price"] is None


def test_ambiguous_old_bed_dimensions_block(reference):
    db, _ = reference
    assert light(db, target_length_m=1.7, target_width_cm=200)["final_price"] is None
    assert light(db, outer_dimensions={"length_cm": 200, "width_cm": 170}, target_length_m=1.7)["final_price"] is None


def test_board_exact_tier_fee_inheritance_material_priority_no_writes(reference):
    db, s = reference
    r = q.quote_from_spec(db, product_type="床", length_m=2,
        boards=[q.BoardSpec("侧板", "榉木-2.2cm", 100, 100)],
        base_product_code="BED", base_sku_code="BED14", price_tier="big_buyer")
    assert (r.packing_fee, r.freight, r.install_fee) == (150, 300, 50)
    assert r.wood_cost == 240
    assert r.final_quote == (r.panse_cost * D("4030.61") / D(3460)).quantize(D(".01"))
    assert r.safety_rate == 1
    assert r.pricing_basis["materials"][0]["config_fallback_price"] == 280
    assert r.pricing_basis["materials"][0]["material_library_price"] == 240
    assert s.big_promo == 3950 and not db.dirty


@pytest.mark.parametrize("field,value", [("base_sku_code", "missing"), ("price_tier", "big"), ("base_product_code", "OTHER")])
def test_board_no_fallback_for_wrong_identity_or_tier(reference, field, value):
    db, _ = reference
    args = dict(base_product_code="BED", base_sku_code="BED14", price_tier="big_buyer")
    args[field] = value
    with pytest.raises(ValueError):
        q.quote_from_spec(db, product_type="床", length_m=2, boards=[], **args)


def test_missing_fee_not_replaced_with_category_fee(reference):
    db, s = reference
    s.install_cost = None
    db.flush()
    with pytest.raises(ValueError):
        q.quote_from_spec(db, product_type="床", length_m=2, boards=[],
            base_product_code="BED", base_sku_code="BED14", price_tier="big_buyer")


def test_readonly_recalculation_fingerprints(reference):
    from app.cli.custom_quote_verify import run
    db, _ = reference
    result = run(db, {"light": {"same": {"base_product_code": "BED", "base_sku_code": "BED14",
        "price_tier": "big_buyer", "outer_dimensions": {"length_cm": 210, "width_cm": 185}}}})
    assert result["before"] == result["after"]
    assert result["prices_unchanged"] is True
    assert result["platform_actions"] == 0
