"""逐单配件覆盖 (parts_override, 2026-07-08): 追加/补差单人工指定配件替代整单BOM, 治双计。"""
from __future__ import annotations

from app.models.order import Order
from app.services import parts_recon_service as prs


def test_override_replaces_bom():
    o = Order(order_no="OVX", platform="淘宝", parts_override={"电力轨道": 75})
    cons = prs._order_category_consumption(o, {}, {}, {})   # 有覆盖→短路, 不碰(空)BOM
    assert set(cons) == {"电力轨道"}
    assert float(cons["电力轨道"]["amount"]) == 75.0
    assert cons["电力轨道"]["materials"][0]["override"] is True


def test_override_leather():
    o = Order(order_no="OVL", platform="淘宝", parts_override={"真皮": 346.8})
    cons = prs._order_category_consumption(o, {}, {}, {})
    assert float(cons["真皮"]["amount"]) == 346.8


def test_empty_override_means_no_parts():
    o = Order(order_no="OVZ", platform="淘宝", parts_override={})
    assert prs._order_category_consumption(o, {}, {}, {}) == {}   # 补差单: 空覆盖=不配配件


def test_none_override_falls_through_to_bom():
    # parts_override=None → 走 BOM (这里 BOM 空 → {}), 证明不误伤普通单
    o = Order(order_no="OVN", platform="淘宝", qty=1, parts_override=None)
    assert prs._order_category_consumption(o, {}, {}, {}) == {}
