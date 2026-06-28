# -*- coding: utf-8 -*-
"""定制成本v2 (灰度开关 fin_custom_cost_v2; 用户 2026-06-29):
方案A(有真实工厂账单的定制单不再兜底实付×85%) + 方案4(非木作=定价表配件+物流+安装, 不再木作÷占比),
且**仅对 est_parts>0(配件可信)的有账单定制单生效**; est_parts=0 分桶回退旧口径(占比+兜底)。

旧8条 floor/口径测试(test_custom_floor_0626 / test_cost_caliber_fixes_0625 / test_actual_parts_0626)
**不受本改动影响**: 它们的 Order 都没设 est_parts(=0)且开关默认关 → 走旧路径, 行为逐字不变。
本文件只新增 v2 专项覆盖 + 三道护栏(默认安全 / 配件=0 分桶 / 片段不冒假利润)。
"""
from decimal import Decimal

from app.models.order import Order
from app.services import order_financials as ofin


def _set_v2(on: bool) -> None:
    ofin._cost_v2_cache["on"] = on


def test_v2_off_default_is_legacy():
    """默认(开关关): 有账单定制单 + est_parts>0 仍走旧兜底 → 证明默认部署对现网零影响。"""
    _set_v2(False)
    o = Order(order_no="V0", is_custom=True, paid_amount=Decimal("4000"),
              actual_cost=Decimal("2010"), est_parts=Decimal("300"))
    assert ofin.physical_cost_breakdown(o)["cap_mode"] == "定制兜底85"


def test_v2_on_with_parts_uses_pricing_no_floor():
    """开关开 + 配件可信(est_parts=300): 非木作=配件300+物流350+安装80=730, 成本=2010+730+170=2910, 不兜底。"""
    _set_v2(True)
    try:
        o = Order(order_no="V1", is_custom=True, paid_amount=Decimal("4000"),
                  actual_cost=Decimal("2010"), est_parts=Decimal("300"),
                  est_logistics=Decimal("350"), est_install=Decimal("80"), est_packing=Decimal("170"))
        b = ofin.physical_cost_breakdown(o)
        assert b["cap_mode"] == "v2实配件"
        assert b["estimate_part"] == Decimal("730")
        assert b["final"] == Decimal("2910") == ofin.physical_cost(o)
    finally:
        _set_v2(False)


def test_v2_on_zero_parts_still_floored():
    """★分桶护栏: 开关开 但 est_parts=0(配件不可信) → 回退旧口径占比+兜底, 不塌到只剩木作(防利润虚高)。"""
    _set_v2(True)
    try:
        o = Order(order_no="V2", is_custom=True, paid_amount=Decimal("4000"), actual_cost=Decimal("2010"))
        b = ofin.physical_cost_breakdown(o)
        assert b["cap_mode"] == "定制兜底85"
        assert b["final"] == Decimal("3400.00")   # 与旧口径完全一致
    finally:
        _set_v2(False)


def test_v2_on_fragment_still_capped():
    """★片段护栏: 开关开 + 配件可信 但 定金片段(实付300 远小于成本) → 仍片段封顶 实付×85%=255, 不冒假利润。"""
    _set_v2(True)
    try:
        o = Order(order_no="V3", is_custom=True, paid_amount=Decimal("300"),
                  actual_cost=Decimal("5700"), est_parts=Decimal("200"))
        b = ofin.physical_cost_breakdown(o)
        assert b["cap_mode"] == "片段85"
        assert b["final"] == Decimal("255.00")   # 300×0.85
    finally:
        _set_v2(False)


def test_v2_on_nonbill_custom_unchanged():
    """开关开 但无工厂账单的定制单 → v2 不生效(走推演/兜底分支), 与旧口径一致。"""
    _set_v2(True)
    try:
        o = Order(order_no="V4", is_custom=True, paid_amount=Decimal("4000"),
                  actual_cost=None, theoretical_cost=Decimal("2000"), est_parts=Decimal("300"))
        b = ofin.physical_cost_breakdown(o)
        assert b["cap_mode"] == "定制兜底85"   # 无账单仍兜底(方案A只去"有账单"的兜底)
    finally:
        _set_v2(False)


def test_v2_on_noncustom_unchanged():
    """开关开 对非定制单零影响(v2 只动定制分支)。"""
    _set_v2(True)
    try:
        o = Order(order_no="V5", is_custom=False, sku_code="PPS900", paid_amount=Decimal("2000"),
                  actual_cost=Decimal("170"), wood_cost_est=Decimal("0"), theoretical_cost=None,
                  est_parts=Decimal("300"))
        assert ofin.physical_cost(o) == Decimal("170")
    finally:
        _set_v2(False)
