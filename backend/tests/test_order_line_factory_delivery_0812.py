"""2026-08-12: 淘宝子订单逐商品工厂制单闭环。"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail
from app.services import order_line_delivery_service as lines
from app.services import order_sheet_archive_service as sheets
from app.services import settings_service
from app.services import taobao_order_import as importer
from app.services import factory_dispatch_feishu_service as dispatch


def _order(db, no: str, *, factory_no=None, name="床") -> Order:
    row = Order(
        platform="淘宝", order_no=no, order_date=date(2026, 8, 12), status="paid",
        paid_amount=Decimal("5000"), product_name=name, sku="标准",
        sku_code="PPS2633007032018", product_code="PPS26330070320",
        qty=1, factory_no=factory_no, customer_name="徐陈欢",
        customer_phone="18413251887", customer_address="上海市松江区测试路1号",
    )
    db.add(row); db.flush()
    return row


def _line(db, order_no: str, sub: str, name: str, sku: str, *, refunded=False) -> OrderDetail:
    row = OrderDetail(
        sync_key=f"line:{sub}", sub_order_no=sub, order_no=order_no,
        product_name=name, product_code=sku[:14], sku_code=sku, sku_name=name,
        qty=1, amount=Decimal("2500"), source="import",
        line_status="cancelled" if refunded else "paid",
        refund_status="退款成功" if refunded else "没有申请退款",
        refund_amount=Decimal("2500") if refunded else Decimal("0"),
    )
    db.add(row); db.flush()
    return row


def _sent(db, order: Order, *, factory_no: int) -> ImportedFile:
    row = ImportedFile(
        kind="order_sheet_sent", original_filename=f"{order.order_no}.jpg",
        stored_path=f"/tmp/{order.order_no}.jpg", source="test",
        row_summary={
            "order_no": order.order_no, "factory_no_at_render": factory_no,
            "factory_label_at_render": f"畔色{factory_no}单", "pushed": True,
        },
    )
    db.add(row); db.flush()
    return row


def test_import_persists_child_order_status_and_refund(db_session):
    raw = importer._sales_detail_csv_bytes() if hasattr(importer, "_sales_detail_csv_bytes") else None
    # Use the public importer with a real-shaped two-line CSV.
    import csv, io
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow([
        "子订单编号", "主订单编号", "商品标题", "购买数量", "商家编码",
        "商品属性", "订单状态", "买家应付货款", "退款状态", "退款金额", "订单创建时间",
    ])
    writer.writerow(["SUB-BED", "MAIN-1", "榉木床", 1, "PPS2633007032018", "颜色分类:榉木床", "买家已付款,等待卖家发货", 2800, "没有申请退款", "无退款申请", "2026-08-12 10:00:00"])
    writer.writerow(["SUB-TABLE", "MAIN-1", "岩板餐桌", 1, "PPS2421007090112", "颜色分类:餐桌", "交易关闭", 2600, "退款成功", 2600, "2026-08-12 10:00:00"])
    importer.import_taobao_orders(db_session, "detail.csv", output.getvalue().encode("gbk"))

    bed = db_session.query(OrderDetail).filter_by(sub_order_no="SUB-BED").one()
    table = db_session.query(OrderDetail).filter_by(sub_order_no="SUB-TABLE").one()
    assert bed.sync_key == "line:SUB-BED" and bed.line_status == "paid"
    assert table.line_status == "cancelled" and table.refund_amount == Decimal("2600")
    assert bed.factory_delivery_required is True
    assert lines.line_is_refunded(table) is True


def test_legacy_multi_product_binds_sent_bed_only(db_session):
    order = _order(db_session, "3316523196113117592", factory_no=363, name="榉木床")
    bed = _line(db_session, order.order_no, "3316523196113126774", "榉木床", "PPS2633007032018")
    table = _line(db_session, order.order_no, "3316523196113135956", "岩板餐桌", "PPS2421007090112", refunded=True)
    _sent(db_session, order, factory_no=363)
    db_session.commit()

    result = lines.bind_unambiguous_legacy_evidence(db_session)

    assert result["bound"] == [bed.sub_order_no]
    assert bed.factory_no == 363 and bed.factory_delivery_state == "sent"
    assert table.factory_no is None and table.factory_delivery_required is False
    assert lines.delivery_count_gate(db_session)["ok"] is True


def test_legacy_refunded_representative_is_not_misbound_to_active_sibling(db_session):
    """旧主单图若实际代表退款柜子，不能因为只剩床有效就冒充床的送达凭证。"""
    order = _order(db_session, "MAIN-REFUNDED-REP", factory_no=720, name="退款餐边柜")
    order.sku_code = "PFG2525001122511"
    bed = _line(db_session, order.order_no, "SUB-ACTIVE-BED", "樱桃木床", "PPS2633011022614")
    _line(db_session, order.order_no, "SUB-REFUNDED-CABINET", "退款餐边柜", "PFG2525001122511", refunded=True)
    _sent(db_session, order, factory_no=720)
    db_session.commit()

    result = lines.bind_unambiguous_legacy_evidence(db_session)

    assert result["bound"] == []
    assert result["ambiguous_order_nos"] == [order.order_no]
    assert bed.factory_delivery_required is False
    assert bed.factory_delivery_state is None


def test_legacy_binding_factory_number_conflict_does_not_abort_batch(db_session):
    """旧主单号已被新的子订单占用时应报告冲突，不能让整个定时任务回滚。"""
    occupied_order = _order(db_session, "OCCUPIED")
    occupied = _line(db_session, occupied_order.order_no, "SUB-OCCUPIED", "床头柜", "PPS2638004022511")
    occupied.factory_no = 721
    occupied.factory_delivery_required = True

    order = _order(db_session, "MAIN-CONFLICT", factory_no=721, name="樱桃木床")
    line = _line(db_session, order.order_no, "SUB-CONFLICT", "樱桃木床", order.sku_code)
    _sent(db_session, order, factory_no=721)
    db_session.commit()

    result = lines.bind_unambiguous_legacy_evidence(db_session)

    assert result["bound"] == []
    assert result["factory_no_conflicts"] == [{
        "order_no": order.order_no,
        "factory_no": 721,
        "occupied_sub_order_no": occupied.sub_order_no,
    }]
    assert line.factory_no is None


def test_partial_refund_legacy_order_switches_to_child_delivery_before_render(db_session, _feishu):
    settings_service.set_value(db_session, "feishu_push_chat_id", "factory-chat")
    order = _order(db_session, "MAIN-PARTIAL-REFUND", name="退款餐边柜")
    order.order_date = date(2026, 6, 10)
    order.seller_memo = "开始制作"
    order.sku_code = "PFG2525001122511"
    bed = _line(db_session, order.order_no, "SUB-BED-ONLY", "樱桃木床", "PPS2633011022614")
    cabinet = _line(db_session, order.order_no, "SUB-CABINET-REFUND", "退款餐边柜", "PFG2525001122511", refunded=True)
    order.refund_amount = cabinet.refund_amount
    db_session.commit()

    generated = sheets.generate_pending(db_session)
    assert generated["generated"] == 0
    assert bed.factory_delivery_required is True
    assert cabinet.factory_delivery_required is True
    assert db_session.query(ImportedFile).filter_by(kind="order_sheet").count() == 0

    result = sheets.reconcile_order_line_delivery(db_session)
    assert result["sub_order_nos"] == [bed.sub_order_no]
    assert cabinet.factory_delivery_state is None
    assert _feishu == ["img-key"]


def test_legacy_factory_number_sequence_includes_child_numbers(db_session):
    order = _order(db_session, "MAIN-NUMBER")
    order.factory_no = 730
    line = _line(db_session, order.order_no, "SUB-NUMBER", "樱桃木床", order.sku_code)
    line.factory_no = 735
    db_session.flush()

    assert sheets._next_factory_no(db_session) == 736


@pytest.fixture
def _feishu(monkeypatch):
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    sent = []
    monkeypatch.setattr(sheets, "render_png", lambda sheet: f"IMG:{sheet.order_no}".encode())
    monkeypatch.setattr("app.services.feishu_client.upload_image", lambda db, content: "img-key")
    monkeypatch.setattr("app.services.feishu_client.send_text", lambda db, chat, text: {"message_id": "txt"})
    monkeypatch.setattr("app.services.feishu_client.send_image", lambda db, chat, key: sent.append(key) or {"message_id": "img"})
    return sent


def test_new_table_is_sent_once_as_own_child_order(db_session, _feishu):
    settings_service.set_value(db_session, "feishu_push_chat_id", "factory-chat")
    order = _order(db_session, "3316351911689080765", name="岩板餐桌")
    line = _line(db_session, order.order_no, "3316351911689080765", "岩板餐桌", "PPS2421007090113")
    line.factory_delivery_required = True
    db_session.commit()

    result = sheets.reconcile_order_line_delivery(db_session)
    assert result["pushed"] == 1
    assert line.factory_no is not None and line.factory_delivery_state == "sent"
    assert lines.delivery_count_gate(db_session)["ok"] is True
    # Idempotent: a second reconciliation cannot send the image again.
    assert sheets.reconcile_order_line_delivery(db_session)["pushed"] == 0
    assert _feishu == ["img-key"]


def test_activated_backfill_evidence_is_superseded_and_line_really_repushes(
    db_session, _feishu,
):
    """仅重建存档的 backfill 不能让激活单形成“已送达”假成功。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "factory-chat")
    order = _order(db_session, "3307063044854158575", factory_no=None, name="榉木床头柜")
    order.order_date = date(2026, 6, 7)
    order.is_customer_delayed = True
    order.production_note = "延期发货"
    order.seller_memo = "开始制作，15日安排发货"
    line = _line(
        db_session,
        order.order_no,
        order.order_no,
        "榉木床头柜",
        "PPS2638004022511",
    )
    line.factory_delivery_required = True
    line.factory_no = 105
    line.factory_delivery_state = "sent"
    backfill = ImportedFile(
        kind="order_sheet_sent",
        original_filename=f"{order.order_no}_畔色105单.jpg",
        stored_path=f"/tmp/{order.order_no}.jpg",
        source="factory_backfill",
        row_summary={
            "order_no": order.order_no,
            "sub_order_no": line.sub_order_no,
            "line_id": line.id,
            "factory_no_at_render": 105,
            "factory_label_at_render": "畔色105单",
            "render_width": 1684,
            "pushed": True,
            "backfilled": True,
            "legacy_line_binding": True,
        },
    )
    db_session.add(backfill)
    db_session.commit()

    reset = sheets.repush_activated(db_session)

    assert reset["reset_for_new_no"] == [order.order_no]
    assert reset["superseded_sub_order_nos"] == [line.sub_order_no]
    db_session.refresh(backfill)
    db_session.refresh(line)
    assert backfill.row_summary["delivery_superseded"] is True
    assert line.factory_no is None and line.factory_delivery_state is None
    assert line.sub_order_no not in lines.sent_line_evidence(db_session)
    assert lines.delivery_count_gate(db_session)["missing_sub_order_nos"] == [line.sub_order_no]

    pushed = sheets.reconcile_order_line_delivery(db_session)

    assert pushed["sub_order_nos"] == [line.sub_order_no]
    assert line.factory_no is not None and line.factory_no != 105
    assert line.factory_delivery_state == "sent"
    current = lines.sent_line_evidence(db_session)[line.sub_order_no]
    assert current.id != backfill.id
    assert current.row_summary["activated"] is True
    assert lines.delivery_count_gate(db_session)["ok"] is True
    # 第三轮调用仍必须幂等，不能因为激活标记缺失而再次重推。
    assert sheets.repush_activated(db_session)["reset_for_new_no"] == []
    assert sheets.reconcile_order_line_delivery(db_session)["pushed"] == 0
    assert _feishu == ["img-key"]


def test_quantity_gate_blocks_false_success(db_session):
    order = _order(db_session, "MAIN-GATE")
    line = _line(db_session, order.order_no, "SUB-MISSING", "餐桌", "PPS2421007090113")
    line.factory_delivery_required = True
    db_session.commit()

    gate = lines.delivery_count_gate(db_session)
    assert gate["ok"] is False
    assert gate["active_product_count"] == 1
    assert gate["sent_factory_sheet_count"] == 0
    assert gate["missing_sub_order_nos"] == ["SUB-MISSING"]


def test_refunded_never_sent_line_is_not_voided_or_pushed(db_session, _feishu):
    settings_service.set_value(db_session, "feishu_push_chat_id", "factory-chat")
    order = _order(db_session, "MAIN-REFUND")
    line = _line(db_session, order.order_no, "SUB-REFUND", "餐桌", "PPS2421007090112", refunded=True)
    line.factory_delivery_required = True
    db_session.commit()

    assert sheets.reconcile_order_line_delivery(db_session)["pushed"] == 0
    assert sheets.reconcile_refunded_order_lines(db_session)["voided"] == 0
    assert _feishu == []


def test_factory_dispatch_has_one_row_per_child_order(db_session):
    order = _order(db_session, "MAIN-DISPATCH", factory_no=700)
    bed = _line(db_session, order.order_no, "SUB-D-BED", "榉木床", "PPS2633007032018")
    table = _line(db_session, order.order_no, "SUB-D-TABLE", "岩板餐桌", "PPS2421007090113")
    bed.factory_delivery_required = table.factory_delivery_required = True
    bed.factory_no, table.factory_no = 700, 701
    bed.factory_delivery_state = table.factory_delivery_state = "sent"
    for line in (bed, table):
        db_session.add(ImportedFile(
            kind="order_sheet_sent", original_filename=f"{line.sub_order_no}.jpg",
            stored_path=f"/tmp/{line.sub_order_no}.jpg", source="test",
            file_hash=f"hash-{line.sub_order_no}",
            row_summary={
                "order_no": order.order_no, "sub_order_no": line.sub_order_no,
                "factory_no_at_render": line.factory_no,
                "factory_label_at_render": f"畔色{line.factory_no}单",
                "render_width": 1684, "pushed": True,
            },
        ))
    db_session.commit()

    rows = dispatch.build_rows(db_session)
    child_rows = [row for row in rows if row.get("订单号") == order.order_no]
    assert {row["子订单号"] for row in child_rows} == {"SUB-D-BED", "SUB-D-TABLE"}
    assert {row["工厂下单号"] for row in child_rows} == {"畔色700单", "畔色701单"}


def test_factory_dispatch_excludes_refunded_child_even_if_previously_sent(db_session):
    order = _order(db_session, "MAIN-DISPATCH-REFUND", factory_no=702)
    active = _line(
        db_session,
        order.order_no,
        "SUB-D-ACTIVE",
        "榉木床",
        "PPS2633007032018",
    )
    refunded = _line(
        db_session,
        order.order_no,
        "SUB-D-REFUNDED",
        "岩板餐桌",
        "PPS2421007090113",
        refunded=True,
    )
    active.factory_delivery_required = refunded.factory_delivery_required = True
    active.factory_no, refunded.factory_no = 702, 703
    active.factory_delivery_state = refunded.factory_delivery_state = "sent"
    for line in (active, refunded):
        db_session.add(ImportedFile(
            kind="order_sheet_sent",
            original_filename=f"{line.sub_order_no}.jpg",
            stored_path=f"/tmp/{line.sub_order_no}.jpg",
            source="test",
            file_hash=f"hash-{line.sub_order_no}",
            row_summary={
                "order_no": order.order_no,
                "sub_order_no": line.sub_order_no,
                "factory_no_at_render": line.factory_no,
                "factory_label_at_render": f"畔色{line.factory_no}单",
                "render_width": 1684,
                "pushed": True,
            },
        ))
    db_session.commit()

    rows = dispatch.build_rows(db_session)

    assert [row["子订单号"] for row in rows] == [active.sub_order_no]
    assert refunded.sub_order_no in dispatch._ineligible_factory_entity_keys(db_session)


def test_dispatch_sync_upgrades_legacy_main_order_row(monkeypatch, db_session):
    order = _order(db_session, "MAIN-UPGRADE", factory_no=710)
    line = _line(db_session, order.order_no, "SUB-UPGRADE", "榉木床", "PPS2633007032018")
    line.factory_delivery_required = True
    line.factory_no = 710
    line.factory_delivery_state = "sent"
    order.wood_cost_est = Decimal("1000")
    db_session.add(ImportedFile(
        kind="order_sheet_sent", original_filename="upgrade.jpg",
        stored_path="/tmp/upgrade.jpg", source="test", file_hash="upgrade-hash",
        row_summary={
            "order_no": order.order_no, "sub_order_no": line.sub_order_no,
            "factory_no_at_render": 710, "factory_label_at_render": "畔色710单",
            "render_width": 1684, "pushed": True,
        },
    ))
    db_session.commit()
    monkeypatch.setattr(dispatch, "_ensure_schema", lambda *a, **k: {})
    monkeypatch.setattr("app.services.feishu_client.list_table_fields", lambda *a, **k: [
        {"field_name": name, "type": typ, "field_id": name, "is_primary": name == "工厂下单号"}
        for name, typ in dispatch.FIELD_SPECS
    ])
    monkeypatch.setattr("app.services.feishu_client.list_views", lambda *a, **k: [
        {"view_id": vid, "view_name": name, "view_type": kind}
        for vid, (name, kind) in dispatch.EXPECTED_VIEWS.items()
    ])
    monkeypatch.setattr("app.services.feishu_client.list_records", lambda *a, **k: [{
        "record_id": "legacy-row",
        "fields": {"工厂下单号": "畔色710单", "订单号": order.order_no},
    }])
    created, updated = [], []
    monkeypatch.setattr("app.services.feishu_client.batch_create_records", lambda db, app, table, rows: created.extend(rows) or [])
    monkeypatch.setattr("app.services.feishu_client.batch_update_records", lambda db, app, table, rows: updated.extend(rows) or [])
    monkeypatch.setattr("app.services.feishu_client.batch_delete_records", lambda *a, **k: 0)
    monkeypatch.setattr(dispatch, "_ensure_urgency_option_styles", lambda *a, **k: False)
    monkeypatch.setattr(dispatch, "_load_image_cache", lambda db: {})

    result = dispatch.sync(db_session, include_images=False)
    assert result["ok"] is True, result
    assert created == []
    assert updated[0]["record_id"] == "legacy-row"
    assert updated[0]["fields"]["子订单号"] == "SUB-UPGRADE"
