"""Read-only scope audit and explicit-owner retry for prior dimension-only failures."""
import hashlib
import json
from sqlalchemy import select
from app.models.order import Order, OrderDetail
from app.services import factory_sheet, order_line_delivery_service as delivery
from app.services import order_sheet_archive_service as archive

SCHEMA = 'factory-size-retry-v1'
SIZE_CODES = {'variant_size_unverified', 'size_unverified', 'custom_size_in_remark'}


def scan(db):
    active = {x.id for x in delivery.active_lines(db)}
    sent = delivery.sent_line_evidence(db)
    items = []
    for line in db.scalars(select(OrderDetail).where(OrderDetail.factory_delivery_required.is_(True))):
        old_error = '本SKU专属尺寸未核实' in (line.factory_delivery_error or '')
        if not old_error and (line.id not in active or line.factory_delivery_state == 'sent'
                              or line.factory_delivery_message_id or line.factory_delivery_sent_at):
            continue
        order = db.scalar(select(Order).where(Order.order_no == line.order_no))
        facts_failed = False
        try:
            sheet = factory_sheet.build_for_order_line(db, order.id, line.id) if order else None
        except Exception:
            sheet, facts_failed = None, True
        size_missing = bool(sheet and (not sheet.size_info or any(w.code in SIZE_CODES for w in sheet.warnings)))
        if not old_error and not size_missing and not facts_failed:
            continue
        protected = (bool(line.factory_delivery_message_id or line.factory_delivery_sent_at)
                     or str(line.sub_order_no) in sent
                     or line.factory_delivery_state not in (None, '', 'failed'))
        blockers = []
        if facts_failed or sheet is None: blockers.append('production_facts_unavailable')
        if protected: blockers.append('sent_unknown_or_inflight')
        if line.id not in active: blockers.append('not_active_factory_line')
        if not old_error: blockers.append('not_prior_dimension_failure_use_normal_queue')
        if not line.sku_code and not line.sku_name: blockers.append('sku_missing')
        if sheet and any(w.code == 'production_quantity_unverified' for w in sheet.warnings):
            blockers.append('production_quantity_unverified')
        if order and not archive._addr_ok_for_factory(order) and not archive._can_push_production_only_without_address(order):
            blockers.append('address_incomplete')
        item = {'line_id': line.id, 'order_no': line.order_no, 'sub_order_no': line.sub_order_no,
                'factory_no': line.factory_no, 'sku_code': line.sku_code, 'qty': line.qty,
                'state': line.factory_delivery_state, 'prior_dimension_error': old_error,
                'size_unverified': size_missing, 'ready': not blockers, 'blockers': blockers}
        facts = {**item, 'sku_name': line.sku_name, 'product_code': line.product_code,
                 'size_info': sheet.size_info if sheet else None,
                 'note': sheet.production_note if sheet else None,
                 'remark': sheet.remark if sheet else None}
        item['fingerprint'] = hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False,
                                                       default=str).encode()).hexdigest()
        items.append(item)
    return {'schema': SCHEMA, 'read_only': True, 'items': items,
            'ready_sub_order_nos': [x['sub_order_no'] for x in items if x['ready']]}


def apply_manifest(db, manifest):
    if manifest.get('schema') != SCHEMA:
        raise ValueError('manifest schema mismatch')
    selected = [x for x in manifest.get('items', []) if x.get('ready')]
    if not selected:
        raise ValueError('no approved candidates')
    current = {x['line_id']: x for x in scan(db)['items']}
    scope, skipped = set(), []
    for item in selected:
        now = current.get(item['line_id'])
        if now and 'sent_unknown_or_inflight' in now['blockers']:
            skipped.append(item['line_id'])
            continue
        # Successful lines may no longer be returned by the missing-size scan.
        line = db.get(OrderDetail, item['line_id'])
        if line and (line.factory_delivery_message_id or line.factory_delivery_sent_at or
                     line.factory_delivery_state in ('sent', 'uncertain', 'unknown', 'rendering', 'sending_image', 'sending_caption')):
            skipped.append(item['line_id'])
            continue
        if not now or not now['ready'] or now['fingerprint'] != item.get('fingerprint'):
            raise ValueError('scope changed; re-scan before retry: ' + str(item['line_id']))
        scope.add(now['sub_order_no'])
    if not scope:
        return {'pushed': 0, 'protected_line_ids': skipped, 'reason': 'all_protected'}
    result = archive.reconcile_order_line_delivery(db, only_sub_order_nos=scope, limit=len(scope))
    return {**result, 'protected_line_ids': skipped, 'scope': sorted(scope)}
