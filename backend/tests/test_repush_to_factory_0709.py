"""工厂生产看板「重推给工厂」repush_to_factory: 守卫(远期/取消/退款/不存在) + 没号顺排 (2026-07-09)。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.services import order_sheet_archive_service as osa


def _o(no, **kw):
    d = dict(platform="淘宝", status="paid", paid_amount=Decimal("100"), is_refill=False, sku="标准款")
    d.update(kw)
    return Order(order_no=no, **d)


def test_repush_rejects_cancelled(db_session):
    db_session.add(_o("RP1", status="cancelled"))
    db_session.flush()
    r = osa.repush_to_factory(db_session, "RP1")
    assert r["ok"] is False and "取消" in r["error"]


def test_repush_rejects_remote(db_session):
    db_session.add(_o("RP2", seller_memo="等通知装修好再发"))
    db_session.flush()
    r = osa.repush_to_factory(db_session, "RP2")
    assert r["ok"] is False and "远期" in r["error"]


def test_repush_rejects_missing(db_session):
    r = osa.repush_to_factory(db_session, "NOPE")
    assert r["ok"] is False


def test_repush_valid_assigns_number(db_session, monkeypatch):
    # 避开 wkhtmltoimage 渲染 + 飞书推送, 只验守卫通过 + 没号顺排
    monkeypatch.setattr(osa, "generate_for_order", lambda db, o: {"order_no": o.order_no, "duplicate": False})
    monkeypatch.setattr(osa, "push_pending_images", lambda db, **k: {"pushed": 1, "failed": 0})
    o = _o("RP3", order_date=date(2026, 7, 8), paid_amount=Decimal("1000"))  # 真实整柜价, 避开补差<400规则
    db_session.add(o)
    db_session.flush()
    r = osa.repush_to_factory(db_session, "RP3")
    assert r["ok"] is True and r["pushed"] == 1
    db_session.refresh(o)
    assert o.factory_no is not None          # 正式单没号 → 顺排了一个
