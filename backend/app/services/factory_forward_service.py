"""Human acknowledgement only. Never sends orders, changes quantities or production status."""
from __future__ import annotations
import logging
import re
import threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from app.models.order import Order, OrderDetail
from app.models.factory_forward import FactoryForward
from app.services import feishu_client

log = logging.getLogger(__name__)
LABELS = {'pending': '待转发', 'forwarded': '已转发', 'deferred': '暂缓转发'}
_sync_lock = threading.Lock()


def key_for(order_id, line_id=None):
    return f'line:{line_id}' if line_id else f'order:{order_id}'


def projection(db, order_id, line_id=None):
    key = key_for(order_id, line_id)
    row = db.get(FactoryForward, key)
    return {'key': key, 'state': row.state if row else 'pending',
            'label': LABELS[row.state] if row else LABELS['pending'],
            'revision': row.revision if row else 0, 'actor': row.actor if row else None,
            'changed_at': row.changed_at.isoformat() if row and row.changed_at else None,
            'history': row.history if row else [], 'sync_pending': row.sync_pending if row else False,
            'sync_error': row.sync_error if row else None}


def table_fields(db, order_id, line_id=None):
    p = projection(db, order_id, line_id)
    return {'微信转发状态': p['label'], '转发登记人': p['actor'] or '',
            '转发登记时间': p['changed_at'] or ''}


def order_targets(db, order):
    from app.services.order_line_delivery_service import line_is_refunded, is_master_summary_line
    lines = db.scalars(select(OrderDetail).where(OrderDetail.order_no == order.order_no,
                                               OrderDetail.source == 'import')).all()
    if lines:
        return [(key_for(order.id, x.id), x) for x in lines
                if x.sub_order_no and (x.factory_delivery_required or x.factory_no)
                and not line_is_refunded(x) and not is_master_summary_line(db, x)]
    return [(key_for(order.id), None)] if order.factory_no else []


def items_for_order(db, order):
    return [{**projection(db, order.id, line.id if line else None),
             'factory_no': line.factory_no if line else order.factory_no,
             'sub_order_no': line.sub_order_no if line else None,
             'product_name': line.product_name if line else order.product_name,
             'order_no': order.order_no,
             'image_sent': bool(line.factory_delivery_message_id) if line else False}
            for _, line in order_targets(db, order)]


def resolve(db, key):
    try:
        kind, raw_id = key.split(':')
        ident = int(raw_id)
        if kind not in ('line', 'order') or ident <= 0:
            raise ValueError()
    except (ValueError, AttributeError):
        raise ValueError('转发对象无效')
    line = db.get(OrderDetail, ident) if kind == 'line' else None
    order = (db.scalar(select(Order).where(Order.order_no == line.order_no).with_for_update())
             if line else db.scalar(select(Order).where(Order.id == ident).with_for_update())
             if kind == 'order' else None)
    if not order or order.status not in ('paid', 'production') or order.is_refill:
        raise ValueError('订单已退出在制范围，不能更改转发登记')
    if key not in {k for k, _ in order_targets(db, order)}:
        raise ValueError('无有效实体子订单，不能更改转发登记')
    return order, line


def change(db, key, state, revision, actor):
    if state not in LABELS or not actor or len(actor) > 160:
        raise ValueError('状态或登记人无效')
    order, line = resolve(db, key)  # parent row serializes creation and concurrent edits
    row = db.get(FactoryForward, key, populate_existing=True)
    current = row.revision if row else 0
    if revision != current:
        raise ValueError('状态已被他人更新，请刷新后再选择')
    if row and row.state == state:
        db.commit()
        return projection(db, order.id, line.id if line else None)
    if not row:
        row = FactoryForward(key=key, order_id=order.id, line_id=line.id if line else None,
                             state='pending', revision=0, history=[], card_state='none')
        db.add(row)
    now = datetime.now(timezone.utc)
    row.history = [*(row.history or []), {'from': row.state, 'to': state,
                   'actor': actor, 'at': now.isoformat(), 'revision': current + 1}]
    row.state, row.revision, row.actor, row.changed_at = state, current + 1, actor, now
    row.sync_pending, row.sync_error, row.sync_attempts = True, None, 0
    db.commit()
    return projection(db, order.id, line.id if line else None)


def card(db, row):
    order = db.get(Order, row.order_id)
    line = db.get(OrderDetail, row.line_id) if row.line_id else None
    no = line.factory_no if line else order.factory_no
    name = (line.product_name if line else order.product_name) or ''
    label = f'畔色{no}单' if no else '待编号'
    return {'config': {'wide_screen_mode': True, 'update_multi': True},
            'header': {'title': {'tag': 'plain_text', 'content': f'{label} · 微信转发登记'}},
            'elements': [{'tag': 'markdown', 'content':
                f'{name}\n订单：{order.order_no}\n子单：{line.sub_order_no if line else "旧母单"}\n'
                f'**{LABELS[row.state]}** · {row.actor or "尚未登记"}\n'
                '仅登记人工转发，不代表微信送达或工厂接单。'},
                {'tag': 'action', 'actions': [
                    {'tag': 'button', 'text': {'tag': 'plain_text', 'content': title},
                     'value': {'op': 'factory_forward', 'key': row.key, 'state': state,
                               'revision': row.revision}}
                    for state, title in [('forwarded', '✅ 已转发'), ('deferred', '❌ 暂缓转发'),
                                         ('pending', '恢复待转发')]]}]}


def attach_card(db, line_id=None, *, order_id=None, image_message_id=None):
    line = db.get(OrderDetail, line_id) if line_id else None
    image_id = line.factory_delivery_message_id if line else image_message_id
    if not image_id:
        return
    order = (db.scalar(select(Order).where(Order.order_no == line.order_no).with_for_update()) if line else
             db.scalar(select(Order).where(Order.id == order_id).with_for_update()))
    key = key_for(order.id, line.id if line else None)
    row = db.get(FactoryForward, key, populate_existing=True)
    if row and row.image_message_id and row.image_message_id != image_id:
        row.history = [*(row.history or []), {'from': row.state, 'to': 'pending',
            'actor': '系统:新下单图需重新确认转发', 'at': datetime.now(timezone.utc).isoformat(),
            'revision': row.revision + 1}]
        row.state, row.revision, row.card_state = 'pending', row.revision + 1, 'none'
        row.actor, row.changed_at = '系统:新下单图需重新确认转发', datetime.now(timezone.utc)
        row.card_message_id, row.sync_pending, row.sync_attempts = None, True, 0
    if row and row.card_state != 'none':
        db.commit()
        return  # sending/unknown is protected; never replay an unknown send
    if not row:
        row = FactoryForward(key=key, order_id=order.id, line_id=line.id if line else None,
                             state='pending', revision=0, history=[], sync_pending=False)
        db.add(row)
    row.image_message_id = image_id
    row.card_state = 'sending'
    db.commit()
    try:
        result = feishu_client.reply_card(db, row.image_message_id, card(db, row)) or {}
        mid = result.get('message_id') or (result.get('message') or {}).get('message_id')
        if not mid:
            raise RuntimeError('missing_card_message_id')
        row.card_message_id, row.card_state = mid, 'sent'
        row.sync_pending = True
        db.commit()
    except Exception:
        db.rollback()
        row = db.get(FactoryForward, key)
        row.card_state = 'unknown'
        db.commit()
        log.exception('转发登记卡结果未知，不重发 line=%s', line_id)


def handle_card(db, event):
    if not event.get('_factory_forward_trusted'):
        raise ValueError('回调来源未验证，未修改')
    value = (event.get('action') or {}).get('value') or {}
    context = event.get('context') or {}
    actor = (event.get('operator') or {}).get('open_id')
    row = db.get(FactoryForward, str(value.get('key') or ''))
    if not actor or not row or not row.card_message_id or row.card_message_id != context.get('open_message_id'):
        raise ValueError('卡片身份不匹配，未修改')
    # Only called from the authenticated Feishu SDK event handler, not a public API.
    return change(db, row.key, value.get('state'), value.get('revision'), '飞书:' + actor)


def sync_pending(db):
    """Retry idempotent projections only, never images/new cards. Coalesce changes."""
    if not _sync_lock.acquire(blocking=False):
        return {'ok': False, 'busy': True}
    try:
        rows = db.scalars(select(FactoryForward).where(FactoryForward.sync_pending.is_(True),
                                                     FactoryForward.sync_attempts < 5)).all()
        if not rows:
            return {'ok': True, 'count': 0}
        versions = {r.key: r.revision for r in rows}
        for row in rows:
            row.sync_attempts += 1
        db.commit()
        try:
            result = sync_table(db, keys=set(versions))
        except Exception:
            db.rollback()
            result = {'ok': False}
        for row in rows:
            try:
                if not result.get('ok'):
                    raise RuntimeError('工厂表同步未完成')
                db.refresh(row)
                version = row.revision
                if row.card_message_id:
                    feishu_client.patch_card(db, row.card_message_id, card(db, row))
                # CAS prevents acknowledging an edit made while network I/O was running.
                from sqlalchemy import update
                db.execute(update(FactoryForward).where(FactoryForward.key == row.key,
                    FactoryForward.revision == versions[row.key], FactoryForward.revision == version)
                    .values(sync_pending=False, sync_error=None))
                db.commit()
            except Exception as exc:
                db.rollback()
                row = db.get(FactoryForward, row.key)
                row.sync_error = type(exc).__name__ + ': 同步待重试'
                db.commit()
        return result
    finally:
        _sync_lock.release()


def sync_table(db, keys=None):
    """Update only the three new columns on existing exact rows; never delete/create orders."""
    from app.services import factory_dispatch_feishu_service as table
    if not table._SYNC_LOCK.acquire(blocking=False):
        return {'ok': False, 'errors': ['factory_sync_busy']}
    try:
        app, tid = table._target(db)
        if (app, tid) != (table.DEFAULT_APP_TOKEN, table.DEFAULT_TABLE_ID):
            return {'ok': False, 'errors': ['factory_target_identity_mismatch']}
        names = ('微信转发状态', '转发登记人', '转发登记时间')
        fields = {x['field_name']: x for x in feishu_client.list_table_fields(db, app, tid)}
        for name in names:
            if name not in fields:
                feishu_client.create_field(db, app, tid, name, 1)
            elif fields[name]['type'] != 1:
                return {'ok': False, 'errors': ['转发字段类型不匹配:' + name]}
        remote = feishu_client.list_records(db, app, tid)
        updates, errors = [], []
        projected = table.build_rows(db)
        found = set()
        for item in projected:
            key = key_for(item['_order_id'], item.get('_line_id'))
            if keys is not None and key not in keys:
                continue
            found.add(key)
            sub = item.get('子订单号')
            candidates = [r for r in remote if (
                table._norm(r['fields'].get('子订单号')) == sub if sub else
                not table._norm(r['fields'].get('子订单号')) and
                table._norm(r['fields'].get('订单号')) == item['订单号'])]
            if len(candidates) != 1:
                errors.append(key + ':工厂表缺行或身份重复，等待原订单同步')
                continue
            fields = {name: item.get(name) or '' for name in names}
            if any(not table._equivalent(candidates[0]['fields'].get(n), v) for n, v in fields.items()):
                updates.append({'record_id': candidates[0]['record_id'], 'fields': fields})
        if keys:
            errors.extend(k + ':未进入工厂表有效范围' for k in keys - found)
        failed = feishu_client.batch_update_records(db, app, tid, updates)
        if failed:
            errors.append('部分转发字段写入失败')
        readback = {r['record_id']: r['fields'] for r in feishu_client.list_records(db, app, tid)}
        for update in updates:
            if any(not table._equivalent(readback.get(update['record_id'], {}).get(n), v)
                   for n, v in update['fields'].items()):
                errors.append('转发字段读回不一致')
        return {'ok': not errors, 'updated': len(updates), 'matched': len(found), 'errors': errors,
                'readback_verified': not errors}
    finally:
        table._SYNC_LOCK.release()


def run_sync():
    from app.database import SessionLocal
    with SessionLocal() as db:
        sync_pending(db)


def query(db, text):
    if not any(w in text for w in ('转发', '没发送', '未发送', '已发送', '已经发送', '没下单', '没有下', '已经下', '已下单', '暂缓下单')):
        return None
    wanted = ('deferred' if '暂缓' in text else 'pending_all' if any(w in text for w in
              ('没转发', '未转发', '没发送', '未发送', '没下单', '没有下')) else
              'forwarded' if any(w in text for w in ('已转发', '已经转发', '已经下', '已下单', '已发送', '已经发送')) else
              'pending' if '待转发' in text else None)
    no = re.search(r'(?:畔色|查)?\s*(\d{1,8})\s*单', text)
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    rows = []
    for order in db.scalars(select(Order).where(Order.status.in_(['paid', 'production']), Order.is_refill.is_(False))):
        for item in items_for_order(db, order):
            if no and item['factory_no'] != int(no[1]):
                continue
            if wanted == 'pending_all' and item['state'] == 'forwarded':
                continue
            if wanted in LABELS and item['state'] != wanted:
                continue
            if '今天' in text:
                dt = item['changed_at']
                if not dt or datetime.fromisoformat(dt).replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Asia/Shanghai')).date() != today:
                    continue
            rows.append(item)
    return rows


def reply_query(db, message_id, rows):
    # All matches, bounded per card; no silent truncation.
    for start in range(0, max(len(rows), 1), 25):
        chunk = rows[start:start+25]
        body = '\n'.join(f'畔色{x["factory_no"] or "待编号"}单 · {x["label"]} · {x["product_name"] or ""} · 子单{x["sub_order_no"] or x["order_no"]}' for x in chunk)
        feishu_client.reply_text(db, message_id,
            f'转发登记共 {len(rows)} 条（{start+1}–{start+len(chunk)}）\n' + (body or '没有符合条件的订单。') +
            '\n“已转发”仅为人工登记，未登记含暂缓；不等于工厂接单。')


def handle_query(db, event, text):
    if not event.get('_factory_forward_trusted'):
        return None
    from app.services import settings_service
    msg = event.get('message') or {}
    chat = settings_service.get(db, 'feishu_push_chat_id', env_fallback=False)
    if not chat or msg.get('chat_id') != chat:
        return None
    rows = query(db, text)
    if rows is None:
        return None
    reply_query(db, msg['message_id'], rows)
    return {'kind': 'factory_forward_query', 'count': len(rows)}
