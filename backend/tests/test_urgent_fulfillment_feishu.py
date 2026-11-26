from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from app.models.order import Order, OrderDetail
from app.services import urgent_fulfillment_feishu as service, fulfillment_deadlines as deadlines
from app.services import settings_service, notify_service


DAY = "2026-11-26"
ROW = {"order_no": "synthetic-order", "label": "畔色999单", "sub_order_nos": ["child"],
       "due": "2026-11-30", "days": 4, "action": "发货", "reason": "已到提醒期限"}


@pytest.fixture
def remote(db_session, monkeypatch):
    state = {"records": [], "messages": [], "creates": 0, "fail": None}
    monkeypatch.setattr(service, "_ensure_table", lambda *a: "urgent-table")
    monkeypatch.setattr(notify_service, "get_alert_chat_id", lambda db: "alert-chat")
    def listing(db, token, table):
        if table == "urgent-table":
            records = deepcopy(state["records"])
            for r in records:
                value = r["fields"].get("剩余天数")
                if value is not None:
                    r["fields"]["剩余天数"] = str(value)
            return records
        return [
            {"record_id": "source1", "fields": {"订单号": ROW["order_no"], "子订单号": "child", "商品名称": "柜子", "SKU规格": "1000mm"}},
            {"record_id": "source2", "fields": {"订单号": ROW["order_no"], "子订单号": "refunded", "商品名称": "已退款椅子"}},
        ]
    def request(db, method, url, **kwargs):
        body = kwargs["json"]
        if url.endswith("/batch_create"):
            state["creates"] += 1
            if state["fail"] == "before":
                raise TimeoutError("unknown")
            for r in body["records"]:
                state["records"].append({"record_id": str(len(state["records"])+1), "fields": deepcopy(r["fields"])})
            if state["fail"] == "after":
                raise TimeoutError("accepted but unknown")
        elif url.endswith("/batch_update"):
            for u in body["records"]:
                next(r for r in state["records"] if r["record_id"] == u["record_id"])["fields"].update(deepcopy(u["fields"]))
        elif "/im/v1/messages" in url:
            state["messages"].append(deepcopy(body))
            if state["fail"] == "message":
                raise TimeoutError("message outcome unknown")
            return {"message_id": "om_"+str(len(state["messages"]))}
        else:
            raise AssertionError(url)
        return {}
    monkeypatch.setattr(service.fc, "list_records", listing)
    monkeypatch.setattr(service.fc, "_req", request)
    return state


def test_same_day_updates_and_next_day_history(db_session, remote):
    first = service._sync(db_session, [ROW], DAY, DAY+" 09:10")
    assert first["status"] == "verified"
    assert remote["records"][0]["fields"]["商品名称"] == "柜子"
    assert remote["records"][0]["fields"]["紧急程度"] == "非常紧急"
    service._sync(db_session, [{**ROW, "days": -1}], DAY, DAY+" 10:10")
    assert remote["records"][0]["fields"]["紧急程度"] == "逾期"
    assert len(remote["messages"]) == remote["creates"] == 1
    service._sync(db_session, [], DAY, DAY+" 11:10")
    assert remote["records"][0]["fields"]["清单状态"] == "已退出今日清单"
    service._sync(db_session, [ROW], "2026-11-27", "2026-11-27 09:10")
    assert len(remote["records"]) == len(remote["messages"]) == 2
    assert remote["records"][0]["fields"]["统计日期"] == DAY


@pytest.mark.parametrize("failure", ["before", "after"])
def test_unknown_creation_never_replayed(db_session, remote, failure):
    remote["fail"] = failure
    with pytest.raises(TimeoutError):
        service._sync(db_session, [ROW], DAY, DAY)
    remote["fail"] = None
    if failure == "before":
        with pytest.raises(RuntimeError, match="未知"):
            service._sync(db_session, [ROW], DAY, DAY)
    else:
        assert service._sync(db_session, [ROW], DAY, DAY)["status"] == "verified"
    assert remote["creates"] == 1


def test_unknown_message_does_not_resend(db_session, remote):
    remote["fail"] = "message"
    with pytest.raises(TimeoutError):
        service._sync(db_session, [ROW], DAY, DAY)
    remote["fail"] = None
    with pytest.raises(RuntimeError, match="原群消息"):
        service._sync(db_session, [ROW], DAY, DAY)
    assert len(remote["messages"]) == 1


def test_bad_readback_sends_no_message(db_session, remote, monkeypatch):
    original = service.fc.list_records
    def corrupt(db, token, table):
        records = original(db, token, table)
        if table == "urgent-table" and records:
            records[0]["fields"]["商品名称"] = "wrong"
        return records
    monkeypatch.setattr(service.fc, "list_records", corrupt)
    with pytest.raises(RuntimeError, match="未全部核实"):
        service._sync(db_session, [ROW], DAY, DAY)
    assert not remote["messages"]


def test_partial_refund_kept_full_refund_excluded(db_session):
    order = Order(platform="淘宝", order_no="refund-test", status="paid", order_date=date(2026, 11, 1),
                  paid_amount=Decimal("900"), buyer_payable_amount=Decimal("1000"),
                  refund_amount=Decimal("100"), refund_status="退款成功")
    line = OrderDetail(order_no=order.order_no, sub_order_no="c", source="import", line_status="paid",
                       qty=1, factory_no=999, amount=Decimal("1000"), refund_amount=Decimal("100"), refund_status="退款成功")
    db_session.add_all([order, line]); db_session.flush()
    rows = deadlines.pending_orders(db_session, date(2026, 11, 26))
    assert len(rows) == 1 and rows[0]["refund_note"]
    line.refund_amount = Decimal("1000"); db_session.flush()
    assert deadlines.pending_orders(db_session, date(2026, 11, 26)) == []


def test_actionable_notices_are_preserved():
    for title in ["取数登录", "畔色 ERP | 发货报表待口令", "畔色 ERP | 自动化状态", "新品开发阶段催办", "活动价保说明链接缺失"]:
        assert not notify_service._erp_only_in_alert_chat(title, "warn", "需要处理")
    assert not notify_service._erp_only_in_alert_chat("月度财务报表", "error", "生成失败")


def test_table_create_recovered_from_intent(db_session, monkeypatch):
    tables = []
    monkeypatch.setattr(service, "_tables", lambda *a: deepcopy(tables))
    monkeypatch.setattr(service.fc, "list_table_fields", lambda *a: [{"field_name": n, "type": t} for n, t in service.FIELDS])
    def create(*a, **kw):
        tables.append({"table_id": "new", "name": service.TABLE_NAME})
        raise TimeoutError("accepted but unknown")
    monkeypatch.setattr(service.fc, "_req", create)
    with pytest.raises(TimeoutError):
        service._ensure_table(db_session, "base", {})
    state = service._load(db_session)
    assert state["table_create_pending"]
    assert service._ensure_table(db_session, "base", state) == "new"
    assert len(tables) == 1
    with pytest.raises(RuntimeError, match="不接管"):
        service._ensure_table(db_session, "base", {})


@pytest.mark.parametrize("payload,valid", [
    ({"has_more": False, "items": None, "total": 0}, True),
    ({"has_more": False, "items": None, "total": 3}, False),
    ({"has_more": True, "items": None, "total": 0}, False),
    ({"has_more": False, "items": "invalid", "total": 0}, False),
])
def test_official_empty_table_and_incomplete_pages(db_session, monkeypatch, payload, valid):
    monkeypatch.setattr(service.fc, "_req", lambda *a, **kw: payload)
    if valid:
        assert service.fc.list_records(db_session, "base", "empty") == []
    else:
        with pytest.raises(service.fc.FeishuError, match="invalid_pagination"):
            service.fc.list_records(db_session, "base", "empty")


@pytest.mark.parametrize("actual,expected,equal", [
    ("-7", -7, True), ("0", 0, True), ("5.0", 5, True), (None, None, True),
    ("", None, True), ("", 0, False), ("-7", 7, False), ("5.1", 5, False),
    ("NaN", 1, False), ("not a number", 1, False), (True, 1, False),
])
def test_number_readback_preserves_values(actual, expected, equal):
    assert service._same({"剩余天数": actual}, {"剩余天数": expected}) is equal
