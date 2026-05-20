from datetime import date

import pytest

from app.models.product import Product
from app.services import product_coder


def _add_product(db, code):
    db.add(Product(code=code, name=f"产品 {code}"))
    db.flush()


def test_parse_code_real_example():
    parts = product_coder.parse_code("PPS26330070320")
    assert parts is not None
    assert parts.brand == "PS"
    assert parts.year == "26"
    assert parts.category == "33"
    assert parts.counter == 7
    assert parts.month_day == "0320"


def test_parse_code_rejects_garbage():
    assert product_coder.parse_code("nope") is None
    assert product_coder.parse_code("") is None
    assert product_coder.parse_code("PPS26330") is None  # too short


def test_first_code_in_dimension(db_session):
    code = product_coder.next_product_code(
        db_session, brand="PS", category="33", created_at=date(2026, 3, 20)
    )
    assert code == "PPS26330010320"


def test_counter_increments(db_session):
    _add_product(db_session, "PPS26330010320")
    _add_product(db_session, "PPS26330020325")
    code = product_coder.next_product_code(
        db_session, brand="PS", category="33", created_at=date(2026, 4, 1)
    )
    assert code == "PPS26330030401"


def test_counter_isolated_by_brand(db_session):
    _add_product(db_session, "PPS26330010320")
    _add_product(db_session, "PPS26330020325")
    # 换 FG 品牌 → 计数独立从 1 开始
    code = product_coder.next_product_code(
        db_session, brand="FG", category="33", created_at=date(2026, 4, 1)
    )
    assert code == "PFG26330010401"


def test_counter_isolated_by_year(db_session):
    _add_product(db_session, "PPS26330050320")
    code = product_coder.next_product_code(
        db_session, brand="PS", category="33", created_at=date(2027, 1, 1)
    )
    assert code == "PPS27330010101"


def test_counter_isolated_by_category(db_session):
    _add_product(db_session, "PPS26330050320")
    code = product_coder.next_product_code(
        db_session, brand="PS", category="35", created_at=date(2026, 3, 20)
    )
    assert code == "PPS26350010320"


def test_validation_rejects_bad_brand(db_session):
    with pytest.raises(ValueError):
        product_coder.next_product_code(db_session, brand="p", category="33")
    with pytest.raises(ValueError):
        product_coder.next_product_code(db_session, brand="PSS", category="33")


def test_validation_rejects_bad_category(db_session):
    with pytest.raises(ValueError):
        product_coder.next_product_code(db_session, brand="PS", category="3")
    with pytest.raises(ValueError):
        product_coder.next_product_code(db_session, brand="PS", category="ab")
