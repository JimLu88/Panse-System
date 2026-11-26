"""Provisional one-piece factory orders, updated through the same Feishu card.

Purchase facts are immutable. A separate policy marker is never human confirmation.
PATCH retries address an existing message; no uncertain SEND is ever replayed.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from sqlalchemy import select, text
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.services import factory_production_evidence as facts

POLICY = 'factory_quantity_provisional_one_v1'


def marker(db, order, line):
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == POLICY))
    if not row or row.value_plain != '1':
        return None
    return {'schema': 'factory-quantity-provisional-v1', 'policy': POLICY,
            'identity': facts._identity(order, line), 'physical_qty': 1,
            'evidence_ref': 'user-instruction:2026-11-26:provisional-one-until-feishu-confirmation'}


@contextmanager
def _lock(db, line_id):
    # Dedicated connection keeps the lock across the workflow's durable commits.
    connection = None
    acquired = True
    if db.get_bind().dialect.name == 'postgresql':
        connection = db.get_bind().connect()
        acquired = connection.execute(text('SELECT pg_try_advisory_lock(11261126, :id)'), {'id': line_id}).scalar()
    try:
        yield acquired
    finally:
        if connection is not None:
            if acquired:
                connection.execute(text('SELECT pg_advisory_unlock(11261126, :id)'), {'id': line_id})
            connection.close()


def can_confirm(db, order, line, message_id):
    from app.services import factory_quantity_request_service as q
    record = q._read(q._row(db, q.PREFIX + str(line.id))) or {}
    return bool(record.get('provisional_delivered') and record.get('delivery_image_key')
        and not record.get('delivery_update_pending')
        and record.get('identity') == facts._identity(order, line)
        and record.get('delivery_message_id') == message_id == line.factory_delivery_message_id
        and line.factory_delivery_state == 'sent' and q._eligible(db, line, order))


def deliver(db, line_id):
    if os.getenv('PANSE_DISABLE_NOTIFY'):
        return {'state': 'notify_disabled'}
    with _lock(db, line_id) as acquired:
        if not acquired:
            return {'state': 'updating'}
        return _deliver(db, line_id)


def _deliver(db, line_id):
    from app.services import factory_quantity_request_service as q
    from app.services import factory_sheet, order_sheet_archive_service as sheets, feishu_client
    line = db.get(OrderDetail, line_id)
    order = db.scalar(select(Order).where(Order.order_no == line.order_no)) if line else None
    if not q._eligible(db, line, order):
        return {'state': 'ineligible'}
    record = q._read(q._row(db, q.PREFIX + str(line_id)))
    if not record:
        q.request_quantity(db, line_id=line_id)
        record = q._read(q._row(db, q.PREFIX + str(line_id)))
    if (not record or record.get('identity') != facts._identity(order, line)
            or not record.get('card_message_id') or record.get('state') not in ('awaiting_reply', 'confirmed', 'sent')):
        return {'state': 'quantity_card_unavailable'}
    if (line.factory_delivery_message_id and
            line.factory_delivery_message_id != record.get('delivery_message_id')):
        return {'state': 'existing_delivery_not_provisional'}
    if (record.get('provisional_delivered') and not record.get('delivery_update_pending')
            and line.factory_delivery_state == 'sent'):
        return {'state': 'sent', 'message_id': line.factory_delivery_message_id,
                'duplicate': True}
    sheet = factory_sheet.build_for_order_line(db, order.id, line.id,
        address_pending_for_production=sheets._can_push_production_only_without_address(order))
    if (not sheet.quantity_provisional and not (record.get('provisional_delivered') and sheet.quantity_confirmation)):
        return {'state': 'not_provisional'}
    if any(w.code in ('sales_detail_missing', 'linked_topup_binding_stale') for w in sheet.warnings):
        return {'state': 'production_facts_incomplete'}
    # A confirmed quantity is immutable during this update. Resuming uses this
    # same card, never a new factory order or an independent image SEND.
    row = q._row(db, q.PREFIX + str(line_id), lock=True)
    record = q._read(row)
    record.update(delivery_update_pending=True, delivery_message_id=record['card_message_id'])
    q._save(db, row, record)
    try:
        png = sheets.render_png(sheet)
        key = feishu_client.upload_image(db, png)
        proposed = dict(record, delivery_image_key=key, delivery_qty=sheet.qty,
                        delivery_update_pending=False,
                        provisional_delivered=True,
                        state='sent' if sheet.quantity_confirmation else 'awaiting_reply')
        mid = record['delivery_message_id']
        try:
            feishu_client.patch_card(db, mid, q.question_card(proposed))
        except Exception as exc:
            if getattr(exc, 'code', None) != 230031:
                raise
            # Existing guarded replacement persists a SEND claim before acting.
            # Its outcome cannot be retried when unknown.
            row = q._row(db, q.PREFIX + str(line_id), lock=True)
            staged = q._read(row)
            staged.update(delivery_image_key=key, delivery_qty=sheet.qty)
            q._save(db, row, staged)
            if not q._replace_expired_card(db, line_id, mid):
                raise RuntimeError('expired_card_replacement_unknown')
            latest = q._read(q._row(db, q.PREFIX + str(line_id)))
            mid = latest['expired_card_replacements'][mid]['message_id']
            # Replacement may already exist from an earlier pending update.
            feishu_client.patch_card(db, mid, q.question_card(proposed))
            proposed['expired_card_replacements'] = latest['expired_card_replacements']
            proposed['card_message_id'] = mid
        sheets.archive_sent_line_snapshot(db, order, line, png,
            source='quantity_card', rendered_sheet=sheet)
        line.factory_delivery_state = 'sent'
        line.factory_delivery_message_id = mid
        line.factory_delivery_sent_at = line.factory_delivery_sent_at or datetime.now(timezone.utc)
        line.factory_delivery_error = None
        row = q._row(db, q.PREFIX + str(line_id), lock=True)
        latest = q._read(row)
        latest.update(proposed, delivery_message_id=mid, card_sync_pending=True,
                      factory_sync_pending=True)
        q._save(db, row, latest)
    except Exception as exc:
        db.rollback()
        row = q._row(db, q.PREFIX + str(line_id), lock=True)
        latest = q._read(row)
        latest.update(delivery_update_pending=True, delivery_error_type=type(exc).__name__)
        q._save(db, row, latest)
        return {'state': 'update_pending', 'error_type': type(exc).__name__}
    q.refresh_cards(db, line_id)
    synced = sync_table(db, line_id)
    return {'state': 'sent', 'message_id': mid, 'physical_qty': sheet.qty,
            'provisional': bool(sheet.quantity_provisional), 'table': synced}


def sync_table(db, line_id):
    from app.services import factory_quantity_request_service as q, factory_dispatch_feishu_service as table
    row = q._row(db, q.PREFIX + str(line_id))
    record = q._read(row)
    try:
        result = table.sync(db, include_images=True, only_order_nos={record['identity']['order_no']})
        state = 'verified' if result.get('ok') and not result.get('skipped') else 'pending'
    except Exception as exc:
        db.rollback()
        result = {'error_type': type(exc).__name__}
        state = 'pending'
    row = q._row(db, q.PREFIX + str(line_id), lock=True)
    latest = q._read(row)
    latest.update(factory_sync_pending=state != 'verified', factory_sync={'state': state, 'result': result})
    q._save(db, row, latest)
    return {'state': state}
