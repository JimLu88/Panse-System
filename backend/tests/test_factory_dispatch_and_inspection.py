from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.services import (
    factory_dispatch_feishu_service as dispatch,
    feishu_bot_service as bot,
    import_storage,
    inspection_gallery_service as gallery,
    scheduler,
)


def _order(**overrides) -> Order:
    data = {
        "platform": "淘宝",
        "order_no": "331400000000000001",
        "order_date": date.today(),
        "customer_name": "张三",
        "customer_phone": "13800000000",
        "customer_address": "江西省南昌市",
        "product_code": "PPS001",
        "product_name": "榉木餐桌",
        "sku": "1.6米 原木色",
        "sku_code": "SKU001",
        "qty": 2,
        "status": "paid",
        "paid_amount": Decimal("3000"),
        "wood_cost_est": Decimal("1200"),
        "factory_no": 321,
        "buyer_message": "做好后通知拍照确认",
    }
    data.update(overrides)
    return Order(**data)


def test_dispatch_rows_use_unit_wood_and_flags(db_session, monkeypatch):
    order = _order(
        is_customer_delayed=True,
        customer_delay_deadline=date(2026, 8, 20),
    )
    topup = _order(
        order_no="331400000000000002",
        factory_no=None,
        product_name="补差邮费专拍链接",
        sku_code="SKU002",
        wood_cost_est=Decimal("10"),
    )
    db_session.add_all([order, topup])
    db_session.commit()
    monkeypatch.setattr(dispatch.gallery_lookup, "sku_image_rel", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.gallery_lookup, "main_image_rel", lambda *a, **k: None)

    rows = dispatch.build_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row["工厂下单号"] == "畔色321单"
    assert row["木作成本价"] == 600.0
    assert row["客户延期单"] is True
    assert row["客户通知拍照"] is True
    assert row["预计发货日期"] == dispatch._date_ms(date(2026, 8, 20))
    assert "订单金额" not in row


def test_dispatch_contact_is_plain_text_and_preserves_nonstandard_value(
    db_session, monkeypatch
):
    contact = "0571-12345678 / 微信联系"
    db_session.add(_order(customer_phone=contact))
    db_session.commit()
    monkeypatch.setattr(dispatch.gallery_lookup, "sku_image_rel", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.gallery_lookup, "main_image_rel", lambda *a, **k: None)

    rows = dispatch.build_rows(db_session)
    assert rows[0]["客户联系方式"] == contact
    assert dict(dispatch.FIELD_SPECS)["客户联系方式"] == 1
    assert dispatch.LEGACY_RENAMES["销售负责人"] == ("客户联系方式", 1)


def test_dispatch_compare_ignores_timestamp_and_normalizes_number_strings():
    expected = {
        "订单号": "331400000000000001",
        "订购数量": 2,
        "木作成本价": 600.0,
        "系统更新时间": 2000,
    }
    remote = {
        "订单号": "331400000000000001",
        "订购数量": "2",
        "木作成本价": "600",
        "系统更新时间": 1000,
    }
    assert dispatch._same(remote, expected) is True
    remote["木作成本价"] = "601"
    assert dispatch._same(remote, expected) is False


def test_inspection_gallery_archives_and_filters(db_session, monkeypatch, tmp_path):
    order = _order()
    db_session.add(order)
    db_session.commit()
    monkeypatch.setattr(import_storage, "get_root", lambda: tmp_path)

    rec = gallery.archive_image(
        db_session,
        order=order,
        content=b"\x89PNG\r\n\x1a\nfake",
        original_name="复检.png",
        source="web",
        uploaded_by="测试",
        captured_on=date(2026, 7, 30),
    )
    db_session.commit()

    rows = gallery.list_images(
        db_session,
        date_from=date(2026, 7, 30),
        date_to=date(2026, 7, 30),
        product="餐桌",
        factory_no=321,
    )
    assert [row["id"] for row in rows] == [rec.id]
    assert rows[0]["factory_label"] == "畔色321单"
    assert import_storage.read(rec.stored_path).startswith(b"\x89PNG")


def test_feishu_inspection_caption_parser():
    post = {
        "title": "",
        "content": [[
            {"tag": "at", "user_id": "ou_x"},
            {"tag": "text", "text": "验货 畔色321单"},
            {"tag": "img", "image_key": "img_x"},
        ]],
    }
    assert bot._post_text(post) == "验货 畔色321单"
    assert bot._post_image_keys(post) == ["img_x"]
    assert bot._inspection_ref(bot._post_text(post)) == {"factory_no": 321}
    assert bot._inspection_ref("验货 331400000000000001") == {
        "order_no": "331400000000000001"
    }
    assert bot._inspection_ref("这是普通采购单 321") is None


def test_order_delivery_chains_dispatch_sync(db_session, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "sync",
        lambda db: {"ok": True, "rows": 12, "created": 1, "updated": 11},
    )
    result = scheduler._sync_factory_dispatch_after_orders(db_session, {"images_pushed": 3})
    assert result["factory_dispatch"]["rows"] == 12
    assert "_run_status" not in result


def test_dispatch_failure_enters_order_retry_pipeline(db_session, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "sync",
        lambda db: {"ok": False, "errors": ["字段写入失败"]},
    )
    result = scheduler._sync_factory_dispatch_after_orders(db_session, {})
    assert result["_run_status"] == "fail"
    assert "飞书系统下单表同步失败" in result["_error"]
    assert "字段写入失败" in result["_error"]
