"""下单图推飞书: 解耦"已推图"与"本次新生成", 修复历史 bug (归档全是HTML、飞书无图)。

核心回归点: 旧逻辑只推 generate_pending 本次返回的新单号, 一旦被每小时补生成任务抢先生成,
该单 HTML 已归档却永远不再被推。新逻辑按 row_summary.pushed 判定, 与生成时机解耦。
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
    """挡掉真实飞书外发 + wkhtmltoimage 渲染, 记录被推送的单号。"""
    sent: list[str] = []
    # 测试环境 conftest 设了 PANSE_DISABLE_NOTIFY 兜底防真发; 这里已 mock 外发, 放行推送逻辑
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    # 每单返回不同字节 — 否则存档按内容 hash 去重会把第二单误判重复 (真实渲染每单不同)
    monkeypatch.setattr(osa, "render_png", lambda sheet: f"PNG-{sheet.order_no}".encode())
    monkeypatch.setattr("app.services.feishu_client.upload_image", lambda db, png: "img_key_xyz")
    monkeypatch.setattr("app.services.feishu_client.send_text", lambda db, cid, text: {"sent": True})

    def _send_image(db, cid, key):
        sent.append(cid)
        return {"sent": True}

    monkeypatch.setattr("app.services.feishu_client.send_image", _send_image)
    return sent


def _add_paid_order(db, no: str, day: int = 20):
    # 默认 6/20 ≥ _AUTO_NUMBER_SINCE(6/19): 新单会被自动顺排工厂编号, 故能正常自动推送。
    # (老单 <6/19 无编号现在被自动推送跳过, 见 test_order_sheet_push_0626 的专项回归)
    db.add(Order(platform="淘宝", order_no=no, qty=1, product_name=f"测试产品{no}", sku="标准款",
                 order_date=date(2026, 6, day), status="paid", paid_amount=Decimal("1000"),
                 customer_address="浙江省杭州市西湖区文一路1号"))
    db.flush()


def _sheet_rec(db, no: str) -> ImportedFile:
    # 2026-06-19: 下单图存档命名改为 {日期}_{订单号}.jpg, 按解析出的订单号定位 (兼容新旧命名)。
    for r in db.query(ImportedFile).filter_by(kind="order_sheet").all():
        if osa._order_no_from_name(r.original_filename) == no:
            return r
    raise AssertionError(f"未找到 {no} 的下单图归档")


def test_push_decoupled_from_generation(db_session, _feishu_stub):
    """生成(模拟每小时补生成) 与 推送 解耦: HTML 早已归档, 仍能被补推一次。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    _add_paid_order(db_session, "PUSH-A")
    _add_paid_order(db_session, "PUSH-B")

    # 模拟每小时补生成任务: 只生成 HTML, 不推图 (旧 bug 下这步会把"新单"吃光)
    gen = osa.generate_pending(db_session)
    assert set(gen["order_nos"]) == {"PUSH-A", "PUSH-B"}
    assert (_sheet_rec(db_session, "PUSH-A").row_summary or {}).get("pushed") is None

    # 现在补推: 应把两张都推出去并标记 pushed (旧逻辑这里会推 0)
    res = osa.push_pending_images(db_session, include_baseline=False)
    assert res["pushed"] == 2
    assert res["remaining"] == 0
    assert set(res["order_nos"]) == {"PUSH-A", "PUSH-B"}
    assert _feishu_stub == ["oc_factory_group", "oc_factory_group"]
    assert _sheet_rec(db_session, "PUSH-A").row_summary.get("pushed") is True
    sent = db_session.query(ImportedFile).filter_by(kind="order_sheet_sent").all()
    assert len(sent) == 2
    assert {r.row_summary["factory_label_at_render"] for r in sent} == {
        f"畔色{db_session.query(Order).filter_by(order_no=no).one().factory_no}单"
        for no in ("PUSH-A", "PUSH-B")
    }
    assert all(r.row_summary["render_width"] == 1684 for r in sent)

    # 幂等: 再推不重复
    res2 = osa.push_pending_images(db_session, include_baseline=False)
    assert res2["pushed"] == 0


def test_old_activated_remote_order_is_numbered_and_pushed(db_session, _feishu_stub):
    """6/19 前下的远期单，后来备注开始制作，也必须自动转正式工厂单。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    order = Order(
        platform="淘宝",
        order_no="OLD-ACTIVATED-PUSH",
        qty=1,
        product_name="榉木岩板餐桌",
        sku="砂白色1.8米岩板餐桌",
        sku_code="PPS-OLD-ACT",
        order_date=date(2026, 5, 24),
        status="paid",
        paid_amount=Decimal("2800"),
        customer_address="上海市松江区测试路1号",
        seller_memo="开始制作",
        remote_seq=61,
    )
    db_session.add(order)
    db_session.flush()

    generated = osa.generate_pending(db_session)
    assert generated["order_nos"] == ["OLD-ACTIVATED-PUSH"]
    assert osa.count_pending_push(db_session, include_baseline=True) == 1

    pushed = osa.push_pending_images(db_session, include_baseline=True)

    db_session.refresh(order)
    assert pushed["pushed"] == 1
    assert pushed["order_nos"] == ["OLD-ACTIVATED-PUSH"]
    assert order.factory_no is not None
    assert osa.count_pending_push(db_session, include_baseline=True) == 0
    assert _sheet_rec(db_session, order.order_no).row_summary["delivery_state"] == "sent"


def test_baseline_excluded_from_auto_but_manual_can_push(db_session, _feishu_stub):
    """历史基线: 18:00 自动(include_baseline=False)跳过, 手动(True)可补推。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    _add_paid_order(db_session, "HIST-1")
    osa.generate_pending(db_session)

    # 部署一次性基线打标
    marked = osa.baseline_existing_sheets(db_session)
    assert marked == 1
    assert _sheet_rec(db_session, "HIST-1").row_summary.get("baseline") is True

    # 自动推: 跳过历史基线
    assert osa.count_pending_push(db_session, include_baseline=False) == 0
    assert osa.push_pending_images(db_session, include_baseline=False)["pushed"] == 0

    # 手动推: 历史基线也纳入
    assert osa.count_pending_push(db_session, include_baseline=True) == 1
    res = osa.push_pending_images(db_session, include_baseline=True)
    assert res["pushed"] == 1
    assert _sheet_rec(db_session, "HIST-1").row_summary.get("pushed") is True


def test_no_chat_id_returns_reason(db_session, _feishu_stub):
    """未配推送群: 返回 reason=no_chat_id, 不抛异常 (前端据此提示去配置)。"""
    _add_paid_order(db_session, "NOCHAT-1")
    osa.generate_pending(db_session)
    res = osa.push_pending_images(db_session, include_baseline=True)
    assert res["reason"] == "no_chat_id"
    assert res["pushed"] == 0


def test_refunded_order_not_pushed(db_session, _feishu_stub):
    """退款单不推工厂 (走作废图流程)。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    o = Order(platform="淘宝", order_no="RF-1", qty=1, order_date=date(2026, 6, 8),
              status="paid", paid_amount=Decimal("800"))
    db_session.add(o)
    db_session.flush()
    osa.generate_pending(db_session)
    # 标记退款
    o.refund_amount = Decimal("800")
    o.refund_status = "退款成功"
    db_session.flush()

    res = osa.push_pending_images(db_session, include_baseline=True)
    assert res["pushed"] == 0
    assert _feishu_stub == []


def test_sample_order_not_pushed(db_session, _feishu_stub):
    """样块/样品单永不推工厂下单图 (用户 2026-07-04), 真产品单照常推。"""
    settings_service.set_value(db_session, "feishu_push_chat_id", "oc_factory_group")
    db_session.add(Order(platform="淘宝", order_no="SAMPLE-1", qty=1,
                         product_name="畔色木作木块小样樱桃木样品样块", sku="榉木样块",
                         order_date=date(2026, 6, 20), status="paid", paid_amount=Decimal("30")))
    _add_paid_order(db_session, "REAL-1")
    db_session.flush()
    osa.generate_pending(db_session)

    res = osa.push_pending_images(db_session, include_baseline=True)
    assert res["pushed"] == 1                       # 只推真产品
    assert set(res["order_nos"]) == {"REAL-1"}
    assert _feishu_stub == ["oc_factory_group"]      # 样块没发飞书
    assert _sheet_rec(db_session, "SAMPLE-1").row_summary.get("skipped_sample") is True
    # 幂等 + 样块不再占待推队列
    assert osa.push_pending_images(db_session, include_baseline=True)["pushed"] == 0


def test_is_sample_order_detection():
    """检测器命中 281 样块单真实字段, 不误伤 282 真餐桌。"""
    class _O:
        def __init__(self, name, sku):
            self.product_name = name
            self.sku = sku
    assert osa._is_sample_order(
        _O("畔色木作木块小样樱桃木黑胡桃木白蜡木榉木红白橡木样品样块", "榉木样块")) is True
    assert osa._is_sample_order(
        _O("畔色 岩板实木餐桌日式简约长方形榉木书桌家用饭桌原木小户型桌", "砂白色1.6米岩板餐桌")) is False
