"""Daily urgent-order snapshots in a separate table of the existing factory base.

Only this table is written. Unknown creates are reconciled by key, never replayed.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import threading
import uuid

from sqlalchemy import text

from app.services import feishu_client as fc, settings_service as settings
from app.services import factory_dispatch_feishu_service as factory, notify_service

ENABLED_KEY = "urgent_fulfillment_feishu_enabled"
STATE_KEY = "urgent_fulfillment_feishu_v1"
TABLE_NAME = "紧急订单（每日）"
FIELDS = [(n, 1) for n in (
    "工厂下单号", "统计日期", "紧急程度", "商品名称", "SKU规格", "目标日期",
    "跟进事项", "提醒原因", "清单状态", "订单号", "子订单号", "记录键", "更新时间",
    "退款提示",
)] + [("剩余天数", 2)]
_LOCK = threading.Lock()
_PG_LOCK = 726112601


def _load(db):
    return json.loads(settings.get(db, STATE_KEY, env_fallback=False) or "{}")


def _save(db, state):
    settings.set_value(db, STATE_KEY, json.dumps(state, ensure_ascii=False))
    db.commit()


@contextmanager
def _lock(db):
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("紧急订单表正在同步")
    try:
        if db.bind.dialect.name != "postgresql":
            yield
        else:
            # Hold a dedicated connection: commits of durable intents must not
            # release the lock or return its session to the connection pool.
            with db.bind.connect() as conn:
                if not conn.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": _PG_LOCK}):
                    raise RuntimeError("紧急订单表正在其他进程同步")
                try:
                    yield
                finally:
                    conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _PG_LOCK})
    finally:
        _LOCK.release()


def _text(value):
    if isinstance(value, list):
        return "".join(str(v.get("text", "")) if isinstance(v, dict) else str(v) for v in value)
    return str(value or "")


def _tables(db, token):
    result, cursor, seen = [], None, set()
    for _ in range(100):
        data = fc._req(db, "GET", fc._BASE + f"/bitable/v1/apps/{token}/tables",
                       params={"page_size": 100, **({"page_token": cursor} if cursor else {})})
        result.extend(data.get("items", []))
        if data.get("has_more") is False:
            return result
        cursor = data.get("page_token")
        if not cursor or cursor in seen:
            raise RuntimeError("紧急订单表目录未完整读取")
        seen.add(cursor)
    raise RuntimeError("紧急订单表目录页数超限")


def _ensure_table(db, token, state):
    tables = _tables(db, token)
    if state.get("app_token") and state["app_token"] != token:
        raise RuntimeError("工厂表绑定已变化，请核对紧急订单表")
    if state.get("table_id"):
        matched = [t for t in tables if t["table_id"] == state["table_id"] and t["name"] == TABLE_NAME]
    else:
        matched = [t for t in tables if t["name"] == TABLE_NAME]
    if len(matched) > 1:
        raise RuntimeError("存在同名紧急订单表，请核对")
    if matched and not state.get("table_id") and not state.get("table_create_pending"):
        raise RuntimeError("已有同名表但无本程序创建记录，不接管已有表")
    if not matched:
        if state.get("table_id") or state.get("table_create_pending"):
            raise RuntimeError("紧急订单表创建结果待核对，禁止重复创建")
        state.update(app_token=token, table_create_pending=True)
        _save(db, state)
        fc._req(db, "POST", fc._BASE + f"/bitable/v1/apps/{token}/tables",
                json={"table": {"name": TABLE_NAME, "default_view_name": "每日紧急清单",
                                 "fields": [{"field_name": n, "type": t} for n, t in FIELDS]}})
        matched = [t for t in _tables(db, token) if t["name"] == TABLE_NAME]
        if len(matched) != 1:
            raise RuntimeError("紧急订单表创建后未核实")
    table = matched[0]["table_id"]
    fields = {f["field_name"]: f["type"] for f in fc.list_table_fields(db, token, table)}
    if any(fields.get(n) != t for n, t in FIELDS):
        raise RuntimeError("紧急订单表字段不匹配，不覆盖已有表结构")
    state.update(app_token=token, table_id=table, table_create_pending=False)
    _save(db, state)
    return table


def build_rows(rows, source, day, updated_at):
    grouped = {}
    for record in source:
        fields = record["fields"]
        grouped.setdefault(_text(fields.get("订单号")), []).append(fields)
    result = {}
    for row in rows:
        order = row["order_no"]
        children = set(row.get("sub_order_nos", []))
        originals = [f for f in grouped.get(order, [])
                     if not children or _text(f.get("子订单号")) in children]
        def joined(name):
            return "；".join(dict.fromkeys(_text(f.get(name)) for f in originals if f.get(name)))
        days = row["days"]
        urgency = ("逾期" if days is not None and days < 0 else
                   "非常紧急" if days is not None and days <= 5 else "紧急")
        if row["action"] == "核实交期":
            urgency = "交期待核实"
        key = f"{day}:{order}"
        result[key] = {
            "工厂下单号": row["label"], "统计日期": day, "紧急程度": urgency,
            "商品名称": joined("商品名称") or "尚未匹配工厂表，请核实",
            "SKU规格": joined("SKU规格"), "目标日期": row["due"] or "待核实",
            "跟进事项": row["action"] if originals else "核实订单状态及工厂下单记录",
            "提醒原因": row["reason"], "清单状态": "待跟进",
            "订单号": order, "子订单号": "、".join(sorted(children)), "记录键": key,
            "更新时间": updated_at, "退款提示": row.get("refund_note", ""),
            "剩余天数": days,
        }
    return result


def _index(records):
    result = {}
    for r in records:
        key = _text(r["fields"].get("记录键"))
        if not key or key in result:
            raise RuntimeError("紧急订单表存在空白或重复记录键，请核对")
        result[key] = r
    return result


def _same(actual, expected):
    return all((_number_equal(actual.get(k), v) if k == "剩余天数" else _text(actual.get(k)) == _text(v))
               for k, v in expected.items())


def _number_equal(actual, expected):
    # Bitable Number fields read back as decimal strings, including zero and
    # negative days. Empty is distinct from zero; no rounding hides a mismatch.
    if expected is None:
        return actual is None or actual == ""
    if isinstance(actual, bool) or actual is None or actual == "":
        return False
    try:
        number = Decimal(str(actual))
        return number.is_finite() and number == Decimal(str(expected))
    except (InvalidOperation, ValueError):
        return False


def sync_daily(db, rows, *, now=None):
    if settings.get(db, ENABLED_KEY, env_fallback=False) != "1":
        return {"status": "disabled"}
    if __import__("os").environ.get("PANSE_DISABLE_NOTIFY"):
        return {"status": "test_disabled"}
    from app.services.fulfillment_deadlines import _BEIJING
    now = now or datetime.now(_BEIJING)
    day = now.astimezone(_BEIJING).date().isoformat()
    with _lock(db):
        return _sync(db, rows, day, now.astimezone(_BEIJING).strftime("%Y-%m-%d %H:%M"))


def _sync(db, rows, day, updated_at):
    token = settings.get(db, factory.APP_TOKEN_KEY, env_fallback=False) or factory.DEFAULT_APP_TOKEN
    factory_table = settings.get(db, factory.TABLE_ID_KEY, env_fallback=False) or factory.DEFAULT_TABLE_ID
    chat = notify_service.get_alert_chat_id(db)
    if not chat:
        raise RuntimeError("未配置ERP提醒群")
    state = _load(db)
    table = _ensure_table(db, token, state)
    if table == factory_table:
        raise RuntimeError("紧急表不得覆盖工厂原表")
    base = fc._BASE + f"/bitable/v1/apps/{token}/tables/{table}/records"
    current = _index(fc.list_records(db, token, table))
    pending = set(state.get("pending_rows", [])) - current.keys()
    if pending:
        raise RuntimeError("紧急订单行创建结果未知，需核对原记录，不重复新增")
    state["pending_rows"] = []
    source = fc.list_records(db, token, factory_table)
    expected = build_rows(rows, source, day, updated_at)
    # Same-day rows that leave the active set are retained with an explicit
    # status; previous days and any employee-added columns remain untouched.
    for key, record in current.items():
        if _text(record["fields"].get("统计日期")) == day and key not in expected:
            expected[key] = {"清单状态": "已退出今日清单", "更新时间": updated_at}
    creates = [{"fields": f} for k, f in expected.items() if k not in current]
    for start in range(0, len(creates), 500):
        chunk = creates[start:start + 500]
        state["pending_rows"] = [r["fields"]["记录键"] for r in chunk]
        _save(db, state)
        # Deliberately bypass batch_create_records' fallback-on-timeout.
        fc._req(db, "POST", base + "/batch_create", json={"records": chunk})
        readback = _index(fc.list_records(db, token, table))
        if any(k not in readback or not _same(readback[k]["fields"], expected[k]) for k in state["pending_rows"]):
            raise RuntimeError("紧急订单新增行未全部核实")
        state["pending_rows"] = []
        _save(db, state)
    updates = [{"record_id": current[k]["record_id"], "fields": f} for k, f in expected.items()
               if k in current and not _same(current[k]["fields"], f)]
    for start in range(0, len(updates), 500):
        fc._req(db, "POST", base + "/batch_update", json={"records": updates[start:start + 500]})
    verified = _index(fc.list_records(db, token, table))
    if any(k not in verified or not _same(verified[k]["fields"], f) for k, f in expected.items()):
        raise RuntimeError("紧急订单表回读不一致，暂不发群通知")
    url = f"https://feishu.cn/base/{token}?table={table}"
    state.update(last_date=day, last_count=len(rows), url=url)
    _save(db, state)
    notice_key = f"urgent_fulfillment_notice:{day}:{table}:{chat}"
    notice = json.loads(settings.get(db, notice_key, env_fallback=False) or "{}")
    if not notice:
        notice = {"status": "sending", "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, notice_key))}
        settings.set_value(db, notice_key, json.dumps(notice))
        db.commit()
        message = f"紧急订单清单 · {day}\n待跟进 {len(rows)} 单。请在表内查看，后续更新以表格为准。\n{url}"
        response = fc._req(db, "POST", fc._BASE + "/im/v1/messages?receive_id_type=chat_id",
                           json={"receive_id": chat, "msg_type": "text", "uuid": notice["uuid"],
                                 "content": json.dumps({"text": message}, ensure_ascii=False)})
        if not response.get("message_id"):
            raise RuntimeError("紧急订单表已更新，群消息送达未知，不重复发送")
        notice.update(status="sent", message_id=response["message_id"])
        settings.set_value(db, notice_key, json.dumps(notice))
        db.commit()
    elif notice.get("status") != "sent":
        raise RuntimeError("紧急订单表已更新，原群消息送达待核对，不重复发送")
    return {"status": "verified", "orders": len(rows), "date": day, "url": url,
            "message_id": notice["message_id"], "table_id": table}
