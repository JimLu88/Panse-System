import io
from datetime import date, timedelta
from decimal import Decimal

from openpyxl import load_workbook
from PIL import Image

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
        production_note="客户已通知开始制作",
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
    assert row["订单提醒"] == "⏳ 客户延期（已开始制作） · 📷 通知拍照"
    assert row["交期紧急度"] == "非常紧急"
    assert row["发货安排"] == "需拍照后通知爱群"
    assert "验货图片数" not in row
    assert row["预计发货日期"] == dispatch._date_ms(deadline)
    assert "订单金额" not in row
    assert dispatch.FIELD_SPECS[0] == ("工厂下单号", 1)
    assert dict(dispatch.FIELD_SPECS)["交期紧急度"] == 3
    assert dict(dispatch.FIELD_SPECS)["工厂下单图"] == 17
    assert dispatch.LEGACY_RENAMES["产品图"] == ("工厂下单图", 17)
    assert dispatch.MAIN_VIEW_LAYOUT["group_by"] == "下单分组"
    assert dispatch.MAIN_VIEW_LAYOUT["sort_by"] == "系统排序键"


def test_dispatch_customer_delay_without_start_is_remote_wait_notice(db_session):
    order = _order(
        order_no="331400000000000009",
        factory_no=146,
        remote_seq=88,
        is_customer_delayed=True,
        customer_delay_deadline=date.today() + timedelta(days=2),
        buyer_message="客户要求延期发货，等通知",
        seller_memo=None,
        remark=None,
        production_note=None,
    )
    db_session.add(order)
    db_session.commit()

    row = dispatch.build_rows(db_session)[0]
    assert row["工厂下单号"] == "远期单88"
    assert row["下单分组"] == "远期单"
    assert row["订单状态"] == "等客户通知"
    assert row["交期紧急度"] == "远期单"
    assert row["发货安排"] == "之后发货（等通知）"
    assert row["预计发货日期"] is None
    assert row["订单提醒"] == "⏳ 远期等通知"


def test_dispatch_started_order_still_waits_for_shipping_notice(db_session):
    """开始制作只解除生产挂起，不能覆盖买家的发货前通知门。"""
    order = _order(
        order_no="3312219648672006758",
        factory_no=368,
        buyer_message="延迟发货 发货前通知",
        seller_memo="开始制作",
        production_note=None,
    )
    db_session.add(order)
    db_session.commit()

    row = dispatch.build_rows(db_session)[0]

    assert row["订单状态"] == "生产中"
    assert row["发货安排"] == "做好后等通知发货"
    assert row["订单备注"] == "买家：延迟发货 发货前通知\n卖家：开始制作"


def test_dispatch_explicit_direct_ship_can_release_old_notice():
    order = _order(
        buyer_message="延迟发货，发货前通知",
        seller_memo="客户改口：无需通知直接发货",
        production_note=None,
    )
    assert dispatch.order_flags.waits_for_shipping_notice(order) is False


def test_dispatch_excludes_refunded_parent_order(db_session):
    db_session.add(_order(
        refund_status="退款成功",
        refund_amount=Decimal("3000"),
    ))
    db_session.commit()

    assert dispatch.build_rows(db_session) == []


def test_dispatch_photo_keywords_cover_common_customer_phrasing():
    positive_notes = (
        "发货前必须拍照给我确认",
        "做好以后拍几张照片发过来",
        "完工图发我看一下",
        "请录个视频确认细节",
        "客户要求视频验货",
        "需要远程验货后再发货",
    )
    for note in positive_notes:
        assert dispatch._photo_requested(
            _order(buyer_message=note, seller_memo=None, remark=None, production_note=None)
        ), note


def test_dispatch_photo_keywords_ignore_explicit_negative_phrasing():
    negative_notes = (
        "客户说不用拍照，做好直接发货",
        "无需发图，正常安排即可",
        "不需要提供照片",
        "不要录视频",
        "取消拍照要求",
    )
    for note in negative_notes:
        assert not dispatch._photo_requested(
            _order(buyer_message=note, seller_memo=None, remark=None, production_note=None)
        ), note
    assert dispatch._photo_requested(
        _order(
            buyer_message="不用拍照，但需要录视频确认",
            seller_memo=None,
            remark=None,
            production_note=None,
        )
    )


def test_dispatch_contact_is_plain_text_and_preserves_nonstandard_value(
    db_session, monkeypatch
):
    contact = "0571-12345678 / 微信联系"
    db_session.add(_order(customer_phone=contact))
    db_session.commit()

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
        _order(status="paid", refund_amount=Decimal("42.40")),
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


def test_dispatch_compare_treats_empty_attachment_as_missing():
    expected = {
        "订单号": "331400000000000001",
        "工厂下单图": [],
    }
    remote = {
        "订单号": "331400000000000001",
        "工厂下单图": None,
    }
    assert dispatch._same(remote, expected) is True


def test_dispatch_reuses_remote_attachment_when_signature_matches():
    fields = {
        "下单图签名": "factory-sheet:abc",
        "工厂下单图": [{
            "file_token": "token-existing",
            "name": "2026-08-12_3301_畔色385单.jpg",
        }],
    }
    assert dispatch._remote_attachment_matches(
        fields,
        signature="factory-sheet:abc",
        expected_name="2026-08-12_3301_畔色385单.jpg",
    ) is True
    assert dispatch._remote_attachment_value(fields) == [
        {"file_token": "token-existing"},
    ]


def test_dispatch_legacy_attachment_bootstraps_by_exact_sent_filename():
    fields = {
        "工厂下单图": [{
            "file_token": "token-existing",
            "name": "2026-08-12_3301_畔色385单.jpg",
        }],
    }
    assert dispatch._remote_attachment_matches(
        fields,
        signature="factory-sheet:new-signature",
        expected_name="2026-08-12_3301_畔色385单.jpg",
    ) is True
    assert dispatch._remote_attachment_matches(
        fields,
        signature="factory-sheet:new-signature",
        expected_name="另一张图.jpg",
    ) is False


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


def test_deferred_dispatch_image_upload_does_not_fail_order_pipeline(db_session, monkeypatch):
    monkeypatch.setattr(
        dispatch,
        "sync_if_enabled",
        lambda db: {
            "ok": True,
            "rows": 12,
            "created": 0,
            "updated": 3,
            "errors": [],
            "deferred_image_uploads": [{
                "order_no": "3308543835478010890",
                "reason": "FeishuError: timed out",
            }],
        },
    )
    result = scheduler._sync_factory_dispatch_after_orders(
        db_session,
        {"images_pushed": 1},
    )
    assert result["factory_dispatch"]["deferred_image_uploads"]
    assert "_run_status" not in result


def test_dispatch_attachment_timeout_keeps_old_image_and_returns_ok(
    db_session, monkeypatch, tmp_path,
):
    order = _order()
    db_session.add(order)
    db_session.commit()
    monkeypatch.setattr(import_storage, "get_root", lambda: tmp_path)

    image_buf = io.BytesIO()
    Image.new("RGB", (320, 200), color="white").save(image_buf, format="JPEG")
    sent = dispatch.order_sheet_archive_service.archive_sent_snapshot(
        db_session,
        order,
        image_buf.getvalue(),
        backfilled=True,
    )
    db_session.commit()
    remote_fields = {
        "订单号": order.order_no,
        "工厂下单号": "畔色321单",
        "工厂下单图": [{
            "file_token": "old-token",
            "name": "old-sheet.jpg",
        }],
    }
    monkeypatch.setattr(dispatch, "_ensure_schema", lambda *a, **k: None)
    monkeypatch.setattr(
        dispatch.feishu_client,
        "list_table_fields",
        lambda *a, **k: [
            {"field_name": name, "type": field_type, "is_primary": name == "工厂下单号"}
            for name, field_type in dispatch.FIELD_SPECS
        ],
    )
    monkeypatch.setattr(
        dispatch.feishu_client,
        "list_views",
        lambda *a, **k: [
            {"view_id": view_id, "view_name": name, "view_type": kind}
            for view_id, (name, kind) in dispatch.EXPECTED_VIEWS.items()
        ],
    )
    monkeypatch.setattr(
        dispatch.feishu_client,
        "list_records",
        lambda *a, **k: [{"record_id": "rec1", "fields": remote_fields}],
    )
    monkeypatch.setattr(
        dispatch,
        "_attachment_value",
        lambda *a, **k: (_ for _ in ()).throw(
            dispatch.feishu_client.FeishuError("The write operation timed out")
        ),
    )
    updates = []
    monkeypatch.setattr(
        dispatch.feishu_client,
        "batch_update_records",
        lambda db, app, table, rows: updates.extend(rows) or [],
    )
    monkeypatch.setattr(
        dispatch.feishu_client,
        "batch_create_records",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应新建")),
    )
    monkeypatch.setattr(
        dispatch,
        "_ensure_urgency_option_styles",
        lambda *a, **k: False,
    )

    result = dispatch.sync(db_session, include_images=True)

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["deferred_image_uploads"][0]["order_no"] == order.order_no
    assert updates
    assert updates[0]["fields"]["工厂下单图"] == [{"file_token": "old-token"}]
    # 上传未成功时不能写入签名绑定；下轮仍会继续尝试。
    assert dispatch._load_image_bindings(db_session).get(order.order_no) is None


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


def test_dispatch_sync_removes_refunds_and_does_not_block_on_missing_cost(
    db_session, monkeypatch
):
    active = _order(
        order_no="ACTIVE-MISSING-COST",
        factory_no=322,
        sku_code="SKU-MISSING-COST",
        wood_cost_est=None,
        buyer_message=None,
    )
    refunded = _order(
        order_no="REFUNDED-FACTORY-ORDER",
        factory_no=323,
        refund_status="退款成功",
        refund_amount=Decimal("3000"),
    )
    db_session.add_all([active, refunded])
    db_session.commit()

    monkeypatch.setattr(dispatch, "_ensure_schema", lambda *a, **k: None)
    monkeypatch.setattr(
        dispatch.feishu_client,
        "list_table_fields",
        lambda *a, **k: [
            {
                "field_name": name,
                "type": field_type,
                "field_id": name,
                "is_primary": name == "工厂下单号",
            }
            for name, field_type in dispatch.FIELD_SPECS
        ],
    )
    monkeypatch.setattr(
        dispatch.feishu_client,
        "list_views",
        lambda *a, **k: [
            {"view_id": view_id, "view_name": name, "view_type": kind}
            for view_id, (name, kind) in dispatch.EXPECTED_VIEWS.items()
        ],
    )
    monkeypatch.setattr(
        dispatch.feishu_client,
        "list_records",
        lambda *a, **k: [{
            "record_id": "refund-record",
            "fields": {
                "工厂下单号": "畔色323单",
                "订单号": refunded.order_no,
                "订单状态": "生产中",
            },
        }],
    )
    created = []
    deleted = []
    monkeypatch.setattr(
        dispatch.feishu_client,
        "batch_create_records",
        lambda db, app, table, rows: created.extend(rows) or ["active-record"],
    )
    monkeypatch.setattr(
        dispatch.feishu_client,
        "batch_update_records",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        dispatch.feishu_client,
        "batch_delete_records",
        lambda db, app, table, ids: deleted.extend(ids) or len(ids),
    )
    monkeypatch.setattr(dispatch, "_ensure_urgency_option_styles", lambda *a, **k: False)

    result = dispatch.sync(db_session, include_images=False)

    assert result["ok"] is True
    assert result["missing_wood_cost"] == [active.order_no]
    assert result["warnings"] and result["errors"] == []
    assert result["created"] == 1
    assert created[0]["订单号"] == active.order_no
    assert deleted == ["refund-record"]
    assert result["deleted_ineligible"] == 1


def test_periodic_feishu_sync_propagates_factory_dispatch_failure(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        dispatch.feishu_client,
        "get_credentials",
        lambda db: ("app", "secret"),
    )
    monkeypatch.setattr(
        dispatch,
        "sync_if_enabled",
        lambda db: {"ok": False, "errors": ["工厂表写入失败"]},
    )
    monkeypatch.setattr(feishu_sync_service, "sync_all", lambda db: [])

    result = scheduler._job_feishu_sync(db_session)

    assert result["_run_status"] == "fail"
    assert "工厂表写入失败" in result["_error"]


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

    content = dispatch.export_workbook(db_session, include_images=False)
    workbook = load_workbook(io.BytesIO(content), read_only=True)
    sheet = workbook["系统下单表"]
    headers = [cell.value for cell in sheet[1]]
    values = dict(zip(headers, [cell.value for cell in sheet[2]]))
    assert values["交期紧急度"] == "已超期"
    assert values["发货安排"] == "需拍照后通知爱群"
    assert headers[0] == "工厂下单号"
    assert "工厂下单图" in headers
    assert "产品图" not in headers


def test_dispatch_uses_sent_factory_sheet_not_draft_or_gallery_product_image(
    db_session, monkeypatch, tmp_path
):
    order = _order()
    db_session.add(order)
    db_session.commit()
    monkeypatch.setattr(import_storage, "get_root", lambda: tmp_path)

    image_buf = io.BytesIO()
    Image.new("RGB", (320, 200), color="white").save(image_buf, format="JPEG")
    # 草稿图即使存在也不能进入飞书，因为它可能生成于分配工厂编号之前。
    import_storage.archive(
        db_session,
        content=image_buf.getvalue(),
        original_name=f"{order.order_date.isoformat()}_{order.order_no}.jpg",
        kind="order_sheet",
        source="auto",
        on_date=order.order_date,
    )
    sent = dispatch.order_sheet_archive_service.archive_sent_snapshot(
        db_session,
        order,
        image_buf.getvalue(),
        backfilled=True,
    )
    db_session.commit()

    row = dispatch.build_rows(db_session)[0]
    assert row["_sheet_path"] == sent.stored_path
    assert row["_sheet_signature"].startswith("factory-sheet:")
    assert row["_sheet_name"].endswith(f"_{order.order_no}_畔色321单.jpg")


def test_dispatch_rejects_sent_sheet_with_different_factory_number(
    db_session, monkeypatch, tmp_path
):
    order = _order(factory_no=321)
    db_session.add(order)
    db_session.commit()
    monkeypatch.setattr(import_storage, "get_root", lambda: tmp_path)

    image_buf = io.BytesIO()
    Image.new("RGB", (1684, 1190), color="white").save(image_buf, format="JPEG")
    import_storage.archive(
        db_session,
        content=image_buf.getvalue(),
        original_name=f"{date.today().isoformat()}_{order.order_no}_畔色320单.jpg",
        kind="order_sheet_sent",
        source="test",
        row_summary={
            "order_no": order.order_no,
            "factory_no_at_render": 320,
            "factory_label_at_render": "畔色320单",
            "render_width": 1684,
            "pushed": True,
        },
    )
    db_session.commit()

    row = dispatch.build_rows(db_session)[0]
    assert row["工厂下单号"] == "畔色321单"
    assert row["_sheet_path"] is None


def test_factory_sheet_backfill_respects_attempt_limit_on_errors(
    db_session, monkeypatch
):
    db_session.add_all(
        [
            _order(),
            _order(
                order_no="331400000000000002",
                factory_no=322,
                sku_code="SKU002",
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(dispatch, "_factory_sheet_images", lambda db: {})

    def fail_build(db, order_id):
        raise RuntimeError("render failed")

    monkeypatch.setattr(dispatch.factory_sheet, "build", fail_build)

    result = dispatch.backfill_factory_sheet_snapshots(db_session, limit=1)

    assert result["eligible"] == 2
    assert result["pending"] == 2
    assert result["attempted"] == 1
    assert len(result["errors"]) == 1


def test_factory_sheet_backfill_source_fits_database_column():
    assert len("factory_backfill") <= 16


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
