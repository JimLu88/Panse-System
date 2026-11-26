"""Daily urgent-order snapshots in a separate table of the existing factory base.

Only this table is written. Unknown creates are reconciled by key, never replayed.
"""
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import threading
import uuid

from sqlalchemy import text

from app.services import feishu_client as fc, settings_service as settings
from app.services import factory_dispatch_feishu_service as factory, notify_service
from app.services.urgent_note_highlights import note_signal

ENABLED_KEY = "urgent_fulfillment_feishu_enabled"
STATE_KEY = "urgent_fulfillment_feishu_v1"
TABLE_NAME = "紧急订单（每日）"
FIELDS = [(n, 1) for n in (
    "工厂下单号", "统计日期", "紧急程度", "商品名称", "SKU规格", "目标日期",
    "跟进事项", "提醒原因", "清单状态", "订单号", "子订单号", "记录键", "更新时间",
    "退款提示", "备注时间要求",
)] + [("剩余天数", 2), ("工厂下单图", 17), ("备注提醒", 3)]
# Bitable native colored tags, matching the factory table's established palette.
COLOR_RED, COLOR_YELLOW, COLOR_NEUTRAL = 44, 24, 10
TAG_FIELDS = {"紧急程度", "目标日期", "备注提醒"}
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
    # Only the explicitly owned urgent table may receive this additive field.
    # A timed-out creation is read back next time, never blindly repeated.
    if "工厂下单图" not in fields and state.get("table_id") == table:
        if state.get("image_field_pending"):
            raise RuntimeError("紧急表图片列创建结果待核对，不重复创建")
        state["image_field_pending"] = True
        _save(db, state)
        fc.create_field(db, token, table, "工厂下单图", 17)
        fields = {f["field_name"]: f["type"] for f in fc.list_table_fields(db, token, table)}
    for name, kind in [("备注时间要求", 1), ("备注提醒", 3)]:
        if name not in fields and state.get("table_id") == table:
            pending_key = "field_pending:" + name
            if state.get(pending_key):
                raise RuntimeError("紧急表备注列创建结果待核对，不重复创建")
            state[pending_key] = True
            _save(db, state)
            fc.create_field(db, token, table, name, kind)
            fields = {f["field_name"]: f["type"] for f in fc.list_table_fields(db, token, table)}
        if fields.get(name) == kind:
            state.pop("field_pending:" + name, None)
    if any(fields.get(n) not in ({1, 3} if n in TAG_FIELDS else {t}) for n, t in FIELDS):
        raise RuntimeError("紧急订单表字段不匹配，不覆盖已有表结构")
    state.pop("image_field_pending", None)
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
        note_level, note_excerpt = note_signal(
            [row.get("note_excerpt", ""), joined("订单备注")], date.fromisoformat(day))
        if row.get("note_level") == "red":
            note_level = "red"
        if note_level == "red" and urgency not in ("逾期", "交期待核实"):
            urgency = "非常紧急"
        elif note_level and (days is None or days > 11) and urgency != "交期待核实":
            urgency = "时间要求待核实"
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
            "备注提醒": ("非常紧急 / 请优先核实" if note_level == "red" else
                         "有时间要求 / 请核实" if note_level else ""),
            "备注时间要求": (("🔴 " if note_level == "red" else "🟡 ") + note_excerpt
                            if note_excerpt else ""),
        }
        attachments = _attachments([image for original in originals
                                    for image in factory._remote_attachment_value(original)])
        if attachments:
            result[key]["工厂下单图"] = attachments
    return result


def _attachments(value):
    return list({item["file_token"]: {"file_token": item["file_token"]}
                 for item in (value or [])
                 if isinstance(item, dict) and item.get("file_token")}.values())


def _index(records):
    result = {}
    for r in records:
        key = _text(r["fields"].get("记录键"))
        if not key or key in result:
            raise RuntimeError("紧急订单表存在空白或重复记录键，请核对")
        result[key] = r
    return result


def _same(actual, expected):
    return all(({x["file_token"] for x in _attachments(actual.get(k))} ==
                {x["file_token"] for x in _attachments(v)} if k == "工厂下单图" else
                _number_equal(actual.get(k), v) if k == "剩余天数" else _text(actual.get(k)) == _text(v))
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


def _tag_color(name, value, today):
    if name == "紧急程度":
        return COLOR_RED if value in ("逾期", "非常紧急") else COLOR_YELLOW
    if name == "备注提醒":
        return COLOR_RED if value.startswith("非常紧急") else COLOR_YELLOW
    try:
        days = (date.fromisoformat(value) - today).days
    except ValueError:
        return COLOR_YELLOW
    return COLOR_RED if days <= 5 else COLOR_YELLOW if days <= 11 else COLOR_NEUTRAL


def _ensure_colors(db, token, table, current, expected, day):
    """Only owned presentation fields; preserve option IDs and every old value."""
    fields = {f["field_name"]: f for f in fc.list_table_fields(db, token, table)}
    converted = []
    for name in sorted(TAG_FIELDS):
        field = fields[name]
        if field["type"] not in (1, 3):
            raise RuntimeError("紧急表颜色字段类型冲突：" + name)
        options = [dict(o) for o in (field.get("property") or {}).get("options", [])]
        by_name = {o["name"]: o for o in options}
        values = {_text(r["fields"].get(name)) for r in current.values()}
        values.update(_text(f.get(name)) for f in expected.values())
        for value in sorted(values - {""}):
            if value not in by_name:
                option = {"name": value}
                options.append(option)
                by_name[value] = option
        for option in options:
            option["color"] = _tag_color(name, option["name"], date.fromisoformat(day))
        if field["type"] == 3 and options == (field.get("property") or {}).get("options", []):
            continue
        prop = dict(field.get("property") or {}) if field["type"] == 3 else {}
        prop["options"] = options
        fc.update_field(db, token, table, field["field_id"], field_name=name,
                        field_type=3, property_=prop, ui_type="SingleSelect")
        verified = next(f for f in fc.list_table_fields(db, token, table) if f["field_name"] == name)
        actual = {o["name"]: o.get("color") for o in (verified.get("property") or {}).get("options", [])}
        if verified["type"] != 3 or any(actual.get(o["name"]) != o["color"] for o in options):
            raise RuntimeError("紧急表颜色设置回读不一致：" + name)
        if field["type"] == 1:
            converted.append(name)
    if converted:
        readback = _index(fc.list_records(db, token, table))
        if set(readback) != set(current) or any(
            not _same(readback[k]["fields"], {n: r["fields"].get(n) for n in converted})
            for k, r in current.items()
        ):
            raise RuntimeError("紧急表文字转颜色标签后值不一致，停止同步")


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
    _ensure_colors(db, token, table, current, expected, day)
    for key, fields in expected.items():
        if key in current and fields.get("工厂下单图"):
            fields["工厂下单图"] = _attachments(
                factory._remote_attachment_value(current[key]["fields"]) + fields["工厂下单图"])
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
