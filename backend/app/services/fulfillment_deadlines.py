"""Internal order deadlines and a single ERP reminder for the shipping desk."""
from datetime import date, datetime, timedelta, timezone
import re

from sqlalchemy import select

from app.models.order import Order, OrderDetail
from app.services import alert_service, order_flags, order_service
from app.services.order_line_delivery_service import line_is_fully_refunded, is_master_summary_line
from app.services.order_sheet_archive_service import _is_refunded

_BEIJING = timezone(timedelta(hours=8))
_FIELDS = ("remark", "seller_memo", "buyer_message", "production_note")
_DIGITS = "零一二三四五六七八九"
_DURATION = re.compile(
    r"(?:要求|约定|承诺|请|必须|最迟|最晚|务必|需要|要|在)*"
    r"(?P<anchor>下单|付款|确认图纸|确认尺寸|开工)?(?:当天起|之日起|起算|后|起)?"
    r"(?P<days>[1-9]\d?|[一二两三四五六七八九十]{1,3})(?:个)?(?:自然)?天"
    r"(?:以内|内|之内)?(?:必须|要|需|安排|全部)?"
    r"(?P<kind>制作完成|生产完成|做完|做好|完成|发出|发货|送到|到货|送达)[！!。]?"
)
_HOLD = re.compile(r"(?:天|日|号|月)(?:以|之)?后(?:再)?发|(?:不早于|先别|等通知)")
_URGENT = re.compile(r"(?:^|[，,。；;！!\s])(?:请|必须|要|需要|务必|尽量)?(?:加急|尽早发|早点加急|赶工)")
_QUESTION = re.compile(r"能否|能不能|可否|是否|可以吗|行吗|[?？]|取消.{0,8}(?:工期|加急|发货要求)|(?:不用|无需|不必|不再)(?:加急|赶工)")
_LABELS = {"production": "完成制作", "shipping": "发货", "arrival": "送达"}


def _number(token):
    if token.isascii():
        return int(token)
    token = token.replace("两", "二")
    if token in _DIGITS:
        return _DIGITS.index(token)
    left, sep, right = token.partition("十")
    if sep and left in list(_DIGITS) + [""] and right in list(_DIGITS) + [""]:
        return (_DIGITS.index(left) if left else 1) * 10 + (_DIGITS.index(right) if right else 0)
    return 0


def deadline(order, today):
    """Return one internal action and its source; never update a platform promise."""
    schedule = order_flags.factory_schedule(order, today=today)
    base = schedule["effective_deadline"]
    notes = [getattr(order, field, "") or "" for field in _FIELDS]
    text = " ".join(notes)
    if (schedule["remote_resume_date"] and not order_flags.has_shipping_delay(order)
            and re.search(r"[0-9]+\s*天|[?？]|不是|不能|无需|不用", text)):
        # The legacy board also interprets “N天发” as an appointment. Its date
        # cannot supply the starting date for a payment/approval-based promise.
        base = schedule["original_deadline"]
    if (schedule["urgency"] == "remote" or order_flags.waits_for_shipping_notice(order)
            or (order_flags.has_shipping_delay(order) and not order_flags.is_customer_delay_activated(order))
            or (_HOLD.search(text) and (not base or base > today))):
        return None
    targets = set()
    review = set()
    for note in notes:
        for clause in re.split(r"[，,。；;|\n]", note):
            clause = "".join(clause.split())
            match = _DURATION.fullmatch(clause)
            if not match:
                if re.fullmatch(r"(?:工期|交期|生产周期|制作周期)?(?:约|为|预计)?"
                                r"[0-9一二两三四五六七八九十]+(?:个工作日|个?天)(?:内|左右)?", clause):
                    review.add("确认备注天数的起点及完成事项")
                elif re.search(r"[0-9一二两三四五六七八九十]+(?:个工作日|个?天).{0,8}(?:做完|完成|发货|发出|到货|送达)", clause):
                    review.add("核实备注工期")
                continue
            action = match["kind"]
            kind = "shipping" if action in ("发货", "发出") else "arrival" if action in ("送达", "到货", "送到") else "production"
            days = _number(match["days"])
            if kind == "arrival":
                review.add("客户约定送达，需核实运输时间")
            elif match["anchor"] != "下单" or not order.order_date or not days:
                review.add("确认" + _LABELS[kind] + "的起算日期")
            else:
                targets.add((order.order_date + timedelta(days=days), kind))
    if len(targets) > 1 or (targets and _QUESTION.search(text)):
        review.add("核实备注中不同的交期要求")
    due, action = base, "发货"
    if (len(targets) == 1 and not review and not order_flags.has_shipping_delay(order)
            and not getattr(order, "ship_deadline", None)):
        candidate, kind = next(iter(targets))
        if not due or candidate <= due:
            due, action = candidate, _LABELS[kind]
    elif targets and base and any(target != base for target, _ in targets):
        review.add("核实备注与已确认交期")
    urgent_word = bool(_URGENT.search(text)) and not _QUESTION.search(text)
    remaining = (due - today).days if due else None
    shortened = bool(targets and due and base and due < base)
    if (remaining is None or remaining > 11) and not review and not urgent_word and not shortened:
        return None
    return {"due": due.isoformat() if due else None, "days": remaining,
            "action": "核实交期" if review else action,
            "reason": "；".join(sorted(review)) or (
                "按下单时约定的工期" if targets and due != base else
                "备注要求加急" if urgent_word else "已到提醒期限"),
            "original_due": base.isoformat() if base else None}


def pending_orders(db, today=None):
    today = today or datetime.now(_BEIJING).date()
    # Match legacy Chinese paid status as well as canonical rows.
    paid = {raw for raw in order_service._STATUS_ALIASES if order_service.normalize_status(raw) == "paid"}
    orders = list(db.scalars(select(Order).where(Order.status.in_(paid | {"paid", "production"}))))
    if not orders:
        return []
    lines = {}
    for line in db.scalars(select(OrderDetail).where(OrderDetail.source == "import",
            OrderDetail.order_no.in_([o.order_no for o in orders]),
            OrderDetail.sub_order_no.isnot(None), OrderDetail.sub_order_no != "")):
        lines.setdefault(line.order_no, []).append(line)
    rows = []
    for order in orders:
        if (order.ship_date or order.is_refill or _is_refunded(order, db=db)
                or (order.platform_field_state or {}).get("shipping_only")):
            continue
        active = [line for line in lines.get(order.order_no, ())
                  if not is_master_summary_line(db, line) and not line_is_fully_refunded(line, db)
                  and order_service.normalize_status(line.line_status) in ("paid", "production")]
        if order.order_no in lines and not active:
            continue
        info = deadline(order, today)
        if info:
            numbers = sorted({line.factory_no for line in active if line.factory_no})
            label = "、".join(f"畔色{n}单" for n in numbers) or order_flags.factory_label(order) or order.order_no
            rows.append({**info, "order_no": order.order_no, "label": label,
                         "sub_order_nos": sorted({line.sub_order_no for line in active}),
                         "refund_note": "存在退款记录，跟进时核实退款范围" if order.refund_amount or any(line.refund_amount for line in active) else ""})
    return sorted(rows, key=lambda row: (row["due"] or "9999", row["order_no"]))


def refresh_reminder(db):
    key = "fulfillment_internal_deadlines"
    rows = pending_orders(db)
    if not rows:
        alert_service.resolve_by_dedupe(db, key)
    elif alert_service.get_active_context(db, key) != {"rows": rows}:
        preview = [f"{r['label']} · {r['due'] or '日期待核'} {r['action']} · {r['reason']}" for r in rows[:4]]
        alert_service.upsert(db, kind=key, dedupe_key=key, severity="warn",
            title="紧急订单待跟进", body=f"共 {len(rows)} 单\n" + "\n".join(preview),
            related_url="/orders/kanban?view=urgent", context={"rows": rows}, push_notify=False)
    from app.services import urgent_fulfillment_feishu
    return {"orders": len(rows), "feishu": urgent_fulfillment_feishu.sync_daily(db, rows)}
