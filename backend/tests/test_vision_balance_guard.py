# -*- coding: utf-8 -*-
"""推广余额 OCR 反向校验: 读到聚合结算/保证金等非万相台板块 → 强制低置信不入库。"""
from app.services import vision_ocr_service as v


class _Resp:
    def __init__(self, text):
        self.text = text


def test_promo_balance_rejects_aggregated_account(monkeypatch, db_session):
    """06-29 型: OCR 读到『聚合结算账户』(57855.45→误57.85) → 反向校验判低置信, 调用方不入库。"""
    monkeypatch.setattr(v, "_ocr_image_resp", lambda *a, **k: _Resp(
        '{"available": 57.85, "label_found": "聚合结算账户账户余额", "confidence": "high", "note": ""}'))
    data = v.parse_balance_screenshot(db_session, b"x", account_hint="推广")
    assert data["confidence"] == "low"
    assert "反向校验" in data["note"]


def test_promo_balance_rejects_deposit_account(monkeypatch, db_session):
    """读到『保证金账户』也要拦。"""
    monkeypatch.setattr(v, "_ocr_image_resp", lambda *a, **k: _Resp(
        '{"available": 5000.00, "label_found": "保证金账户可用余额", "confidence": "high", "note": ""}'))
    data = v.parse_balance_screenshot(db_session, b"x", account_hint="推广")
    assert data["confidence"] == "low"


def test_promo_balance_accepts_wanxiangtai(monkeypatch, db_session):
    """读对了『万相台无界版·账户总余额』→ 放行, 不降置信。"""
    monkeypatch.setattr(v, "_ocr_image_resp", lambda *a, **k: _Resp(
        '{"available": 5234.79, "label_found": "万相台无界版 账户总余额", "confidence": "high", "note": "ok"}'))
    data = v.parse_balance_screenshot(db_session, b"x", account_hint="推广")
    assert data["confidence"] == "high"
    assert data["available"] == 5234.79


def test_promo_balance_accepts_new_promotion_business_account_layout(
    monkeypatch, db_session,
):
    monkeypatch.setattr(v, "_ocr_image_resp", lambda *a, **k: _Resp(
        '{"available": 0.00, "label_found": "推广业务账户 万相台(元)", '
        '"confidence": "high", "note": "新版资金页"}'))
    data = v.parse_balance_screenshot(db_session, b"x", account_hint="淘宝推广账户")
    assert data["confidence"] == "high"
    assert data["available"] == 0.0


def test_promo_balance_rejects_other_new_layout_child_account(monkeypatch, db_session):
    monkeypatch.setattr(v, "_ocr_image_resp", lambda *a, **k: _Resp(
        '{"available": 800.00, "label_found": "推广业务账户 直通车(元)", '
        '"confidence": "high", "note": ""}'))
    data = v.parse_balance_screenshot(db_session, b"x", account_hint="淘宝推广账户")
    assert data["confidence"] == "low"


def test_nonpromo_account_unaffected(monkeypatch, db_session):
    """非推广账户(如聚合)不受此校验影响, 读聚合结算是对的。"""
    monkeypatch.setattr(v, "_ocr_image_resp", lambda *a, **k: _Resp(
        '{"available": 57855.45, "label_found": "聚合结算账户账户余额", "confidence": "high", "note": ""}'))
    data = v.parse_balance_screenshot(db_session, b"x", account_hint="聚合结算")
    assert data["confidence"] == "high"
