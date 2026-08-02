"""解密补地址后自动重推下单图 (用户 2026-06-30: "飞书里解密后为什么没有进一步自动重新发下单图")。

根治: 缺地址的下单图推过一次即标 pushed=True, 自动推送永久跳过; 口令解密补上地址后
repush_after_address_fill 把它们清标记并定向重推一次, 幂等 (重推后 pushed_addr_ok=True)。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.import_file import ImportedFile
from app.models.order import Order
from app.services import order_sheet_archive_service as osa
from app.services import settings_service


@pytest.fixture
def _feishu_stub(monkeypatch):
    """挡掉真实飞书外发 + 渲染, 记录被推送(send_image)的次数。"""
    sent: list[str] = []
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    monkeypatch.setattr(osa, "render_png", lambda sheet: f"PNG-{sheet.order_no}".encode())
    monkeypatch.setattr("app.services.feishu_client.upload_image", lambda db, png: "img_key")
    monkeypatch.setattr("app.services.feishu_client.send_text", lambda db, cid, text: {"sent": True})
    monkeypatch.setattr("app.services.feishu_client.send_image",
                        lambda db, cid, key: sent.append(cid) or {"sent": True})
    return sent


def _sheet_rec(db, no: str) -> ImportedFile:
    for r in db.query(ImportedFile).filter_by(kind="order_sheet").all():
        if osa._order_no_from_name(r.original_filename) == no:
            return r
    raise AssertionError(f"未找到 {no} 的下单图归档")


def _add_order(db, no: str, *, address: str | None):
    db.add(Order(platform="淘宝", order_no=no, qty=1, product_name=f"产品{no}", sku="标准款",
                 order_date=date(2026, 6, 20), status="paid", paid_amount=Decimal("1000"),
                 customer_name=("张三" if address else None),
                 customer_phone=("13800000000" if address else None),
                 customer_address=address))
    db.flush()


def test_repush_only_for_orders_that_gained_address(db_session, _feishu_stub):
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    _add_order(db_session, "NOADDR-1", address=None)                       # 无地址
    _add_order(db_session, "HASADDR-1", address="广东省深圳市南山区科技园路1号")  # 有地址
    osa.generate_pending(db_session)

    # 首推: 缺地址的必须暂缓，只有完整地址可以发给工厂。
    res = osa.push_pending_images(db_session, include_baseline=False)
    assert res["pushed"] == 1
    assert res["held_no_address"] == ["NOADDR-1"]
    assert not (_sheet_rec(db_session, "NOADDR-1").row_summary or {}).get("pushed")
    assert _sheet_rec(db_session, "HASADDR-1").row_summary.get("pushed_addr_ok") is True

    # 地址还没补 → 没有可重推的
    assert osa.repush_after_address_fill(db_session)["repushed"] == 0

    # 解密把地址补上
    o = db_session.query(Order).filter_by(order_no="NOADDR-1").one()
    o.customer_name, o.customer_phone = "李四", "13900000000"
    o.customer_address = "浙江省杭州市西湖区文一路100号"
    db_session.flush()

    # 口令回调应直接释放被地址安全门暂缓的记录，不依赖整轮取数完成标记。
    r = osa.repush_after_address_fill(db_session)
    assert r["repushed"] == 1
    assert r["order_nos"] == ["NOADDR-1"]
    rec = _sheet_rec(db_session, "NOADDR-1").row_summary
    assert rec.get("pushed") is True and rec.get("pushed_addr_ok") is True

    # 幂等: 已带地址, 再调用为 0 (不会无限重推)
    assert osa.repush_after_address_fill(db_session)["repushed"] == 0


def test_legacy_pushed_without_flag_never_repushed(db_session, _feishu_stub):
    """安全默认: 部署前推送的历史单 (row_summary 只有 pushed, 无 pushed_addr_ok) 即使现在有地址,
    也绝不被自动重推 —— 防一次口令把整批历史已发送单刷给工厂群。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    _add_order(db_session, "LEGACY-1", address="广东省深圳市福田区福华路1号")
    osa.generate_pending(db_session)
    # 模拟历史: 只标 pushed=True, 没有 pushed_addr_ok 键 (旧版本推送行为)
    rec = _sheet_rec(db_session, "LEGACY-1")
    rec.row_summary = {"pushed": True}
    db_session.flush()

    assert osa.find_pushed_without_address(db_session) == []
    assert osa.repush_after_address_fill(db_session)["repushed"] == 0


def test_masked_address_counts_as_missing(db_session, _feishu_stub):
    """星号脱敏地址绝不发送；解密成真实地址后才首次推送。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    _add_order(db_session, "MASK-1", address="广东省***************")
    osa.generate_pending(db_session)
    first = osa.push_pending_images(db_session, include_baseline=False)
    assert first["pushed"] == 0
    assert first["held_no_address"] == ["MASK-1"]
    assert not (_sheet_rec(db_session, "MASK-1").row_summary or {}).get("pushed")

    o = db_session.query(Order).filter_by(order_no="MASK-1").one()
    o.customer_address = "广东省广州市天河区天河路385号"
    db_session.flush()
    assert osa.push_pending_images(db_session, include_baseline=False)["pushed"] == 1


def test_old_signed_row_is_not_reported_as_address_blocker(db_session, _feishu_stub):
    """Historical rows outside the factory queue must not inflate address alerts."""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    _add_order(db_session, "OLD-SIGNED-1", address="Guangdong **************")
    order = db_session.query(Order).filter_by(order_no="OLD-SIGNED-1").one()
    order.order_date = date(2026, 6, 7)
    osa.generate_pending(db_session)
    order.status = "signed"
    db_session.flush()

    result = osa.push_pending_images(db_session, include_baseline=False)

    assert result["pushed"] == 0
    assert result["held_no_address"] == []
    assert osa.count_pending_push(db_session, include_baseline=False) == 0


def test_address_defer_is_not_a_delivery_failure(db_session, _feishu_stub):
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    _add_order(db_session, "DEFER-ADDR-1", address="Zhejiang **************")

    result = osa.reconcile_pending_delivery(db_session)

    assert result["images_remaining"] == 1
    assert result["images_deferred_no_address"] == 1
    assert result["held_no_address"] == ["DEFER-ADDR-1"]
    assert result.get("_run_status") is None


def test_apply_shipping_password_triggers_repush(db_session, _feishu_stub, monkeypatch):
    """端到端: 收到飞书口令解密入库成功 → apply_shipping_password 自动重推缺地址单。"""
    from app.services import feishu_bot_service
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    _add_order(db_session, "PWD-1", address=None)
    osa.generate_pending(db_session)
    osa.push_pending_images(db_session, include_baseline=False)
    assert not (_sheet_rec(db_session, "PWD-1").row_summary or {}).get("pushed")

    # 模拟"口令解密入库"补上地址 + 返回 imported=1
    def _fake_reingest(db):
        o = db.query(Order).filter_by(order_no="PWD-1").one()
        o.customer_name, o.customer_phone = "王五", "13700000000"
        o.customer_address = "江苏省南京市玄武区中山路1号"
        db.flush()
        return {"imported": 1, "updated": 1, "tried": 1}

    monkeypatch.setattr("app.services.agent_ingest_service.reingest_pending_shipping", _fake_reingest)
    monkeypatch.setattr(
        "app.services.agent_ingest_service.finalize_order_pull_after_shipping_password",
        lambda db: {"completed": True},
    )
    r = feishu_bot_service.apply_shipping_password(db_session, "9oMdwP6L")
    assert r.get("imported") == 1
    assert r.get("repushed") == 1
    assert r["delivery"]["images_pushed"] == 0
    assert r["order_pull_completion"]["completed"] is True
    rec = _sheet_rec(db_session, "PWD-1").row_summary
    assert rec.get("pushed") is True and rec.get("pushed_addr_ok") is True


def test_apply_shipping_password_immediately_reconciles_new_sheets(
    db_session, _feishu_stub, monkeypatch
):
    """口令是最后一环时，不再只说“可正常发”，而是当场续跑未送达图片。"""
    from app.services import feishu_bot_service

    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    monkeypatch.setattr(
        "app.services.agent_ingest_service.reingest_pending_shipping",
        lambda db: {"imported": 1, "updated": 9, "tried": 1},
    )
    monkeypatch.setattr(
        "app.services.agent_ingest_service.finalize_order_pull_after_shipping_password",
        lambda db: {"completed": True, "completed_at": "2026-07-27T18:25:00"},
    )
    monkeypatch.setattr(osa, "repush_after_address_fill", lambda db, **kwargs: {"repushed": 0})
    monkeypatch.setattr(
        osa,
        "reconcile_pending_delivery",
        lambda db, **kwargs: {
            "images_pushed": 8,
            "images_failed": 0,
            "images_remaining": 0,
            "order_nos": ["A", "B"],
        },
    )

    result = feishu_bot_service.apply_shipping_password(db_session, "9oMdwP6L")

    assert result["delivery"]["images_pushed"] == 8
    assert result["delivery"].get("_run_status") is None


def test_password_mismatch_pauses_retry_and_keeps_exact_reason(
    db_session, _feishu_stub, monkeypatch
):
    from app.services import automation_pipeline_service as pipeline
    from app.services import feishu_bot_service

    pipeline.record_failure(
        db_session,
        "order_delivery",
        "发货报表待口令",
        retry_slots=[],
    )
    monkeypatch.setattr(
        "app.services.agent_ingest_service.reingest_pending_shipping",
        lambda db: {
            "imported": 0,
            "updated": 0,
            "tried": 1,
            "failed": 1,
            "files": [{
                "file": "ExportOrderList26853427410.xlsx",
                "status": "pending",
                "note": "The file could not be decrypted with this password",
            }],
        },
    )
    monkeypatch.setattr(
        "app.services.agent_ingest_service.pending_shipping_password_files",
        lambda db, **_kwargs: ["ExportOrderList26853427410.xlsx"],
    )

    result = feishu_bot_service.apply_shipping_password(db_session, "not-the-password")

    assert result["imported"] == 0
    assert "ExportOrderList26853427410.xlsx" in result["failure_reason"]
    state = pipeline.get_pipeline(db_session, "order_delivery")
    assert state["waiting_input"] is True
    assert state["final"] is True
    assert pipeline.needs_retry(db_session, "order_delivery") is False


def test_relay_password_mismatch_is_reported_in_reply(db_session, monkeypatch):
    from app.services import feishu_bot_service

    monkeypatch.setattr(
        feishu_bot_service,
        "apply_shipping_password",
        lambda db, pwd: {"imported": 0, "tried": 0},
    )
    monkeypatch.setattr(
        feishu_bot_service,
        "_relay_shipping_password",
        lambda db, pwd: {
            "imported": 0,
            "tried": 1,
            "failed": 1,
            "failure_reason": (
                "ExportOrderList26853427410.xlsx: 口令与该报表不匹配"
            ),
        },
    )
    cards: list[dict] = []
    monkeypatch.setattr(
        feishu_bot_service,
        "_result_card",
        lambda title, body, color: {
            "title": title, "body": body, "color": color,
        },
    )
    monkeypatch.setattr(
        feishu_bot_service,
        "_safe_reply",
        lambda db, message_id, card: cards.append(card),
    )

    result = feishu_bot_service._capture_shipping_password(
        db_session, "message-1", "not-the-password",
    )

    assert "ExportOrderList26853427410.xlsx" in result["failure_reason"]
    assert cards[0]["title"] == "口令未匹配发货报表"
    assert cards[0]["color"] == "orange"
    assert "暂停无效重试" in cards[0]["body"]
