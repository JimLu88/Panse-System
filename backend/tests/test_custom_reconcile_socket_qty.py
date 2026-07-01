# -*- coding: utf-8 -*-
"""定制单核对「插座」规则数量解析回归 (2026-07-02 修):
备注常写中文数量("两个T25插座"), 旧正则只认阿拉伯数字, 会把型号编号"T25"的"25"误当数量
(63×25=1575, 真实该是 63×2=126)。改为优先识别中文数量词, 阿拉伯数字分支加"前面不贴字母/数字"
护栏防止再从型号编号里误读。另加: 单次运费 ¥8(不随数量翻倍)。
"""
from decimal import Decimal

from app.models.material import Material
from app.models.order import Order
from app.services.custom_order_reconcile_service import SOCKET_MATERIAL_CODE, _r_socket


def _seed_socket_material(db, price="63"):
    db.add(Material(code=SOCKET_MATERIAL_CODE, name="xpower电力轨道插座-单独", price=Decimal(price)))
    db.flush()


def test_socket_qty_chinese_numeral_with_model_code_not_confused(db_session):
    """"两个T25插座" → 数量应识别为 2(中文"两"), 不是型号"T25"里的"25"。"""
    _seed_socket_material(db_session, "55")
    o = Order(order_no="SK1", is_custom=True, paid_amount=Decimal("199.76"), remark="两个T25插座 两个T25插座")
    r = _r_socket(db_session, o, "两个T25插座 两个T25插座")
    assert r is not None
    # 55×2 + 运费8 = 118, 不是 55×25+8=1383(旧 bug: 型号"25"误当数量)
    assert r["cost"] == Decimal("118")


def test_socket_qty_arabic_digit_prefix_still_works(db_session):
    """"3个插座"(无型号干扰) 阿拉伯数字分支不受影响, 仍正确取到 3。"""
    _seed_socket_material(db_session, "55")
    o = Order(order_no="SK2", is_custom=True, paid_amount=Decimal("500"), remark="3个插座")
    r = _r_socket(db_session, o, "3个插座")
    assert r["cost"] == Decimal("55") * 3 + Decimal("8")


def test_socket_qty_suffix_notation_still_works(db_session):
    """"插座×3"(数字在后, 紧跟插座) 不受影响。"""
    _seed_socket_material(db_session, "55")
    o = Order(order_no="SK3", is_custom=True, paid_amount=Decimal("500"), remark="插座×3")
    r = _r_socket(db_session, o, "插座×3")
    assert r["cost"] == Decimal("55") * 3 + Decimal("8")


def test_socket_qty_no_number_defaults_to_one(db_session):
    """备注只说"插座"没写数量 → 默认 1 个。"""
    _seed_socket_material(db_session, "55")
    o = Order(order_no="SK4", is_custom=True, paid_amount=Decimal("100"), remark="补插座")
    r = _r_socket(db_session, o, "补插座")
    assert r["cost"] == Decimal("55") * 1 + Decimal("8")
