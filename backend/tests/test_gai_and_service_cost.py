"""「改」后缀定制 + 服务SKU零成本: sku_utils 识别改后缀, order_cost 去改查BOM+加价, 服务行归0。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order
from app.services import order_cost_service, sku_utils


def test_sku_utils_gai_helpers():
    assert sku_utils.strip_custom_suffix("PPS2325005020237-改") == "PPS2325005020237"
    assert sku_utils.strip_custom_suffix("PPS2325005020237改") == "PPS2325005020237"
    assert sku_utils.strip_custom_suffix("PPS2325005020237") == "PPS2325005020237"
    assert sku_utils.has_gai_suffix("PPS2325005020237-改") is True
    assert sku_utils.has_gai_suffix("PPS2325005020237") is False
    # 改后缀 = 定制; 普通后缀11 不是; 99 是 (纯定制)
    assert sku_utils.is_custom_sku_code("PPS2325005020237-改") is True
    assert sku_utils.is_custom_sku_code("PPS2325005020211") is False
    assert sku_utils.is_custom_sku_code("PPS2325005020299") is True


def test_gai_order_cost_is_base_bom_plus_surcharge(db_session):
    db_session.add(Material(code="AC-0001", name="板", price=Decimal("10")))
    db_session.add(BomLine(product_code="P1", sku_code="PPS00100100111",
                           material_code="AC-0001", qty_per_product=2))
    db_session.commit()
    # 改单: sku_code 带改; is_custom + 定制加价50。去改取基础码 PPS00100100111 查BOM=20
    o = Order(platform="淘宝", order_no="G1", sku_code="PPS00100100111-改",
              is_custom=True, custom_surcharge=Decimal("50"), qty=1)
    db_session.add(o)
    db_session.commit()
    order_cost_service.recompute_and_save(db_session, o)
    assert o.theoretical_cost == Decimal("70")   # 基础20(去改查到BOM) + 加价50


@pytest.mark.parametrize("name", ["送货入户", "补差价专用", "商家安装"])
def test_service_order_zero_cost(db_session, name):
    o = Order(platform="淘宝", order_no="S-" + name, sku=name, product_name=name)
    db_session.add(o)
    db_session.commit()
    order_cost_service.recompute_and_save(db_session, o)
    assert o.theoretical_cost == Decimal("0")
