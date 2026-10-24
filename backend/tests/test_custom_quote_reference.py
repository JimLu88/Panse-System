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


def test_bed_form_references_are_exact_and_readonly(reference):
    from app.services.custom_quote_reference import bed_references
    db, s = reference
    result = bed_references(db)
    row = next(x for x in result['items'] if x['sku_code'] == 'BED14')
    assert row['length_cm'] == 210
    assert row['width_cm'] == 185
    assert row['buyer_price'] == 4030.61
    assert row['fees']['packaging_cost'] == 150
    assert row['source'] == 'PricingSku.size_info'
    assert not db.dirty and not db.new and not db.deleted


def test_bed_form_missing_size_is_visible(reference):
    from app.services.custom_quote_reference import bed_references
    db, s = reference
    s.size_info = None
    db.commit()
    row = bed_references(db)['items'][0]
    assert row['length_cm'] is None
    assert '外长' in row['error']


def test_bed_form_missing_buyer_price_not_fallback(reference):
    from app.services.custom_quote_reference import bed_references
    db, s = reference
    db.query(PricingSkuPromo).delete()
    db.commit()
    row = bed_references(db)['items'][0]
    assert row['buyer_price'] is None
    assert row['error']


def test_bed_form_sample_exact_head_and_one_light_set(reference):
    db, _ = reference
    db.add(Material(code='AC-LAMP', name='榉木柔光床小夜灯', price=D(100), unit='套'))
    from app.models.bom import BomLine
    db.add(BomLine(product_code='BED', sku_code='BED14', material_code='AC-LAMP',
                   material_name='榉木柔光床小夜灯', qty_per_product=D(1), unit='套'))
    db.commit()
    result = light(db, outer_dimensions={'length_cm': 200, 'width_cm': 170},
        remove_parts=[{'material': '床头板', 'material_real': '榉木-2.2cm', 'length_cm':170, 'width_cm':70.5, 'qty':1},
                      {'material':'榉木柔光床小夜灯','qty':1}])
    assert result['final_price'] == 3379.73


@pytest.mark.parametrize('name,category', [('樱桃木齐边床','卧室'), ('黑胡桃软包床','卧室-床'), ('榉木无边床',None)])
def test_all_bed_names_route_without_borrowing_other_sku(reference, name, category):
    from app.services.custom_quote_reference import bed_references
    db, s = reference
    p = db.query(Product).filter_by(code='BED').one()
    p.name, p.category = name, category
    db.commit()
    result = bed_references(db)
    assert result['products'] == [{'product_code':'BED','product_name':name}]
    r = light(db, outer_dimensions={'length_cm':210,'width_cm':185})
    assert r['final_price'] == 4030.61
    assert not db.dirty


def test_empty_bed_is_not_silently_routed_as_nonbed(reference):
    from app.services.custom_quote_reference import bed_references
    db, _ = reference
    db.add(Product(code='EMPTY', name='待补数据床', category='卧室'))
    db.add(Product(code='CABINET', name='榉木床头柜', category='卧室'))
    db.commit()
    result = bed_references(db)
    assert 'EMPTY' in {p['product_code'] for p in result['products']}
    assert 'CABINET' not in {p['product_code'] for p in result['products']}
    assert not [x for x in result['items'] if x['product_code']=='EMPTY']


def test_bed_uses_own_variant_cost_curve_and_preserves_original_prices(reference):
    db,s=reference
    for code,label,width,cost in [('BED12','榉木柔光床-1.2米-榉木铺板',125,2010),
                                  ('BEDX','榉木柔光床-1.2米-松木铺板',125,999)]:
        db.add(PricingSku(product_code='BED',sku_code=code,sku=label,
            size_info=f'宽度：{width*10}mm；长度：2100mm；高度：880mm',wood_cost=D(cost),
            packaging_cost=D(150),logistics_cost=D(300),install_cost=D(50),
            physical_cost=D(cost+1250),big_promo=D(3600)))
        db.add(PricingSkuPromo(sku_code=code,big_buyer_price=D(3700)))
    db.commit()
    r=light(db,outer_dimensions={'length_cm':210,'width_cm':125})
    assert r['outer_dimension_basis']['calibration']['wood']==2010
    assert r['outer_dimension_basis']['calibration']['mode']=='recorded_area'
    assert r['final_price']==round(4030.61+(2010-2210)*4030.61/3460,2)
    assert not any('同一 ¥/㎡' in row.get('note','') for row in r['breakdown'])
    assert s.big_promo==3950 and not db.dirty


@pytest.mark.parametrize('parts',[
    [{'material':'不存在配件','qty':1}],
    [{'material':'床头板','qty':1,'material_real':'榉木-2.2cm','width_cm':70}],
    [{'material':'床头板','qty':1},{'material':'床头板','qty':1}],
])
def test_bed_unverified_or_repeated_removal_blocks(reference,parts):
    db,_=reference
    r=light(db,outer_dimensions={'length_cm':210,'width_cm':185},remove_parts=parts)
    assert r['final_price'] is None


def test_equal_area_is_not_claimed_as_unchanged_shape(reference):
    db,_=reference
    r=light(db,outer_dimensions={'length_cm':185,'width_cm':210})
    assert r['outer_dimension_basis']['calibration']['mode']=='equal_area_shape_change'
    assert r['outer_dimension_basis']['calibration']['estimated']
