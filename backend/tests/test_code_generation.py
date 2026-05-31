"""配件编码统一+1 + SKU 编码自动分配 测试。

直接调用路由函数 (传 db_session)，避免独立 engine 受 test_migrations_smoke
模块重载污染共享 metadata 的影响 (pytest-randomly 随机序下会偶发)。
"""
from datetime import date

import pytest

from app.api.materials import create_material, preview_next_code
from app.api.product_composer import ComposeProductIn, PricingIn, compose_product
from app.models.material import Material
from app.schemas.material import MaterialCreate
from app.services import material_coder


# ----------------------------- 配件编码 (纯 service) ----------------------------- #

def test_material_next_code_empty(db_session):
    assert material_coder.next_code(db_session, "MP") == "MP-001"
    assert material_coder.next_code(db_session, "AC") == "AC-001"


def test_material_next_code_increment(db_session):
    db_session.add(Material(code="MP-001", name="a"))
    db_session.add(Material(code="MP-007", name="b"))
    db_session.add(Material(code="AC-183", name="c"))
    db_session.flush()
    assert material_coder.next_code(db_session, "MP") == "MP-008"
    assert material_coder.next_code(db_session, "AC") == "AC-184"
    assert material_coder.next_code(db_session, "MW") == "MW-001"


def test_material_next_code_invalid_prefix(db_session):
    with pytest.raises(ValueError):
        material_coder.next_code(db_session, "ZZ")


# ----------------------------- 配件创建 (路由函数直调) ----------------------------- #

def test_create_material_autocode(db_session):
    create_material(MaterialCreate(name="x", code="MP-005"), db=db_session)
    mat = create_material(MaterialCreate(name="餐桌-人工费-大型", prefix="MP"), db=db_session)
    assert mat.code == "MP-006"


def test_create_material_explicit_code_wins(db_session):
    mat = create_material(MaterialCreate(name="y", code="SP-099"), db=db_session)
    assert mat.code == "SP-099"


def test_next_code_preview(db_session):
    out = preview_next_code(prefix="AC", db=db_session)
    assert out.code == "AC-001"


# ----------------------------- SKU 自动分配 (路由函数直调) ----------------------------- #

def test_compose_auto_sku_codes(db_session):
    payload = ComposeProductIn(
        name="测试床", brand="PS", category="33", category_label="卧室-床",
        created_on=date.today(), bom_lines=[],
        pricing_skus=[
            PricingIn(sku="1.2米", is_custom=False),
            PricingIn(sku="1.5米", is_custom=False),
            PricingIn(sku="定制2.0米", is_custom=True),
        ],
    )
    data = compose_product(payload, db=db_session, _=None)
    code = data["product_code"]
    skus = data["sku_codes"]
    assert len(skus) == 3
    assert skus[0] == f"{code}11"
    assert skus[1] == f"{code}12"
    assert skus[2] == f"{code}90"


def test_compose_explicit_sku_code_preserved(db_session):
    payload = ComposeProductIn(
        name="测试桌", brand="PS", category="21", created_on=date.today(),
        bom_lines=[],
        pricing_skus=[
            PricingIn(sku_code="MANUAL001", sku="手填"),
            PricingIn(sku="自动", is_custom=False),
        ],
    )
    data = compose_product(payload, db=db_session, _=None)
    assert "MANUAL001" in data["sku_codes"]
