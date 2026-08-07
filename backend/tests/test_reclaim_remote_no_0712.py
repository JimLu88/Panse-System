"""远期单不占「畔色X单」号 —— 自动收回 (用户 2026-07-12: 畔色299远期单占号留空洞)。

时序漏洞: 编号时还不是远期(推送又失败) → 客户备注延期变远期 → 号卡在远期单手里。
规则: 有号 + 现远期 + 下单图【从没推给工厂】 → 收回 factory_no、补发内部 remote_seq。
已推过的远期单不悄悄收(工厂见过号, 走 void_remote_pushed 显式作废)。
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
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    monkeypatch.setattr(osa, "render_png", lambda sheet: f"PNG-{sheet.order_no}".encode())
    monkeypatch.setattr("app.services.feishu_client.upload_image", lambda db, png: "k")
    monkeypatch.setattr("app.services.feishu_client.send_text", lambda db, cid, text: {"ok": True})
    monkeypatch.setattr("app.services.feishu_client.send_image", lambda db, cid, key: {"ok": True})


def _order(db, no, factory_no=None, memo=None):
    o = Order(platform="淘宝", order_no=no, qty=1, product_name=f"岩板餐桌{no}", sku="标准款",
              order_date=date(2026, 7, 9), status="paid", paid_amount=Decimal("2678"),
              factory_no=factory_no, seller_memo=memo,
              customer_name="测试客户", customer_phone="13800000000",
              customer_address="浙江省杭州市西湖区测试路1号")
    db.add(o)
    db.flush()
    return o


def test_reclaim_numbered_unpushed_remote(db_session):
    """编了号、没推过、现为远期 → 收回号 + 补 remote_seq。"""
    db = db_session
    o = _order(db, "RC1", factory_no=299, memo="等通知")     # 变远期后号卡手里
    got = osa._reclaim_remote_numbers(db)
    assert [(g["order_no"], g["old_factory_no"]) for g in got] == [("RC1", 299)]
    db.refresh(o)
    assert o.factory_no is None and o.remote_seq is not None


def test_pushed_remote_not_reclaimed(db_session, _feishu_stub):
    """已推给工厂的远期单不悄悄收号(工厂见过, 走显式作废流程)。"""
    db = db_session
    settings_service.set_value(db, "feishu_push_chat_id", "oc_factory")
    o = _order(db, "RC2", memo=None)                          # 正常单: 生成+推送
    osa.generate_pending(db)
    res = osa.push_pending_images(db, include_baseline=False)
    assert "RC2" in res["order_nos"]
    db.refresh(o)
    assert o.factory_no is not None
    o.seller_memo = "延迟等通知"                               # 推完才变远期
    db.commit()
    assert osa._reclaim_remote_numbers(db) == []              # 不收
    db.refresh(o)
    assert o.factory_no is not None


def test_sent_snapshot_only_remote_not_reclaimed(db_session):
    db = db_session
    order = _order(db, "SENT-EVIDENCE", factory_no=302, memo="等通知")
    db.add(ImportedFile(
        kind="order_sheet_sent",
        original_filename="2026-08-03_SENT-EVIDENCE_畔色302单.jpg",
        stored_path="/x/SENT-EVIDENCE.jpg",
        row_summary={"pushed": True, "order_no": "SENT-EVIDENCE"},
    ))
    db.commit()

    assert osa._reclaim_remote_numbers(db) == []
    db.refresh(order)
    assert order.factory_no == 302


def test_push_entry_autoheals(db_session, _feishu_stub):
    """push_pending_images 入口自动跑收回: 远期占号单被清号且不被推。"""
    db = db_session
    settings_service.set_value(db, "feishu_push_chat_id", "oc_factory")
    o = _order(db, "RC3", factory_no=305, memo="等通知")
    res = osa.push_pending_images(db, include_baseline=True)
    assert "RC3" not in res["order_nos"]                      # 远期不推
    assert res["remaining"] == 0
    db.refresh(o)
    assert o.factory_no is None and o.remote_seq is not None  # 号已收回
