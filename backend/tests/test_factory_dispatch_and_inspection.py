import io
from datetime import date, timedelta
from decimal import Decimal

from openpyxl import load_workbook

from app.models.feishu_sync import FeishuTableBinding
from app.models.order import Order
from app.services import (
    factory_dispatch_feishu_service as dispatch,
    feishu_bot_service as bot,
    feishu_sync_service,
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
    deadline = date.today() + timedelta(days=4)
    order = _order(
        is_customer_delayed=True,
        customer_delay_deadline=deadline,
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
    assert row["下单分组"] == "工厂正式单"
    assert row["系统排序键"] == "1-000321"
    assert "下单序号" not in row
    assert row["木作成本价"] == 600.0
    assert row["客户延期单"] is True
    assert row["客户通知拍照"] is True
    assert row["订单提醒"] == "⏳ 客户延期 · 📷 通知拍照"
    assert row["交期紧急度"] == "非常紧急"
    assert row["发货安排"] == "需拍照后通知爱群"
    assert "验货图片数" not in row
    assert row["预计发货日期"] == dispatch._date_ms(deadline)
    assert "订单金额" not in row
    assert dispatch.FIELD_SPECS[0] == ("工厂下单号", 1)
    assert dict(dispatch.FIELD_SPECS)["交期紧急度"] == 3
    assert dispatch.MAIN_VIEW_LAYOUT["group_by"] == "下单分组"
    assert dispatch.MAIN_VIEW_LAYOUT["sort_by"] == "系统排序键"


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


def test_dispatch_urgency_uses_schedule_for_active_and_reason_for_terminal():
    schedule = {"urgency_label": "正常安排"}

    assert dispatch._urgency_label(
        _order(status="paid"),
        refunded=False,
        schedule=schedule,
    ) == "正常安排"
    assert dispatch._urgency_label(
        _order(status="shipped"),
        refunded=False,
        schedule=schedule,
    ) == "完成"
    assert dispatch._urgency_label(
        _order(status="交易成功"),
        refunded=False,
        schedule=schedule,
    ) == "完成"
    assert dispatch._urgency_label(
        _order(status="cancelled"),
        refunded=False,
        schedule=schedule,
    ) == "取消"
    assert dispatch._urgency_label(
        _order(status="signed"),
        refunded=True,
        schedule=schedule,
    ) == "取消"
    assert dispatch._urgency_label(
        _order(status="aftersales"),
        refunded=False,
        schedule=schedule,
    ) == "售后处理"
    assert dispatch._urgency_label(
        _order(status="unknown_status"),
        refunded=False,
        schedule=schedule,
    ) == "待核实"


def test_dispatch_custom_order_uses_projected_cost_and_effective_production_qty(
    db_session, monkeypatch
):
    order = _order(
        is_custom=False,
        qty=4,
        paid_amount=Decimal("2400"),
        theoretical_cost=Decimal("1800"),
        wood_cost_est=Decimal("1200"),
        est_parts=Decimal("100"),
        est_packing=Decimal("100"),
        est_logistics=Decimal("100"),
        est_install=Decimal("100"),
        buyer_message="定制 1.5 米餐桌",
    )
    db_session.add(order)
    db_session.commit()
    monkeypatch.setattr(dispatch.gallery_lookup, "sku_image_rel", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.gallery_lookup, "main_image_rel", lambda *a, **k: None)

    row = dispatch.build_rows(db_session)[0]
    assert row["定制标识"] == "定制单"
    assert row["订购数量"] == 1
    assert row["木作成本价"] == 1400.0
    assert "已扣除非木作成本" in row["木作成本说明"]
    assert row["木作成本说明"].startswith("定制成本需人工核验｜")


def test_dispatch_custom_fallback_keeps_base_wood_cost(db_session, monkeypatch):
    order = _order(
        is_custom=True,
        qty=11,
        paid_amount=Decimal("500"),
        theoretical_cost=Decimal("425"),
        wood_cost_est=Decimal("1200"),
        buyer_message="35cm 定制床头柜",
    )
    db_session.add(order)
    db_session.commit()
    monkeypatch.setattr(dispatch.gallery_lookup, "sku_image_rel", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.gallery_lookup, "main_image_rel", lambda *a, **k: None)

    row = dispatch.build_rows(db_session)[0]
    assert row["订购数量"] == 1
    assert row["木作成本价"] == 1200.0
    assert "基础木作成本兜底" in row["木作成本说明"]


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
        "sync_if_enabled",
        lambda db: {"ok": True, "rows": 12, "created": 1, "updated": 11},
    )
    result = scheduler._sync_factory_dispatch_after_orders(db_session, {"images_pushed": 3})
    assert result["factory_dispatch"]["rows"] == 12
    assert "_run_status" not in result


def test_dispatch_failure_enters_order_retry_pipeline(db_session, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "sync_if_enabled",
        lambda db: {"ok": False, "errors": ["字段写入失败"]},
    )
    result = scheduler._sync_factory_dispatch_after_orders(db_session, {})
    assert result["_run_status"] == "fail"
    assert "飞书系统下单表同步失败" in result["_error"]
    assert "字段写入失败" in result["_error"]


def test_dispatch_auto_setting_can_skip_without_touching_feishu(db_session, monkeypatch):
    dispatch.save_sync_settings(db_session, auto_enabled=False, include_images=False)
    monkeypatch.setattr(
        dispatch,
        "sync",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应访问飞书")),
    )

    result = dispatch.sync_if_enabled(db_session)
    assert result["ok"] is True
    assert result["skipped"] == "auto_disabled"
    assert dispatch.get_sync_settings(db_session)["direction"] == "out"


def test_dispatch_schema_allows_operator_column_reordering():
    fields = [
        {
            "field_name": name,
            "type": field_type,
            "is_primary": name == "工厂下单号",
        }
        for name, field_type in dispatch.FIELD_SPECS
    ]
    # 工厂下单号保持第一列；其余列允许运营在飞书页面自由拖动。
    fields[1], fields[5] = fields[5], fields[1]
    assert dispatch._schema_layout_errors(fields) == []


def test_dispatch_urgency_style_preserves_existing_options(monkeypatch):
    fields = [{
        "field_id": "fld_urgency",
        "field_name": "交期紧急度",
        "type": 3,
        "ui_type": "SingleSelect",
        "property": {
            "options": [
                {"id": "opt_active", "name": "正常安排", "color": 1},
                {"id": "opt_done", "name": "完成", "color": 5},
                {"id": "opt_cancel", "name": "取消", "color": 6},
                {"id": "opt_after", "name": "售后处理", "color": 7},
            ],
            "multiple": False,
        },
    }]
    captured = {}

    def fake_update_field(*args, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(dispatch.feishu_client, "update_field", fake_update_field)

    changed = dispatch._ensure_urgency_option_styles(
        None,
        "app",
        "table",
        fields=fields,
    )

    assert changed is True
    options = captured["property_"]["options"]
    assert options[0] == {"id": "opt_active", "name": "正常安排", "color": 1}
    assert {option["name"]: option["color"] for option in options} == {
        "正常安排": 1,
        "完成": dispatch.TERMINAL_URGENCY_COLOR,
        "取消": dispatch.TERMINAL_URGENCY_COLOR,
        "售后处理": dispatch.TERMINAL_URGENCY_COLOR,
    }
    assert captured["property_"]["multiple"] is False
    assert captured["ui_type"] == "SingleSelect"


def test_dispatch_export_contains_urgency_and_photo_plan(db_session, monkeypatch):
    db_session.add(_order(ship_deadline=date.today() - timedelta(days=1)))
    db_session.commit()
    monkeypatch.setattr(dispatch.gallery_lookup, "sku_image_rel", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.gallery_lookup, "main_image_rel", lambda *a, **k: None)

    content = dispatch.export_workbook(db_session, include_images=False)
    workbook = load_workbook(io.BytesIO(content), read_only=True)
    sheet = workbook["系统下单表"]
    headers = [cell.value for cell in sheet[1]]
    values = dict(zip(headers, [cell.value for cell in sheet[2]]))
    assert values["交期紧急度"] == "已超期"
    assert values["发货安排"] == "需拍照后通知爱群"
    assert headers[0] == "工厂下单号"


def test_generic_feishu_sync_cannot_pull_factory_dispatch_table(db_session):
    binding = FeishuTableBinding(
        system_table="orders",
        feishu_app_token=dispatch.DEFAULT_APP_TOKEN,
        feishu_table_id=dispatch.DEFAULT_TABLE_ID,
        direction="bidirectional",
        enabled=True,
    )
    db_session.add(binding)
    db_session.commit()

    result = feishu_sync_service.sync_binding(db_session, binding)
    assert result.pulled == 0
    assert result.pushed == 0
    assert "仅允许 ERP → 飞书" in result.errors[0]
