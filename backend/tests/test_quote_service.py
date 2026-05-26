from decimal import Decimal

import pytest

from app.models.material import Material
from app.models.pricing import PricingSku
from app.services import quote_service


# ----- light_lookup -----

def test_light_lookup_missing_returns_none(db_session):
    assert quote_service.light_lookup(db_session, "NOPE") is None


def test_light_lookup_returns_four_prices(db_session):
    db_session.add(PricingSku(
        product_code="PPS26330070320",
        sku="榉木无边床-1.2米-榉木铺板",
        sku_code="PPS2633007032011",
        size_category="中型",
        list_price=Decimal("6825.00"),
        daily_price=Decimal("5118.75"),
        small_promo=Decimal("3293.12"),
        mid_promo=Decimal("3234.60"),
        big_promo=Decimal("3123.57"),
        big_promo_margin=Decimal("312.36"),
        gross_margin_rate=Decimal("0.100000"),
    ))
    db_session.flush()
    q = quote_service.light_lookup(db_session, "PPS2633007032011")
    assert q is not None
    assert q.size_category == "中型"
    assert q.daily_price == Decimal("5118.75")
    assert q.big_promo == Decimal("3123.57")


# ----- high_calc -----

def test_high_calc_default_margin_by_size():
    q = quote_service.high_calc(cost=Decimal("1000"), size_category="小型")
    # 1000 / (1 - 0.15) = 1176.47
    assert q.margin_rate == Decimal("0.15")
    assert q.final_price == Decimal("1176.47")
    assert q.margin_amount == Decimal("176.47")


def test_high_calc_large_uses_25pct():
    q = quote_service.high_calc(cost=Decimal("3000"), size_category="大型")
    assert q.margin_rate == Decimal("0.25")
    # 3000 / 0.75 = 4000
    assert q.final_price == Decimal("4000.00")


def test_high_calc_override_margin():
    q = quote_service.high_calc(
        cost=Decimal("1000"), size_category="中型", margin_rate=Decimal("0.30")
    )
    assert q.margin_rate == Decimal("0.30")
    # 1000 / 0.7 ≈ 1428.57
    assert q.final_price == Decimal("1428.57")


def test_high_calc_rejects_unknown_size_without_margin():
    with pytest.raises(ValueError):
        quote_service.high_calc(cost=Decimal("100"), size_category="特大型")


def test_high_calc_rejects_bad_inputs():
    with pytest.raises(ValueError):
        quote_service.high_calc(cost=Decimal("0"), size_category="小型")
    with pytest.raises(ValueError):
        quote_service.high_calc(
            cost=Decimal("100"), size_category="小型", margin_rate=Decimal("1.0")
        )


# ----- material_swap_delta -----

def _add_mat(db, code, name, price=None):
    db.add(Material(code=code, name=name, price=price))
    db.flush()


def test_material_swap_to_more_expensive(db_session):
    _add_mat(db_session, "AC-0001", "榉木铺板", price=Decimal("300"))
    _add_mat(db_session, "AC-0002", "樱桃木铺板", price=Decimal("500"))
    r = quote_service.material_swap_delta(
        db_session, from_code="AC-0001", to_code="AC-0002", qty=Decimal("1")
    )
    assert r.delta == Decimal("200.00")


def test_material_swap_to_cheaper(db_session):
    _add_mat(db_session, "AC-0001", "A", price=Decimal("500"))
    _add_mat(db_session, "AC-0002", "B", price=Decimal("300"))
    r = quote_service.material_swap_delta(
        db_session, from_code="AC-0001", to_code="AC-0002", qty=Decimal("2")
    )
    assert r.delta == Decimal("-400.00")


def test_material_swap_missing_price_returns_none_delta(db_session):
    _add_mat(db_session, "AC-0001", "A", price=Decimal("300"))
    _add_mat(db_session, "AC-0002", "B", price=None)  # 定制物料未补价
    r = quote_service.material_swap_delta(
        db_session, from_code="AC-0001", to_code="AC-0002"
    )
    assert r.delta is None


def test_material_swap_same_code(db_session):
    r = quote_service.material_swap_delta(
        db_session, from_code="AC-0001", to_code="AC-0001"
    )
    assert r.delta == Decimal("0")


def test_material_swap_missing_material_raises(db_session):
    _add_mat(db_session, "AC-0001", "A", price=Decimal("100"))
    with pytest.raises(ValueError):
        quote_service.material_swap_delta(
            db_session, from_code="AC-0001", to_code="DOES-NOT-EXIST"
        )


# ----- any_dimension_delta -----

def test_dimension_delta_positive():
    # 床长加 20cm，每 cm 50 元成本，加 15% 利润：20 × 50 × 1.15 = 1150
    delta = quote_service.any_dimension_delta(
        base_cm=Decimal("180"),
        target_cm=Decimal("200"),
        per_cm_cost=Decimal("50"),
    )
    assert delta == Decimal("1150.00")


def test_dimension_delta_negative():
    delta = quote_service.any_dimension_delta(
        base_cm=Decimal("200"),
        target_cm=Decimal("180"),
        per_cm_cost=Decimal("50"),
    )
    assert delta == Decimal("-1150.00")
