"""Durable, exact-child Feishu quantity questions. No AI inference or broad replay."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

from sqlalchemy import select
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.services import factory_production_evidence as facts
from app.services import feishu_client, settings_service

log = logging.getLogger('panse.factory_quantity')
PREFIX = 'factory_quantity_request:'
CARD_PREFIX = 'factory_quantity_card:'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _row(db, key, *, lock=False):
    query = select(SystemSetting).where(SystemSetting.key == key)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return db.scalar(query)


def _read(row):
    return json.loads(row.value_plain) if row else None


def _save(db, row, record):
    row.value_plain = json.dumps(record, ensure_ascii=False)
    db.commit()


def _eligible(db, line, order):
    from app.services.order_line_delivery_service import line_is_factory_eligible
    return bool(line and order and line.factory_delivery_required and line.sub_order_no
                and line_is_factory_eligible(db, line, order))


def question_card(record, *, status=None):
    # Plain text fields prevent product/remark text from becoming mentions/links.
    body = (f"畔色{record['factory_no']}单 · 数量待确认，请勿生产\n"
            f"订单：{record['identity']['order_no']}\n子单：{record['identity']['sub_order_no']}\n"
            f"产品：{record['product']}\n规格：{record['sku']}\n"
            f"原购买数量：{facts.purchase_quantity_label(record['identity']['purchase_qty'])}（不等于已确认成品数）\n"
            f"订单备注：{record['notes'] or '无'}\n\n"
            "请负责此订单的同事回复本卡片，并 @机器人，填写实际成品数量。"
            "例如回复 1 表示1件，回复 2 表示2件。请勿仅在群里单独发数字。\n"
            "确认后系统自动生成正式制单图；未确认前不要据此生产。")
    if status:
        body = (f"畔色{record['factory_no']}单 · {status}\n"
                f"订单：{record['identity']['order_no']}\n"
                f"子单：{record['identity']['sub_order_no']}\n"
                f"产品：{record['product']}\n规格：{record['sku']}\n"
                f"原购买数量：{facts.purchase_quantity_label(record['identity']['purchase_qty'])}\n"
                f"确认成品数量：{record.get('physical_qty', '待确认')}\n"
                "只按最终正式制单图生产，本卡片不作为生产单。")
    return {'config': {'wide_screen_mode': True},
            'header': {'template': 'orange', 'title': {'tag': 'plain_text',
                       'content': f"畔色{record['factory_no']}单｜数量确认"}},
            'elements': [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': body}}]}


def request_quantity(db, *, line_id):
    """Claim once before external send; an unknown result is never replayed."""
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return {'state': 'notify_disabled'}
    from app.services import factory_sheet
    line = db.scalar(select(OrderDetail).where(OrderDetail.id == line_id).with_for_update())
    order = db.scalar(select(Order).where(Order.order_no == line.order_no)) if line else None
    if not _eligible(db, line, order):
        db.rollback()
        return {'state': 'ineligible'}
    old = _row(db, PREFIX + str(line_id))
    if old:
        record = _read(old)
        db.rollback()
        return {'state': record['state'], 'card_message_id': record.get('card_message_id')}
    if line.factory_delivery_message_id or line.factory_delivery_state not in (None, '', 'failed'):
        db.rollback()
        return {'state': 'delivery_claimed'}
    sheet = factory_sheet.build_for_order_line(db, order.id, line.id)
    if not any(w.code == 'production_quantity_unverified' for w in sheet.warnings):
        db.rollback()
        return {'state': 'quantity_already_known'}
    chat = settings_service.get(db, 'feishu_push_chat_id', env_fallback=False)
    if not chat:
        db.rollback()
        return {'state': 'no_chat_id'}
    record = {'schema': 'factory-quantity-request-v1', 'state': 'asking',
              'identity': facts._identity(order, line), 'factory_no': line.factory_no,
              'product': (line.product_name or order.product_name or line.product_code or '')[:160],
              'sku': (line.sku_name or order.sku or line.sku_code or '')[:160],
              'notes': '\n'.join(str(x) for x in [order.buyer_message, order.remark,
                          order.seller_memo, getattr(order, 'production_note', None)] if x)[:600],
              'chat_id': chat, 'created_at': _now()}
    row = SystemSetting(key=PREFIX + str(line_id), value_plain=json.dumps(record, ensure_ascii=False),
                        is_secret=False, description='生产数量待确认及飞书回复回执')
    db.add(row)
    db.commit()
    try:
        result = feishu_client.send_card(db, chat, question_card(record)) or {}
        mid = result.get('message_id') or (result.get('message') or {}).get('message_id')
        if not mid:
            raise RuntimeError('quantity_card_message_id_missing')
        record.update(state='awaiting_reply', card_message_id=mid)
        db.add(SystemSetting(key=CARD_PREFIX + mid, value_plain=str(line_id), is_secret=False))
        _save(db, row, record)
    except Exception as exc:
        db.rollback()
        row = _row(db, PREFIX + str(line_id))
        record.update(state='ask_uncertain', error_type=type(exc).__name__)
        _save(db, row, record)
        log.exception('数量确认卡发送未完成 line=%s', line_id)
    return {'state': record['state'], 'card_message_id': record.get('card_message_id')}


def _message_text(item):
    content = json.loads((item.get('body') or {}).get('content') or '{}')
    if item.get('msg_type') == 'text':
        value = content.get('text') or ''
        # Only strip Feishu's actual mention placeholders, not arbitrary words.
        return re.sub(r'@_user_\d+|@_all', '', value).strip()
    if item.get('msg_type') == 'post':
        if 'zh_cn' in content:
            content = content['zh_cn']
        parts = [str(content.get('title') or '')]
        for line in content.get('content') or []:
            for node in line:
                if node.get('tag') == 'text':
                    parts.append(str(node.get('text') or ''))
                elif node.get('tag') != 'at':
                    return ''
        return ' '.join(parts).strip()
    return ''


def _notice_once(db, message_id, text):
    key = 'factory_quantity_notice:' + message_id
    if _row(db, key):
        return
    db.add(SystemSetting(key=key, value_plain='claimed', is_secret=False))
    db.commit()
    try:
        result = feishu_client.reply_text(db, message_id, text)
        row = _row(db, key)
        row.value_plain = json.dumps(result, ensure_ascii=False)
        db.commit()
    except Exception:
        db.rollback()
        log.exception('数量确认回复结果未知，不重发 message=%s', message_id)


def handle_reply(db, event):
    """Route only a reply to our persisted card; read back its real Feishu body."""
    msg = event.get('message') or {}
    mid = msg.get('message_id')
    if not mid or msg.get('message_type') not in ('text', 'post'):
        return None
    card_id = None
    binding = None
    for candidate in [msg.get('parent_id'), msg.get('root_id')]:
        if candidate:
            binding = _row(db, CARD_PREFIX + candidate)
            if binding:
                card_id = candidate
                break
    if not binding:
        return None
    result = {'kind': 'factory_quantity', 'message_id': mid}
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return {**result, 'state': 'notify_disabled'}
    line_id = int(binding.value_plain)
    # Never trust an arbitrary webhook's sender, quantity or chat fields. Verify
    # the actual existing message through the bot's authenticated official API.
    items = feishu_client._req(db, 'GET', feishu_client._BASE + '/im/v1/messages/' + mid).get('items') or []
    if len(items) != 1:
        return {**result, 'state': 'message_unverified'}
    actual = items[0]
    sender = actual.get('sender') or {}
    row = _row(db, PREFIX + str(line_id), lock=True)
    record = _read(row)
    if (not record or actual.get('message_id') != mid or actual.get('deleted') is not False
            or actual.get('chat_id') != record['chat_id'] or msg.get('chat_id') != record['chat_id']
            or record.get('card_message_id') != card_id
            or card_id not in (actual.get('parent_id'), actual.get('root_id'))
            or sender.get('sender_type') != 'user' or not sender.get('id')):
        db.rollback()
        return {**result, 'state': 'message_unverified'}
    text = _message_text(actual)
    match = re.fullmatch(r'([1-9][0-9]{0,8})\s*(?:件|张|套)?', text)
    if not match:
        db.rollback()
        _notice_once(db, mid, '数量尚未确认。请回复对应卡片并 @机器人，只填写实际成品数量，例如 1 或 2。')
        return {**result, 'state': 'invalid_quantity'}
    qty = int(match[1])
    if record.get('reply_message_id'):
        db.rollback()
        if record.get('physical_qty') != qty:
            _notice_once(db, mid, f"此前已确认 {record['physical_qty']} 件，本次回复不同，未覆盖、未重复发单。请联系订单负责人核对，勿按本次回复改变生产。")
            return {**result, 'state': 'conflicting_reply'}
        return {**result, 'state': record['state'], 'duplicate': True}
    if record['state'] != 'awaiting_reply':
        db.rollback()
        return {**result, 'state': record['state']}
    line = db.scalar(select(OrderDetail).where(OrderDetail.id == line_id).with_for_update().execution_options(populate_existing=True))
    order = db.scalar(select(Order).where(Order.order_no == line.order_no).with_for_update().execution_options(populate_existing=True)) if line else None
    if (not _eligible(db, line, order) or facts._identity(order, line) != record['identity']
            or line.factory_delivery_message_id or line.factory_delivery_state not in (None, '', 'failed')):
        record.update(state='held', reason='订单变化、关闭或已有发送占用，旧卡片不能确认')
        _save(db, row, record)
        _notice_once(db, mid, '订单状态、规格、购买数量或备注已有变化，或者已经进入发送。此次未确认，请核对最新订单；不要重复回复旧卡片。')
        return {**result, 'state': 'held'}
    actor = 'feishu:' + sender['id']
    try:
        facts.confirm_quantity(db, line_id=line_id, expected_sku=line.sku_code,
                               expected_purchase_qty=line.qty, physical_qty=qty, actor=actor,
                               evidence_ref='feishu-message:' + mid, commit=False)
        record.update(state='confirmed', physical_qty=qty, actor=actor,
                      reply_message_id=mid, confirmed_at=_now())
        _save(db, row, record)
    except ValueError:
        db.rollback()
        _notice_once(db, mid, '订单已有其他数量确认或发送状态变化，本次没有覆盖，也没有重复发单，请人工核对。')
        return {**result, 'state': 'held'}
    from app.services import order_sheet_archive_service as sheets
    delivery = sheets.reconcile_order_line_delivery(db, limit=1, only_sub_order_nos={line.sub_order_no})
    db.expire_all()
    line = db.get(OrderDetail, line_id)
    if line.factory_delivery_state != 'sent':
        _notice_once(db, mid, f"已确认实际成品 {qty} 件，原购买数量和财务未改。正式制单图尚未发送成功，请勿生产；原因：{line.factory_delivery_error or delivery.get('reason') or '待核对送达状态'}")
    return {**result, 'state': line.factory_delivery_state, 'physical_qty': qty, 'delivery': delivery}


def complete_delivery(db, line):
    """Called after a real image receipt. Caption failures never reset sent state."""
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return
    row = _row(db, PREFIX + str(line.id), lock=True)
    record = _read(row)
    if (not record or not record.get('reply_message_id') or record.get('receipt_state')
            or line.factory_delivery_state != 'sent' or not line.factory_delivery_message_id):
        db.rollback()
        return
    record.update(state='sent', image_message_id=line.factory_delivery_message_id,
                  receipt_state='claimed')
    _save(db, row, record)
    # The image is already sent. Persist a distinct projection result; never
    # clear the image receipt or resend it because this table update failed.
    record['factory_sync'] = {'state': 'claimed', 'at': _now()}
    _save(db, row, record)
    try:
        from app.services import factory_dispatch_feishu_service
        synced = factory_dispatch_feishu_service.sync_if_enabled(db)
        record['factory_sync'] = {
            'state': 'disabled' if synced.get('skipped') else 'verified' if synced.get('ok') else 'failed',
            'at': _now(), 'errors': synced.get('errors') or [],
        }
        _save(db, row, record)
    except Exception as exc:
        db.rollback()
        record['factory_sync'] = {'state':'unknown', 'at':_now(), 'error_type':type(exc).__name__}
        _save(db, row, record)
        log.exception('数量已确认、图已送达，工厂表同步未确认；不重发图 line=%s', line.id)
    try:
        name = feishu_client.get_user_name(db, record['actor'].removeprefix('feishu:')) or record['actor']
        text = (f"畔色{record['factory_no']}单数量已确认，正式制单图已发送。\n"
                f"子单：{record['identity']['sub_order_no']}\n"
                f"原购买数量 {facts.purchase_quantity_label(record['identity']['purchase_qty'])}，实际成品 {record['physical_qty']} 件。\n"
                f"确认人：{name}（依据本卡片下的回复）。\n"
                "原购买数量及财务金额不变；请按最新正式制单图的确认数量生产，待确认卡片不作为生产单。\n"
                + ("工厂表已同步并回读。" if record['factory_sync']['state']=='verified'
                   else "工厂表尚未确认同步；系统已单独记录，不会因此重复发制单图。"))
        receipt = feishu_client.reply_text(db, record['card_message_id'], text) or {}
        receipt_id = receipt.get('message_id') or (receipt.get('message') or {}).get('message_id')
        if not receipt_id:
            raise RuntimeError('quantity_receipt_message_id_missing')
        record.update(receipt_state='sent', receipt_message_id=receipt_id)
        _save(db, row, record)
        feishu_client.patch_card(db, record['card_message_id'], question_card(record, status='数量已确认，正式图已发送'))
    except Exception as exc:
        db.rollback()
        log.exception('数量解释/卡片更新未完成，不重发生产图 line=%s', line.id)
        record['receipt_error_type'] = type(exc).__name__
        _save(db, row, record)


def complete_pending_receipts(db, *, only_sub_order_nos=None):
    """Resume a crash after the image receipt, never resend that image."""
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return
    rows = list(db.scalars(select(SystemSetting).where(SystemSetting.key.startswith(PREFIX))))
    for row in rows:
        record = _read(row)
        if not record.get('reply_message_id') or record.get('receipt_state'):
            continue
        if only_sub_order_nos is not None and record['identity']['sub_order_no'] not in only_sub_order_nos:
            continue
        line = db.get(OrderDetail, record['identity']['line_id'])
        if line and line.factory_delivery_state == 'sent' and line.factory_delivery_message_id:
            try:
                complete_delivery(db, line)
            except Exception:
                db.rollback()
                log.exception('数量确认解释恢复未完成 line=%s', line.id)
