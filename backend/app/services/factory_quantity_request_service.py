"""Durable, exact-child Feishu quantity questions. No AI inference or broad replay."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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


def _day():
    return datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()


def _card_ids(record):
    return list(dict.fromkeys([m for m in [record.get('card_message_id'),
        *(record.get('reminder_message_ids') or []),
        *(v.get('message_id') for v in (record.get('expired_card_replacements') or {}).values())] if m]))


def _replace_expired_card(db, line_id, message_id):
    """Feishu forbids edits after 14 days. Append one status card, never an image."""
    row = _row(db, PREFIX + str(line_id), lock=True)
    record = _read(row)
    replacements = record.setdefault('expired_card_replacements', {})
    if message_id in replacements:
        db.rollback()
        return replacements[message_id].get('state') == 'sent'
    replacements[message_id] = {'state': 'sending', 'claimed_at': _now()}
    _save(db, row, record)
    try:
        result = feishu_client.reply_card(db, message_id, question_card(record)) or {}
        mid = result.get('message_id') or (result.get('message') or {}).get('message_id')
        if not mid:
            raise RuntimeError('missing_replacement_card_message_id')
        row = _row(db, PREFIX + str(line_id), lock=True)
        latest = _read(row)
        latest['expired_card_replacements'][message_id] = {'state': 'sent', 'message_id': mid}
        db.add(SystemSetting(key=CARD_PREFIX + mid, value_plain=str(line_id), is_secret=False))
        _save(db, row, latest)
        return True
    except Exception as exc:
        db.rollback()
        row = _row(db, PREFIX + str(line_id), lock=True)
        latest = _read(row)
        latest['expired_card_replacements'][message_id] = {'state': 'unknown', 'error_type': type(exc).__name__}
        _save(db, row, latest)
        return False


def question_card(record, *, status=None, choosing=False):
    confirmed = bool(record.get('physical_qty') and
                     (record.get('reply_message_id') or record.get('confirmation_ref')))
    held = record.get('state') == 'held' and not confirmed
    title = '已确认数量' if confirmed else '旧卡已停用' if held else '待确认数量'
    state = status or ('已确认数量，正式图已发送' if record.get('state') == 'sent' else
                       '已确认数量，正式图待处理' if confirmed else
                       '订单资料已变化，请核对当前订单；本卡片已停用' if record.get('state') == 'held' else
                       '数量待确认，请勿生产')
    body = (f"畔色{record['factory_no']}单 · {state}\n"
            f"订单：{record['identity']['order_no']}\n子单：{record['identity']['sub_order_no']}\n"
            f"产品：{record['product']}\n规格：{record['sku']}\n"
            f"原购买数量：{facts.purchase_quantity_label(record['identity']['purchase_qty'])}\n")
    if confirmed:
        body += (f"已确认成品数量：{record['physical_qty']} 件\n"
                 "只按最终正式制单图生产，本卡片不作为生产单。")
    elif held:
        body += '本卡片对应的订单资料已变化或已有发送记录，不再通过旧卡确认数量。请以当前订单和正式制单图为准。'
    else:
        body += (f"订单备注：{record['notes'] or '无'}\n\n"
                 "点击下方按钮选择实际成品数量，确认后自动生成正式制单图。\n"
                 "也可回复本卡片并 @机器人 填写数量，例如 1 或 2。未确认前请勿生产。")
    if record.get('delivery_image_key') and not held:
        state = ('数量已确认，正在更新原图' if record.get('delivery_update_pending') and confirmed else
                 '已确认数量，原单已更新' if confirmed else '暂按1件已推送，数量待确认')
        body = (f"畔色{record['factory_no']}单 · {state}\n"
                f"子单：{record['identity']['sub_order_no']}\n"
                f"原购买数量：{facts.purchase_quantity_label(record['identity']['purchase_qty'])}\n"
                f"成品数量：{record.get('physical_qty') if confirmed else 1} 件"
                + ('（人工已确认）' if confirmed else '（暂定，待确认）')
                + "\n这是同一张生产单，确认后更新本卡和工厂表，不另下单。"
                + ("" if confirmed else "\n请点击按钮选择实际数量，也可回复本卡片并 @机器人 填写数量。"))
    elements = [{'tag': 'div', 'text': {'tag': 'plain_text', 'content': body}}]
    if record.get('delivery_image_key'):
        elements.append({'tag': 'img', 'img_key': record['delivery_image_key'],
                         'alt': {'tag': 'plain_text', 'content': '工厂下单图（同一单更新）'}})
    if not confirmed and record.get('state') in ('asking', 'awaiting_reply'):
        value = {'op': 'factory_quantity_choose', 'line_id': record['identity']['line_id'],
                 'revision': record.get('revision', 1)}
        if choosing:
            value['op'] = 'factory_quantity_confirm'
            action = {'tag': 'select_static', 'placeholder': {'tag': 'plain_text', 'content': '选择实际成品数量'},
                      'value': value, 'options': [
                          {'text': {'tag': 'plain_text', 'content': f'{qty} 件'}, 'value': str(qty)}
                          for qty in range(1, 101)],
                      'confirm': {'title': {'tag': 'plain_text', 'content': '确认实际成品数量'},
                                  'text': {'tag': 'plain_text', 'content': '请核对所选数量。确认后将按此数量生成正式制单图，原购买数量不变。'}}}
        else:
            action = {'tag': 'button', 'type': 'primary',
                      'text': {'tag': 'plain_text', 'content': '选择并确认数量'}, 'value': value}
        elements.append({'tag': 'action', 'actions': [action]})
    return {'config': {'wide_screen_mode': True, 'update_multi': True},
            'header': {'template': 'green' if confirmed else 'grey' if held else 'orange',
                       'title': {'tag': 'plain_text', 'content': f"畔色{record['factory_no']}单｜{title}"}},
            'elements': elements}


def refresh_cards(db, line_id):
    """Project quantity state; expired cards get one protected status reply."""
    row = _row(db, PREFIX + str(line_id))
    record = _read(row)
    if not record or record.get('delivery_update_pending'):
        return
    failed = []
    for mid in _card_ids(record):
        replacement = (record.get('expired_card_replacements') or {}).get(mid)
        if replacement:
            if replacement.get('state') != 'sent':
                failed.append(mid)
            continue
        try:
            feishu_client.patch_card(db, mid, question_card(record))
        except Exception as exc:
            if getattr(exc, 'code', None) == 230031 and _replace_expired_card(db, line_id, mid):
                continue
            failed.append(mid)
            log.warning('数量卡状态更新待重试 line=%s', line_id)
    row = _row(db, PREFIX + str(line_id), lock=True)
    latest = _read(row)
    # A concurrent confirmation must not be acknowledged by a stale pending patch.
    if latest.get('state') != record.get('state') or latest.get('physical_qty') != record.get('physical_qty'):
        latest['card_sync_pending'] = True
    else:
        latest['card_sync_pending'] = bool(failed)
        latest['card_sync_failed_ids'] = failed
    _save(db, row, latest)


def _remind(db, row, record):
    day = _day()
    if (record.get('state') != 'awaiting_reply' or record.get('reply_message_id')
            or record.get('confirmation_ref') or record.get('reminder_state') in ('sending', 'unknown')
            or record.get('last_reminder_day', record.get('created_at', '')[:10]) == day):
        db.rollback()
        return {'state': record['state'], 'card_message_id': record.get('card_message_id')}
    record.update(reminder_state='sending', last_reminder_day=day)
    _save(db, row, record)
    try:
        result = feishu_client.send_card(db, record['chat_id'], question_card(record)) or {}
        mid = result.get('message_id') or (result.get('message') or {}).get('message_id')
        if not mid:
            raise RuntimeError('missing_reminder_message_id')
        row = _row(db, PREFIX + str(record['identity']['line_id']), lock=True)
        latest = _read(row)
        latest['reminder_message_ids'] = [*(latest.get('reminder_message_ids') or []), mid]
        latest['reminder_state'] = 'sent'
        db.add(SystemSetting(key=CARD_PREFIX + mid, value_plain=str(record['identity']['line_id']), is_secret=False))
        _save(db, row, latest)
        # A reply may have confirmed the order while the reminder was sending.
        if latest.get('state') != 'awaiting_reply':
            refresh_cards(db, record['identity']['line_id'])
        return {'state': latest['state'], 'card_message_id': mid, 'reminded': True}
    except Exception as exc:
        db.rollback()
        row = _row(db, PREFIX + str(record['identity']['line_id']), lock=True)
        latest = _read(row)
        latest.update(reminder_state='unknown', reminder_error_type=type(exc).__name__)
        _save(db, row, latest)
        return {'state': latest['state'], 'reminder_state': 'unknown'}


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
    old = _row(db, PREFIX + str(line_id), lock=True)
    history = []
    if old:
        record = _read(old)
        # Same identity and uncertain sends are never replayed. A changed
        # identity needs a new question, not the old answer or an inert held card.
        if record.get('identity') == facts._identity(order, line):
            if (not line.factory_delivery_message_id and not line.factory_delivery_sent_at
                    and line.factory_delivery_state in (None, '', 'failed')
                    and facts.quantity_confirmation(db, order, line) is None):
                return _remind(db, old, record)
            db.rollback()
            return {'state': record['state'], 'card_message_id': record.get('card_message_id')}
        if record['state'] in ('asking', 'ask_uncertain', 'sent') or record.get('reminder_state') in ('sending', 'unknown'):
            db.rollback()
            return {'state': record['state'], 'card_message_id': record.get('card_message_id')}
        history = list(record.get('superseded') or [])
        history.append({k: v for k, v in record.items() if k != 'superseded'})
    if line.factory_delivery_message_id or line.factory_delivery_sent_at or line.factory_delivery_state not in (None, '', 'failed'):
        db.rollback()
        return {'state': 'delivery_claimed'}
    sheet = factory_sheet.build_for_order_line(db, order.id, line.id, allow_provisional=False)
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
              'chat_id': chat, 'created_at': _now(), 'last_reminder_day': _day(), 'revision': len(history) + 1,
              'superseded': history}
    row = old or SystemSetting(key=PREFIX + str(line_id), is_secret=False,
                               description='生产数量待确认及飞书回复回执')
    row.value_plain = json.dumps(record, ensure_ascii=False)
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
    if history and record['state'] == 'awaiting_reply':
        prior_mid = history[-1].get('card_message_id')
        if prior_mid:
            _notice_once(db, prior_mid, '订单资料已变化，此旧数量卡已失效。请回复新数量卡重新确认，旧回复不会沿用。')
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
            or card_id not in _card_ids(record)
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
    return _confirm(db, line_id, int(match[1]), 'feishu:' + sender['id'], 'feishu-message:' + mid, mid)


def _confirm(db, line_id, qty, actor, evidence_ref, mid):
    row = _row(db, PREFIX + str(line_id), lock=True)
    record = _read(row)
    result = {'kind': 'factory_quantity', 'message_id': mid}
    if record.get('reply_message_id') or record.get('confirmation_ref'):
        db.rollback()
        if record.get('physical_qty') != qty:
            _notice_once(db, mid, f"此前已确认 {record['physical_qty']} 件，本次回复不同，未覆盖、未重复发单。请联系订单负责人核对，勿按本次回复改变生产。")
            return {**result, 'state': 'conflicting_reply'}
        return {**result, 'state': record['state'], 'duplicate': True}
    if record.get('delivery_update_pending'):
        db.rollback()
        return {**result, 'state': 'updating'}
    if record['state'] != 'awaiting_reply':
        db.rollback()
        return {**result, 'state': record['state']}
    line = db.scalar(select(OrderDetail).where(OrderDetail.id == line_id).with_for_update().execution_options(populate_existing=True))
    order = db.scalar(select(Order).where(Order.order_no == line.order_no).with_for_update().execution_options(populate_existing=True)) if line else None
    from app.services import factory_provisional_quantity_service as provisional
    provisional_sent = bool(order and line and provisional.can_confirm(
        db, order, line, record.get('delivery_message_id')))
    if (not _eligible(db, line, order) or facts._identity(order, line) != record['identity']
            or (not provisional_sent and (line.factory_delivery_message_id or line.factory_delivery_sent_at
                or line.factory_delivery_state not in (None, '', 'failed')))):
        record.update(state='held', reason='订单变化、关闭或已有发送占用，旧卡片不能确认')
        _save(db, row, record)
        _notice_once(db, mid, '订单状态、规格、购买数量或备注已有变化，或者已经进入发送。此次未确认，请核对最新订单；不要重复回复旧卡片。')
        return {**result, 'state': 'held'}
    try:
        facts.confirm_quantity(db, line_id=line_id, expected_sku=line.sku_code,
                               expected_purchase_qty=line.qty, physical_qty=qty, actor=actor,
                               evidence_ref=evidence_ref, commit=False,
                               provisional_message_id=record.get('delivery_message_id') if provisional_sent else None)
        record.update(state='confirmed', physical_qty=qty, actor=actor,
                      reply_message_id=mid, confirmation_ref=evidence_ref, confirmed_at=_now(), card_sync_pending=True,
                      **({'delivery_update_pending': True} if provisional_sent else {}))
        _save(db, row, record)
    except ValueError:
        db.rollback()
        _notice_once(db, mid, '订单已有其他数量确认或发送状态变化，本次没有覆盖，也没有重复发单，请人工核对。')
        return {**result, 'state': 'held'}
    if provisional_sent:
        delivery = provisional.deliver(db, line_id)
        return {**result, **delivery, 'physical_qty': qty}
    refresh_cards(db, line_id)
    from app.services import order_sheet_archive_service as sheets
    delivery = sheets.reconcile_order_line_delivery(db, limit=1, only_sub_order_nos={line.sub_order_no})
    db.expire_all()
    line = db.get(OrderDetail, line_id)
    if line.factory_delivery_state != 'sent':
        _notice_once(db, mid, f"已确认实际成品 {qty} 件，原购买数量和财务未改。正式制单图尚未发送成功，请勿生产；原因：{line.factory_delivery_error or delivery.get('reason') or '待核对送达状态'}")
    from app.services import order_post_push_audit
    checked = order_post_push_audit.audit(db, only_order_nos={line.order_no},
                                        only_sub_order_nos={line.sub_order_no})
    if not checked.get('ok') and line.factory_delivery_state == 'sent':
        _notice_once(db, mid, '正式制单图已发送，但原始报表与飞书逐单核对仍有缺项，系统已记录待处理；不会重复发图。')
    refresh_cards(db, line_id)
    return {**result, 'state': line.factory_delivery_state, 'physical_qty': qty,
            'delivery': delivery, 'post_push_audit': checked}


def complete_delivery(db, line):
    """Called after a real image receipt. Caption failures never reset sent state."""
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return
    row = _row(db, PREFIX + str(line.id), lock=True)
    record = _read(row)
    if (not record or not (record.get('reply_message_id') or record.get('confirmation_ref')) or record.get('receipt_state')
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
                f"确认人：{name}（依据本卡片的数量确认）。\n"
                "原购买数量及财务金额不变；请按最新正式制单图的确认数量生产，待确认卡片不作为生产单。\n"
                + ("工厂表已同步并回读。" if record['factory_sync']['state']=='verified'
                   else "工厂表尚未确认同步；系统已单独记录，不会因此重复发制单图。"))
        receipt = feishu_client.reply_text(db, record['card_message_id'], text) or {}
        receipt_id = receipt.get('message_id') or (receipt.get('message') or {}).get('message_id')
        if not receipt_id:
            raise RuntimeError('quantity_receipt_message_id_missing')
        record.update(receipt_state='sent', receipt_message_id=receipt_id)
        _save(db, row, record)
    except Exception as exc:
        db.rollback()
        log.exception('数量解释/卡片更新未完成，不重发生产图 line=%s', line.id)
        record['receipt_error_type'] = type(exc).__name__
        _save(db, row, record)
    finally:
        refresh_cards(db, line.id)


def complete_pending_receipts(db, *, only_sub_order_nos=None):
    """Resume a crash after the image receipt, never resend that image."""
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return
    rows = list(db.scalars(select(SystemSetting).where(SystemSetting.key.startswith(PREFIX))))
    for row in rows:
        record = _read(row)
        if only_sub_order_nos is not None and record['identity']['sub_order_no'] not in only_sub_order_nos:
            continue
        if record.get('delivery_message_id'):
            from app.services import factory_provisional_quantity_service as provisional
            line_id = record['identity']['line_id']
            if record.get('delivery_update_pending'):
                provisional.deliver(db, line_id)
            elif record.get('factory_sync_pending'):
                provisional.sync_table(db, line_id)
            if record.get('card_sync_pending'):
                refresh_cards(db, line_id)
            continue
        if record.get('card_sync_pending'):
            refresh_cards(db, record['identity']['line_id'])
        if not (record.get('reply_message_id') or record.get('confirmation_ref')) or record.get('receipt_state'):
            continue
        line = db.get(OrderDetail, record['identity']['line_id'])
        if line and line.factory_delivery_state == 'sent' and line.factory_delivery_message_id:
            try:
                complete_delivery(db, line)
            except Exception:
                db.rollback()
                log.exception('数量确认解释恢复未完成 line=%s', line.id)


def handle_card(db, event):
    """Only authenticated SDK/webhook callbacks can confirm the current card."""
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return {'state': 'notify_disabled'}
    if not event.get('_factory_forward_trusted'):
        raise ValueError('卡片来源未验证，未确认数量')
    action = event.get('action') or {}
    value = action.get('value') or {}
    context = event.get('context') or {}
    mid = context.get('open_message_id')
    actor = (event.get('operator') or {}).get('open_id')
    binding = _row(db, CARD_PREFIX + str(mid))
    if not binding or not actor or str(value.get('line_id')) != binding.value_plain:
        raise ValueError('卡片与订单不匹配，未确认数量')
    line_id = int(binding.value_plain)
    row = _row(db, PREFIX + str(line_id), lock=True)
    record = _read(row)
    if (not record or mid not in _card_ids(record) or value.get('revision') != record.get('revision', 1)
            or (context.get('open_chat_id') and context['open_chat_id'] != record['chat_id'])):
        db.rollback()
        raise ValueError('此数量卡已失效，请使用当前卡片')
    if value.get('op') == 'factory_quantity_choose':
        db.rollback()
        try:
            feishu_client.patch_card(db, mid, question_card(record, choosing=True))
        except Exception as exc:
            if getattr(exc, 'code', None) != 230031:
                raise
            refresh_cards(db, line_id)
            return {'state': 'replacement_card'}
        latest = _read(_row(db, PREFIX + str(line_id), lock=True))
        changed = latest.get('state') != record.get('state')
        db.rollback()
        if changed:
            refresh_cards(db, line_id)
        return {'state': latest['state']}
    raw_qty = action.get('option')
    if value.get('op') != 'factory_quantity_confirm' or not re.fullmatch(r'[1-9][0-9]{0,8}', str(raw_qty or '')):
        db.rollback()
        raise ValueError('请选择有效的实际成品数量')
    return _confirm(db, line_id, int(raw_qty), 'feishu:' + actor,
                    f'feishu-card:{mid}:revision:{record.get("revision", 1)}', mid)


def run_card_action(event):
    # The SDK acknowledgement is returned immediately; state is durable before delivery.
    from app.database import SessionLocal
    with SessionLocal() as db:
        try:
            handle_card(db, event)
        except ValueError as exc:
            db.rollback()
            mid = (event.get('context') or {}).get('open_message_id')
            if mid:
                _notice_once(db, mid, str(exc))
        except Exception:
            db.rollback()
            log.exception('数量卡处理未完成，保留原确认/发送状态')


def refresh_existing_cards(db):
    """Upgrade card status from facts; expired cards get a reply, never a new confirmation/image."""
    rows = list(db.scalars(select(SystemSetting).where(SystemSetting.key.startswith(PREFIX))))
    result = {'updated': 0, 'pending': 0}
    for row in rows:
        record = _read(row)
        line_id = record['identity']['line_id']
        line = db.get(OrderDetail, line_id)
        order = db.scalar(select(Order).where(Order.order_no == line.order_no)) if line else None
        confirmation = facts.quantity_confirmation(db, order, line) if line and order else None
        if confirmation and confirmation['identity'] == record['identity']:
            record.update(physical_qty=confirmation['physical_qty'], actor=confirmation['actor'],
                          confirmation_ref=confirmation['evidence_ref'])
            record['state'] = 'sent' if line.factory_delivery_state == 'sent' and line.factory_delivery_message_id else 'confirmed'
        elif record.get('state') == 'awaiting_reply' and (not line or not order or
                not _eligible(db, line, order) or record['identity'] != facts._identity(order, line)):
            record.update(state='held')
        record['card_sync_pending'] = True
        _save(db, row, record)
        refresh_cards(db, line_id)
        result['updated'] += 1
        result['pending'] += bool(_read(_row(db, PREFIX + str(line_id))).get('card_sync_pending'))
    return result


def remind_pending(db, *, only_sub_order_nos=None):
    """At an ordinary push, revisit every pending card, independent of image batch limits."""
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return
    keys = list(db.scalars(select(SystemSetting.key).where(SystemSetting.key.startswith(PREFIX))))
    for key in keys:
        row = _row(db, key, lock=True)
        record = _read(row)
        if (not record or record.get('state') != 'awaiting_reply' or
                (only_sub_order_nos is not None and record['identity']['sub_order_no'] not in only_sub_order_nos)):
            db.rollback()
            continue
        line = db.get(OrderDetail, record['identity']['line_id'])
        order = db.scalar(select(Order).where(Order.order_no == line.order_no)) if line else None
        if (not _eligible(db, line, order) or record['identity'] != facts._identity(order, line)
                or (not record.get('provisional_delivered') and (line.factory_delivery_message_id or line.factory_delivery_sent_at
                    or line.factory_delivery_state not in (None, '', 'failed')))
                or record.get('delivery_update_pending') or facts.quantity_confirmation(db, order, line)):
            db.rollback()
            continue
        _remind(db, row, record)
