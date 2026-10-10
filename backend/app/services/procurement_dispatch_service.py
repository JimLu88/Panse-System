"""Database dispatch protocol, exposed through default-disabled machine routes.

Caller owns commit/rollback. No permission exists until the permit transaction
commits; lost responses must be looked up by the same stable action key. This
module never drives a browser, reads credentials, sleeps or retries a send.
The local coordinator has no validated real platform adapter yet.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.procurement import ProcurementInquiry, ProcurementMessage, ProcurementTask
from app.models.procurement_dispatch import (
    ProcurementAccountBinding as Account, ProcurementTaskAccount as Binding,
    ProcurementContactClaim as Contact, ProcurementSendIntent as Intent,
    ProcurementInputGroup as InputGroup,
)
from app.services import procurement_batch_service as batches

PROTOCOL = "dispatch-v1"
UNCERTAIN = {"executing", "unknown", "reconciling", "unresolved"}
BEIJING = timezone(timedelta(hours=8))


class DispatchBlocked(ValueError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now(value=None):
    return batches.utc(value or batches.now_utc())


def register_account(db: Session, *, channel: str, identity_hash: str, executor_id: str,
                     input_group: str, new_24h=25, new_1h=6, messages_24h=40) -> Account:
    """Provision a PAUSED binding; never silently replaces an existing owner."""
    if channel not in {"taobao", "1688", "xiaohongshu"} or not re.fullmatch(r"[a-f0-9]{64}", identity_hash):
        raise DispatchBlocked("需要首版平台和稳定账号摘要，不能使用手机号或显示名代替")
    if any(not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", v) for v in (executor_id, input_group)):
        raise DispatchBlocked("执行器和输入锁组标识无效")
    if any(type(v) is not int or not 1 <= v <= cap for v, cap in ((new_24h, 25), (new_1h, 6), (messages_24h, 40))):
        raise DispatchBlocked("工程预算只能下调，不能自动扩大")
    if not db.get(InputGroup, (executor_id, input_group)):
        db.add(InputGroup(executor_id=executor_id, input_group=input_group))
        db.flush()
    row = Account(id=str(uuid4()), channel=channel, identity_hash=identity_hash,
                  executor_id=executor_id, input_group=input_group, status="paused",
                  new_24h=new_24h, new_1h=new_1h, messages_24h=messages_24h)
    db.add(row)
    db.flush()
    return row


def _lock_account(db, account_id):
    # Real UPDATE obtains a database write lock on SQLite too. Lock ordering is
    # input group, account, then task. Errors require rollback. This database
    # fence does NOT replace the mandatory local OS/UI lock.
    account = db.get(Account, account_id)
    if not account:
        raise DispatchBlocked("账号绑定不存在")
    group_locked = db.execute(update(InputGroup).where(
        InputGroup.executor_id == account.executor_id, InputGroup.input_group == account.input_group,
    ).values(revision=InputGroup.revision + 1), execution_options={"synchronize_session": False}).rowcount
    if group_locked != 1:
        raise DispatchBlocked("共享输入组未登记")
    changed = db.execute(update(Account).where(Account.id == account_id).values(
        revision=Account.revision + 1), execution_options={"synchronize_session": False}).rowcount
    if changed != 1:
        raise DispatchBlocked("账号绑定不存在")
    return db.scalars(select(Account).where(Account.id == account_id)
                      .execution_options(populate_existing=True)).one()


def bind_task(db, *, task_id, account_id):
    account = _lock_account(db, account_id)
    task = db.get(ProcurementTask, task_id)
    if task is None:
        raise DispatchBlocked("批次不存在")
    task = batches.lock_task(db, task)
    batches.require_draft(task)
    if not batches.bounded(task) or account.channel not in task.channels:
        raise DispatchBlocked("只能绑定新批次已选择的渠道")
    row = db.get(Binding, (task_id, account.channel))
    if row and row.account_id != account_id:
        raise DispatchBlocked("已存在不同绑定，不能静默换号")
    if not row:
        db.add(Binding(task_id=task_id, channel=account.channel, account_id=account_id))
        db.flush()


def snapshot_bindings(db, task_id):
    rows = db.execute(select(Binding, Account).join(Account, Account.id == Binding.account_id)
                      .where(Binding.task_id == task_id).order_by(Binding.channel)).all()
    return [{"account_id": a.id, "channel": b.channel, "identity_hash": a.identity_hash,
             "executor_id": a.executor_id, "input_group": a.input_group,
             "new_24h": a.new_24h, "new_1h": a.new_1h, "messages_24h": a.messages_24h}
            for b, a in rows]


def _context(db, account_id, task_id, executor_id):
    account = _lock_account(db, account_id)
    task = db.get(ProcurementTask, task_id)
    if task is None:
        raise DispatchBlocked("批次不存在")
    task = batches.lock_task(db, task)
    bound = next((b for b in (task.policy_snapshot or {}).get("account_bindings", [])
                  if b["account_id"] == account_id), None)
    if not bound or executor_id != account.executor_id or any(bound.get(k) != getattr(account, k) for k in (
        "channel", "identity_hash", "executor_id", "input_group")):
        raise DispatchBlocked("账号、执行电脑或冻结身份不匹配")
    return account, task, bound


def _window(account, task, now):
    if account.status != "ready" or not batches.window_open(task, now=now):
        raise DispatchBlocked("账号暂停或批次不在执行窗口")
    if not 9 <= now.astimezone(BEIJING).hour < 20:
        raise DispatchBlocked("20:00—09:00 安静时段，不主动发送")
    if now >= batches.utc(task.deadline_at) - timedelta(seconds=5):
        raise DispatchBlocked("已到截止前停止发起边界")


def _recover(db, account_id, now):
    rows = db.scalars(select(Intent).where(Intent.account_id == account_id,
                      Intent.state.in_(("reserved", "executing", "reconciling")))).all()
    for row in rows:
        if row.state == "reserved" and batches.utc(row.reserved_until) <= now:
            row.state = "cancelled"  # No permit/attempt was ever issued.
        elif row.state == "executing" and batches.utc(row.permit_until) <= now:
            row.state = "unknown"  # Expiry is not proof that the click failed.
        elif row.state == "reconciling" and batches.utc(row.reconcile_started_at) + timedelta(seconds=30) <= now:
            row.state = "unresolved"  # A crash consumes the one lookup opportunity.
    db.flush()


def _usage(db, account_id, now):
    rows = db.execute(select(Intent, Contact).join(Contact, Contact.id == Intent.contact_id)
                      .where(Intent.account_id == account_id)).all()
    recent, hour, messages = set(), set(), 0
    for intent, contact in rows:
        charged = intent.attempt_at
        if not charged and intent.state == "reserved" and batches.utc(intent.reserved_until) > now:
            charged = intent.reserved_at
        if not charged:
            continue
        age = now - batches.utc(charged)
        if age < timedelta(0):
            raise DispatchBlocked("服务器时间回退，停止新许可并检查时钟")
        if age < timedelta(hours=24):
            messages += 1
            if intent.action_key == "initial:0":
                recent.add(contact.merchant_hash)
                if age < timedelta(hours=1):
                    hour.add(contact.merchant_hash)
    return {"new_24h": recent, "new_1h": hour, "messages_24h": messages}


def budget_status(db, account_id, *, now=None):
    now = _now(now)
    account = db.get(Account, account_id)
    if not account:
        raise DispatchBlocked("账号绑定不存在")
    usage = _usage(db, account_id, now)
    return {"account_id": account_id, "as_of": now.isoformat(),
            "used": {k: len(v) if isinstance(v, set) else v for k, v in usage.items()},
            "limits": {k: getattr(account, k) for k in usage},
            "engineering_policy_only": True, "external_send_enabled": False}


def task_dispatch_status(db, task_id, *, now=None):
    """Read-only, bounded diagnostics; never returns content or permit secrets."""
    now = _now(now)
    bindings = db.scalars(select(Binding).where(Binding.task_id == task_id)).all()
    intents = db.scalars(select(Intent).where(Intent.task_id == task_id).order_by(Intent.reserved_at)).all()
    states = {}
    for row in intents:
        states[row.state] = states.get(row.state, 0) + 1
    contacts = set(row.contact_id for row in intents if row.attempt_at or (
        row.state == "reserved" and batches.utc(row.reserved_until) > now))
    return {"task_id": task_id, "protocol": PROTOCOL, "as_of": now.isoformat(),
            "external_send_enabled": False, "occupied_merchants": len(contacts), "intent_states": states,
            "budgets": [budget_status(db, b.account_id, now=now) for b in bindings],
            "pending": [{"intent_id": i.id, "inquiry_id": i.inquiry_id, "state": i.state,
                         "reconcile_attempts": i.reconcile_attempts} for i in intents if i.state in UNCERTAIN],
            "limitations": ["只提供数据库协议和状态，不连接真实发送", "跨平台同一供应商实体合并尚待核验", "工程预算不代表平台官方额度"]}


def _check_budget(db, account, bound, now, *, merchant_hash=None, add_message=False):
    usage = _usage(db, account.id, now)
    for key, value in usage.items():
        if isinstance(value, set):
            total = len(value | {merchant_hash}) if merchant_hash else len(value)
        else:
            total = value + int(add_message)
        if total > min(getattr(account, key), bound[key]):
            raise DispatchBlocked("同账号跨任务滚动额度已满：" + key)


def _require_clear(db, account, excluding=None):
    query = select(Intent.id).join(Account, Account.id == Intent.account_id).where(
        Account.executor_id == account.executor_id, Account.input_group == account.input_group,
        Intent.state.in_(UNCERTAIN))
    if excluding:
        query = query.where(Intent.id != excluding)
    if db.scalar(query.limit(1)):
        raise DispatchBlocked("账号存在执行中或未决发送，不能另发或换商家")


def _eligible(inquiry, task, now):
    if inquiry.status not in {"ready", "followup_ready", "waiting_reply"}:
        raise DispatchBlocked("该商家当前没有待发动作")
    if inquiry.first_sent_at:
        if inquiry.followup_round >= min(task.max_followup_rounds, 2):
            raise DispatchBlocked("已到本批追问上限")
        if not inquiry.first_response_at and (
            inquiry.followup_round >= 1 or now < batches.utc(inquiry.first_sent_at) + timedelta(hours=24)
            or now >= batches.utc(task.deadline_at) - timedelta(hours=6)
        ):
            raise DispatchBlocked("无回复仅在首问24小时后、截止前6小时外跟进一次")


def reserve(db, *, task_id, inquiry_id, account_id, executor_id, action_key, content, now=None):
    now = _now(now)
    account, task, bound = _context(db, account_id, task_id, executor_id)
    existing = db.scalar(select(Intent).where(Intent.task_id == task_id,
        Intent.inquiry_id == inquiry_id, Intent.action_key == action_key))
    content = content.strip()
    payload_hash = digest(content)
    if existing:
        if existing.account_id != account_id or existing.payload_hash != payload_hash:
            raise DispatchBlocked("稳定意图已存在，不能更改收件账号或内容")
        return existing  # Includes terminal/expired states; NEVER rearms an intent.
    _window(account, task, now)
    _recover(db, account_id, now)
    _require_clear(db, account)
    inquiry = db.get(ProcurementInquiry, inquiry_id)
    if not inquiry or inquiry.task_id != task_id or inquiry.channel != account.channel or not (inquiry.merchant_external_id or "").strip():
        raise DispatchBlocked("需要本批精确平台商家身份，显示名或商品链接不够")
    _eligible(inquiry, task, now)
    from app.services.procurement_service import _action_key, _reviewed_action_content, initial_message, followup_message
    suggested = initial_message(task, inquiry) if inquiry.first_sent_at is None else followup_message(task, inquiry)
    approved, reviewed = _reviewed_action_content(task, inquiry, suggested=suggested)
    if not reviewed or action_key != _action_key(inquiry) or content != approved or not content:
        raise DispatchBlocked("发送内容或轮次未与当前明确确认稿绑定")
    if inquiry.first_sent_at and inquiry.followup_round >= task.max_followup_rounds:
        raise DispatchBlocked("已到追问上限")
    merchant_hash = digest(account.channel + ":" + inquiry.merchant_external_id.strip())
    contact = db.scalar(select(Contact).where(Contact.inquiry_id == inquiry_id))
    if contact and (contact.merchant_hash != merchant_hash or contact.account_id != account_id):
        raise DispatchBlocked("已占用商家身份不能替换")
    _check_budget(db, account, bound, now, merchant_hash=merchant_hash if action_key == "initial:0" else None, add_message=True)
    occupied = set(db.scalars(select(Intent.contact_id).where(Intent.task_id == task_id, Intent.state != "cancelled")))
    occupied.update(db.scalars(select(Contact.id).where(Contact.task_id == task_id, Contact.attempted_at.is_not(None))))
    if (not contact or contact.id not in occupied) and len(occupied) >= task.planned_merchant_count:
        raise DispatchBlocked("本批商家名额已满，未知发送仍占名额")
    if not contact:
        contact = Contact(id=str(uuid4()), task_id=task_id, inquiry_id=inquiry_id, account_id=account_id, merchant_hash=merchant_hash)
        db.add(contact)
        db.flush()
    row = Intent(id=str(uuid4()), task_id=task_id, inquiry_id=inquiry_id, account_id=account_id,
                 contact_id=contact.id, action_key=action_key, content=content, payload_hash=payload_hash,
                 state="reserved", reserved_at=now, reserved_until=min(now + timedelta(seconds=60), batches.utc(task.deadline_at)))
    db.add(row)
    db.flush()
    return row


def _intent_context(db, intent_id, executor_id):
    row = db.get(Intent, intent_id)
    if not row:
        raise DispatchBlocked("发送意图不存在")
    account, task, bound = _context(db, row.account_id, row.task_id, executor_id)
    db.refresh(row)
    return row, account, task, bound


def intent_envelope(db, row):
    """Bind the local target to server-owned inquiry/account identities.

    A caller-provided merchant ID is NOT a sending authority. Return identities
    only through the authenticated machine reserve response, never diagnostics.
    """
    account = db.get(Account, row.account_id)
    inquiry = db.get(ProcurementInquiry, row.inquiry_id)
    task = db.get(ProcurementTask, row.task_id)
    contact = db.get(Contact, row.contact_id)
    if digest(account.channel + ":" + (inquiry.merchant_external_id or "").strip()) != contact.merchant_hash:
        raise DispatchBlocked("原商家身份已变化，禁止生成本机发送信封")
    return {"intent_id": row.id, "state": row.state, "payload_hash": row.payload_hash,
            "task_id": row.task_id, "inquiry_id": row.inquiry_id, "action_key": row.action_key,
            "account_id": account.id, "executor_id": account.executor_id,
            "input_group": account.input_group, "account_identity_hash": account.identity_hash,
            "channel": account.channel, "merchant_external_id": inquiry.merchant_external_id,
            "deadline_at": batches.utc(task.deadline_at).isoformat()}


def issue_permit(db, *, intent_id, executor_id, now=None):
    now = _now(now)
    row, account, task, bound = _intent_context(db, intent_id, executor_id)
    _window(account, task, now)
    _recover(db, account.id, now)
    if row.state != "reserved":
        raise DispatchBlocked("该意图不能再次获得发送许可")
    _require_clear(db, account, excluding=row.id)
    _check_budget(db, account, bound, now)
    inquiry = db.get(ProcurementInquiry, row.inquiry_id)
    _eligible(inquiry, task, now)
    from app.services.procurement_service import _action_key
    contact = db.get(Contact, row.contact_id)
    if (inquiry.approved_action_key != row.action_key or _action_key(inquiry) != row.action_key
            or digest(inquiry.approved_message or "") != row.payload_hash
            or digest(account.channel + ":" + (inquiry.merchant_external_id or "").strip()) != contact.merchant_hash):
        raise DispatchBlocked("确认稿或商家身份已变化，不能发放许可")
    token = secrets.token_urlsafe(32)
    row.state, row.attempt_at, row.permit_hash = "executing", now, digest(token)
    row.permit_until = min(now + timedelta(seconds=5), batches.utc(task.deadline_at) - timedelta(seconds=5))
    contact.attempted_at = contact.attempted_at or now
    db.flush()
    return {"intent_id": row.id, "permit": token, "server_time": now.isoformat(),
            "expires_at": batches.utc(row.permit_until).isoformat(), "payload_hash": row.payload_hash,
            "protocol": PROTOCOL, "requires_local_durable_fence": True}


def mark_unknown(db, *, intent_id, executor_id):
    row, _, _, _ = _intent_context(db, intent_id, executor_id)
    if row.state == "executing":
        row.state = "unknown"
    elif row.state not in {"unknown", "reconciling", "unresolved", "confirmed_sent"}:
        raise DispatchBlocked("尚未尝试的意图不能伪装成已发送未知")
    db.flush()
    return row


def begin_reconcile(db, *, intent_id, executor_id, now=None):
    now = _now(now)
    row, account, _, _ = _intent_context(db, intent_id, executor_id)
    _recover(db, account.id, now)
    if row.state != "unknown" or row.reconcile_attempts != 0:
        raise DispatchBlocked("原意图只允许一次只读核对，不能重新占用")
    row.state, row.reconcile_attempts, row.reconcile_started_at = "reconciling", 1, now
    db.flush()
    return row  # Caller MUST commit before invoking any read-only adapter.


def finish_unresolved(db, *, intent_id, executor_id):
    row, _, _, _ = _intent_context(db, intent_id, executor_id)
    if row.state == "reconciling":
        row.state = "unresolved"
    elif row.state != "unresolved":
        raise DispatchBlocked("未占用核对机会或已有明确结果")
    db.flush()
    return row


def confirm_sent(db, *, intent_id, executor_id, permit, payload_hash, external_message_id, observed_at, now=None):
    now = _now(now)
    row, _, task, _ = _intent_context(db, intent_id, executor_id)
    if not isinstance(permit, str) or not row.permit_hash or not hmac.compare_digest(row.permit_hash, digest(permit)) or row.payload_hash != payload_hash:
        raise DispatchBlocked("许可或回读内容与原发送意图不匹配")
    if not isinstance(external_message_id, str) or not external_message_id.strip() or external_message_id.lower().startswith(("manual-review-", "human-review-")) or len(external_message_id) > 255:
        raise DispatchBlocked("需要适配器取得的真实平台消息标识，人工按钮不是回执")
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None:
        raise DispatchBlocked("回读缺少带时区的实际观察时间，不能补造")
    observed_at = _now(observed_at)
    if observed_at < batches.utc(row.attempt_at) or observed_at > now:
        raise DispatchBlocked("回读时点不在执行尝试与当前时点之间")
    if row.state == "confirmed_sent":
        if row.external_message_id != external_message_id:
            raise DispatchBlocked("同一意图不能覆盖成另一平台消息")
        return row
    if row.state not in UNCERTAIN:
        raise DispatchBlocked("未尝试或已取消的意图不能确认发送")
    if batches.is_supplement(task, now=now):
        batches.close_batch(db, task, now=now)
    row.state, row.external_message_id, row.observed_at, row.confirmed_at = "confirmed_sent", external_message_id, observed_at, now
    # Project immutable evidence, not a new send and not a renewed authorization.
    late = batches.is_supplement(task, now=now)
    message = ProcurementMessage(inquiry_id=row.inquiry_id, direction="outbound", content=row.content,
        event_at=observed_at, external_message_id=external_message_id,
        message_meta={"intent_id": row.id, "confirmed_sent": True, "late_supplement": late,
                      "server_recorded_at": now.isoformat(), "authorization_kind": "human_reviewed"})
    db.add(message)
    if not late:
        inquiry = db.get(ProcurementInquiry, row.inquiry_id)
        inquiry.first_sent_at = inquiry.first_sent_at or row.attempt_at
        inquiry.last_outbound_message, inquiry.last_message_at = row.content, observed_at
        inquiry.followup_round = int(row.action_key.split(":")[1])
        # Do not overwrite an inbound response that arrived before its outbound callback.
        if not inquiry.first_response_at:
            inquiry.status = "waiting_reply"
            inquiry.next_followup_at = batches.utc(row.attempt_at) + timedelta(hours=24)
        if inquiry.approved_action_key == row.action_key:
            inquiry.approved_message = inquiry.approved_message_base = inquiry.approved_action_key = None
            inquiry.message_reviewed_at = inquiry.message_reviewed_by = None
    db.flush()
    return row


def recover_account(db, *, account_id, now=None):
    _lock_account(db, account_id)
    _recover(db, account_id, _now(now))


def cancel_reserved(db, *, intent_id, executor_id):
    row, _, _, _ = _intent_context(db, intent_id, executor_id)
    if row.state != "reserved" or row.attempt_at is not None:
        raise DispatchBlocked("只有从未发放许可的预留可以取消释放")
    row.state = "cancelled"
    db.flush()
    return row
