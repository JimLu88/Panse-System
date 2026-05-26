from app.models.material import Material
from app.models.product import Product
from app.services import match_service


def _add_mat(db, code, name):
    db.add(Material(code=code, name=name))


def _add_prod(db, code, name):
    db.add(Product(code=code, name=name))


def test_fuzzy_empty_query_returns_empty(db_session):
    assert match_service.fuzzy(db_session, "", scope="material") == []
    assert match_service.fuzzy(db_session, "  ", scope="material") == []


def test_fuzzy_exact_substring_ranks_high(db_session):
    _add_mat(db_session, "AC-0001", "电力轨道-Xpower-T25-黑色-1.0")
    _add_mat(db_session, "AC-0002", "电力轨道-Xpower-T25-银色-1.5")
    _add_mat(db_session, "AC-0003", "床铺板-榉木")
    db_session.flush()
    results = match_service.fuzzy(db_session, "电力轨道", scope="material", limit=5)
    assert len(results) == 2
    assert all(r.scope == "material" for r in results)
    assert {r.code for r in results} == {"AC-0001", "AC-0002"}


def test_fuzzy_ranks_closer_match_first(db_session):
    _add_mat(db_session, "AC-0001", "床铺板-榉木")
    _add_mat(db_session, "AC-0002", "榉木")
    db_session.flush()
    results = match_service.fuzzy(db_session, "榉木", scope="material")
    # "榉木" 精确 = AC-0002 应该排第一
    assert results[0].code == "AC-0002"


def test_fuzzy_no_hits(db_session):
    _add_mat(db_session, "AC-0001", "床铺板")
    db_session.flush()
    results = match_service.fuzzy(db_session, "完全无关词", scope="material")
    assert results == []


def test_fuzzy_product_scope(db_session):
    _add_prod(db_session, "PPS26330010520", "测试床 A")
    _add_prod(db_session, "PPS26330020520", "测试床 B")
    db_session.flush()
    results = match_service.fuzzy(db_session, "测试床", scope="product")
    assert len(results) == 2
    assert all(r.scope == "product" for r in results)


def test_fuzzy_unknown_scope_raises(db_session):
    import pytest
    with pytest.raises(ValueError):
        match_service.fuzzy(db_session, "x", scope="nope")  # type: ignore
