"""配件编码统一+1 + SKU 编码自动分配 测试。"""
from datetime import date

from app.models.material import Material
from app.services import material_coder


# ----------------------------- 配件编码 ----------------------------- #

def test_material_next_code_empty(db_session):
    """空库时各前缀从 001 起。"""
    assert material_coder.next_code(db_session, "MP") == "MP-001"
    assert material_coder.next_code(db_session, "AC") == "AC-001"


def test_material_next_code_increment(db_session):
    db_session.add(Material(code="MP-001", name="a"))
    db_session.add(Material(code="MP-007", name="b"))
    db_session.add(Material(code="AC-183", name="c"))
    db_session.flush()
    assert material_coder.next_code(db_session, "MP") == "MP-008"
    assert material_coder.next_code(db_session, "AC") == "AC-184"
    # 不同前缀互不影响
    assert material_coder.next_code(db_session, "MW") == "MW-001"


def test_material_next_code_invalid_prefix(db_session):
    import pytest
    with pytest.raises(ValueError):
        material_coder.next_code(db_session, "ZZ")


def test_create_material_autocode(client, db_session):
    db_session.add(Material(code="MP-005", name="x"))
    db_session.commit()
    resp = client.post("/api/materials", json={"name": "餐桌-人工费-大型", "prefix": "MP"})
    assert resp.status_code == 201
    assert resp.json()["code"] == "MP-006"


def test_create_material_explicit_code_wins(client, db_session):
    resp = client.post("/api/materials", json={"name": "y", "code": "SP-099"})
    assert resp.status_code == 201
    assert resp.json()["code"] == "SP-099"


# ----------------------------- SKU 自动分配 ----------------------------- #

def test_compose_auto_sku_codes(client, db_session):
    payload = {
        "name": "测试床", "brand": "PS", "category": "33",
        "category_label": "卧室-床", "created_on": str(date.today()),
        "bom_lines": [],
        "pricing_skus": [
            {"sku": "1.2米", "is_custom": False},
            {"sku": "1.5米", "is_custom": False},
            {"sku": "定制2.0米", "is_custom": True},
        ],
    }
    resp = client.post("/api/product-composer", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    code = data["product_code"]
    skus = data["sku_codes"]
    assert len(skus) == 3
    # 普通走 11/12, 定制走 90
    assert skus[0] == f"{code}11"
    assert skus[1] == f"{code}12"
    assert skus[2] == f"{code}90"


def test_compose_explicit_sku_code_preserved(client, db_session):
    payload = {
        "name": "测试桌", "brand": "PS", "category": "21",
        "created_on": str(date.today()),
        "bom_lines": [],
        "pricing_skus": [
            {"sku_code": "MANUAL001", "sku": "手填"},
            {"sku": "自动", "is_custom": False},
        ],
    }
    resp = client.post("/api/product-composer", json=payload)
    assert resp.status_code == 201, resp.text
    skus = resp.json()["sku_codes"]
    assert "MANUAL001" in skus
