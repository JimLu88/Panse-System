"""补差/加价单不推工厂 (用户 2026-07-12): 补差单套了柜子产品名, 推给工厂被当成整柜重复做
(例: 严小蓝 ¥315「其他定制」补差被推成畔色292单)。两条口径任一命中即不推、不占「畔色X单」号:
  ① 订单备注含补差类关键词; ② 订单实付 < 门槛 (默认¥400, 工厂制作单页面可配)。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.import_file import ImportedFile  # noqa: F401 (确保建表)
from app.models.order import Order
from app.services import order_sheet_archive_service as osa
from app.services import settings_service


@pytest.fixture
def _feishu_stub(monkeypatch):
    sent: list[str] = []
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    monkeypatch.setattr(osa, "render_png", lambda sheet: f"PNG-{sheet.order_no}".encode())
    monkeypatch.setattr("app.services.feishu_client.upload_image", lambda db, png: "k")
    monkeypatch.setattr("app.services.feishu_client.send_text", lambda db, cid, text: {"ok": True})
    monkeypatch.setattr("app.services.feishu_client.send_image",
                        lambda db, cid, key: (sent.append(cid), {"ok": True})[1])
    return sent


def _order(db, no, amount, day=20, remark=None, seller_memo=None):
    db.add(Order(platform="淘宝", order_no=no, qty=1, product_name=f"樱桃木窄柜{no}", sku="标准款",
                 order_date=date(2026, 6, day), status="paid",
                 paid_amount=Decimal(str(amount)), remark=remark, seller_memo=seller_memo,
                 customer_name="测试客户", customer_phone="13800000000",
                 customer_address="浙江省杭州市西湖区测试路1号"))
    db.flush()


def test_is_parts_topup_unit(db_session):
    db = db_session
    assert osa._is_parts_topup(db, Order(order_no="A", paid_amount=Decimal("315")))[0] is True    # <400
    assert osa._is_parts_topup(db, Order(order_no="B", paid_amount=Decimal("5891")))[0] is False  # 正常整柜
    # 金额够(5000)但备注含关键词 → 仍判补差
    assert osa._is_parts_topup(db, Order(order_no="C", paid_amount=Decimal("5000"),
                                         seller_memo="定制补差价链接"))[0] is True


def test_push_skips_topup_and_no_factory_no(db_session, _feishu_stub):
    db = db_session
    settings_service.set_value(db, "feishu_push_chat_id", "oc_factory")
    _order(db, "REAL-1", 5891)                                  # 正常整柜 → 推
    _order(db, "TOPUP-315", 315)                                # 金额<400 → 不推
    _order(db, "TOPUP-KW", 5000, seller_memo="补差价")          # 关键词 → 不推
    osa.generate_pending(db)
    res = osa.push_pending_images(db, include_baseline=False)
    assert set(res["order_nos"]) == {"REAL-1"}                  # 只推了正常单
    for no in ("TOPUP-315", "TOPUP-KW"):                        # 补差单没拿工厂号
        assert db.query(Order).filter_by(order_no=no).first().factory_no is None
    assert db.query(Order).filter_by(order_no="REAL-1").first().factory_no is not None


def test_threshold_configurable(db_session):
    db = db_session
    assert osa._push_min_amount(db) == 400.0                    # 默认门槛
    settings_service.set_value(db, "factory_push_min_amount", "0")
    assert osa._push_min_amount(db) == 0.0                      # 0 = 关闭金额规则
    assert osa._is_parts_topup(db, Order(order_no="X", paid_amount=Decimal("315")))[0] is False
    settings_service.set_value(db, "factory_push_min_amount", "500")
    assert osa._push_min_amount(db) == 500.0
    assert osa._is_parts_topup(db, Order(order_no="Y", paid_amount=Decimal("450")))[0] is True  # <500


def test_repush_rejects_topup(db_session):
    db = db_session
    settings_service.set_value(db, "feishu_push_chat_id", "oc_factory")
    _order(db, "TOPUP-R", 315)
    db.commit()
    res = osa.repush_to_factory(db, "TOPUP-R")
    assert res["ok"] is False and "补差" in res["error"]
    assert db.query(Order).filter_by(order_no="TOPUP-R").first().factory_no is None  # 拒绝时不占号
