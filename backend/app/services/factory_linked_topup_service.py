"""Explicit one-item main/top-up production binding; no sends or financial edits."""
import json
from hashlib import sha256
from decimal import Decimal
from sqlalchemy import select
from app.models.order import Order, OrderDetail, FactoryOrder
from app.models.pricing import PricingSku
from app.models.settings import SystemSetting

PREFIX = 'factory_linked_topup_main:'
MEMBER_PREFIX = 'factory_linked_topup_member:'


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _dimensions(value):
    if (not isinstance(value, dict) or set(value) != {'length', 'depth', 'height'}
            or any(type(v) is not int or not 0 < v <= 20000 for v in value.values())):
        raise ValueError('必须明确提供长、深、高的整数毫米值')
    return dict(value)


def _placeholder(sku):
    from app.services.sku_utils import is_custom_sku_code
    return bool(sku and (sku.is_custom_placeholder or is_custom_sku_code(sku.sku_code)))


def _facts(db, main_order_no, topup_order_no, *, lock=False):
    from app.services import order_line_delivery_service as delivery
    if not main_order_no or main_order_no == topup_order_no:
        raise ValueError('主单与补差单必须是不同订单')
    query = select(Order).where(Order.order_no.in_([main_order_no, topup_order_no])).order_by(Order.id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    orders = {o.order_no: o for o in db.scalars(query)}
    if len(orders) != 2:
        raise ValueError('主单或补差单不存在')
    result = {}
    for role, no in [('main', main_order_no), ('topup', topup_order_no)]:
        o = orders[no]
        rows = list(db.scalars(select(OrderDetail).where(OrderDetail.order_no == no,
            OrderDetail.source == 'import', OrderDetail.sub_order_no.isnot(None), OrderDetail.sub_order_no != '')))
        if len(rows) != 1 or rows[0].qty != 1 or not rows[0].sku_code:
            raise ValueError('本入口仅支持每单一条真实导入子单、数量1，不能猜配或拆合多件')
        line = rows[0]
        if (o.status not in ('paid', 'production') or o.is_refill or o.is_historical
                or (o.platform_field_state or {}).get('shipping_only') or delivery.line_is_refunded(line)
                or Decimal(str(o.paid_amount or 0)) <= 0 or Decimal(str(o.refund_amount or 0)) > 0):
            raise ValueError('已关闭、退款、未付款或非真实销售订单不能登记')
        sku = db.scalar(select(PricingSku).where(PricingSku.sku_code == line.sku_code))
        if sku is None or _placeholder(sku) != (role == 'topup'):
            raise ValueError('主单必须是实物SKU，补差单必须是定制/补差占位SKU')
        from app.services.factory_production_evidence import quantity_confirmation
        quantity = quantity_confirmation(db, o, line)
        if quantity and quantity.get('physical_qty') != 1:
            raise ValueError('已有成品数量确认与本次合为1件冲突，请先核对')
        result[role] = {
            'order_id': o.id, 'order_no': no, 'status': o.status, 'line_id': line.id,
            'sub_order_no': line.sub_order_no, 'sku_code': line.sku_code, 'product_code': line.product_code,
            'qty': line.qty, 'paid_amount': str(o.paid_amount), 'payable_amount': str(o.buyer_payable_amount),
            'refund_amount': str(o.refund_amount), 'refund_status': o.refund_status,
            'line_status': line.line_status, 'line_amount': str(line.amount),
            'line_refund_amount': str(line.refund_amount), 'line_refund_status': line.refund_status,
            'notes_sha256': _hash([getattr(o, k, None) for k in ('remark','seller_memo','buyer_message','production_note')]),
            'customer_sha256': _hash([o.customer_name,o.customer_phone,o.customer_address]),
            'is_custom': bool(o.is_custom), 'standard_size': sku.size_info,
            'placeholder': _placeholder(sku),
            'quantity_confirmation_sha256': _hash(quantity),
        }
    associations = list(db.scalars(select(FactoryOrder).where(FactoryOrder.platform_order_no == topup_order_no)))
    if (len(associations) != 1 or associations[0].factory_cost_type != 'same_order_topup'
            or associations[0].related_primary_order_no != main_order_no or associations[0].voided_at):
        raise ValueError('请先通过现有账务入口把唯一补差行关联到该主单')
    assoc = associations[0]
    result['association'] = {'id': assoc.id, 'factory_cost_type': assoc.factory_cost_type,
        'related_primary_order_no': assoc.related_primary_order_no, 'platform_order_no': assoc.platform_order_no}
    if not result['main']['is_custom']:
        raise ValueError('请先登记主单为本次明确的定制生产单')
    return result


def preview(db, *, main_order_no, topup_order_no, dimensions_mm):
    dims = _dimensions(dimensions_mm)
    identity = _facts(db, main_order_no, topup_order_no)
    return {'identity': identity, 'dimensions_mm': dims,
            'identity_sha256': _hash({'identity': identity, 'dimensions_mm': dims})}


def confirm(db, *, main_order_no, topup_order_no, dimensions_mm, expected_identity_sha256, actor, evidence_ref):
    """01 calls after its audited preparation; does not number, render or send anything."""
    if not actor or not evidence_ref:
        raise ValueError('必须记录明确操作人及用户生产指示依据')
    dims = _dimensions(dimensions_mm)
    identity = _facts(db, main_order_no, topup_order_no, lock=True)
    fingerprint = _hash({'identity': identity, 'dimensions_mm': dims})
    if fingerprint != expected_identity_sha256:
        raise ValueError('订单或关联事实已变化，请重新核对preview')
    from app.services import order_flags
    main = db.get(Order, identity['main']['order_id'])
    if order_flags.is_remote(main):
        raise ValueError('尚未获得开始制作指示，绑定不能解除远期/发货限制')
    for role in ('main', 'topup'):
        line = db.get(OrderDetail, identity[role]['line_id'])
        if (line.factory_delivery_message_id or line.factory_delivery_sent_at
                or line.factory_delivery_state not in (None, '', 'failed')):
            raise ValueError('已有发送或未知回执，不能用此入口重发')
    key = PREFIX + main_order_no
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == key).with_for_update())
    member = db.scalar(select(SystemSetting).where(SystemSetting.key == MEMBER_PREFIX + topup_order_no))
    if member and member.value_plain != main_order_no:
        raise ValueError('该补差单已被另一个主单占用')
    record = {'schema': 'factory-linked-topup-v1', 'identity': identity, 'dimensions_mm': dims,
              'identity_sha256': fingerprint, 'actor': actor, 'evidence_ref': evidence_ref}
    if row:
        old = json.loads(row.value_plain)
        if old['identity']['topup']['order_no'] != topup_order_no:
            raise ValueError('已有不同补差关联，不能覆盖')
        if all(old.get(k) == v for k, v in record.items()):
            return old
        record['history'] = [*(old.get('history') or []), {k:v for k,v in old.items() if k != 'history'}]
        row.value_plain = json.dumps(record, ensure_ascii=False)
    else:
        db.add(SystemSetting(key=key, value_plain=json.dumps(record, ensure_ascii=False), is_secret=False,
            description='精确主/补差单生产及定制尺寸确认，不改变平台交易或财务'))
    if not member:
        db.add(SystemSetting(key=MEMBER_PREFIX+topup_order_no,value_plain=main_order_no,is_secret=False))
    db.commit()
    return record


def assessment(db, order_no):
    row = db.scalar(select(SystemSetting).where(SystemSetting.key == PREFIX + order_no))
    if not row:
        return {'state': 'missing'}
    try:
        record = json.loads(row.value_plain)
        identity = _facts(db, order_no, record['identity']['topup']['order_no'])
        dims = _dimensions(record['dimensions_mm'])
        valid = (record.get('schema') == 'factory-linked-topup-v1' and record.get('actor') and record.get('evidence_ref')
                 and record['identity_sha256'] == _hash({'identity': identity, 'dimensions_mm': dims}))
        return {'state': 'valid' if valid else 'stale', 'record': record}
    except (ValueError, KeyError, TypeError):
        return {'state': 'stale'}


def topup_member(db, order_no):
    return bool(db.scalar(select(SystemSetting.key).where(SystemSetting.key == MEMBER_PREFIX + order_no))
        or db.scalar(select(FactoryOrder.id).where(FactoryOrder.platform_order_no == order_no,
            FactoryOrder.factory_cost_type == 'same_order_topup').limit(1)))


def audit_review_needed(db, order, line):
    """A potentially physical main excluded by the keyword must remain visible to audit."""
    if topup_member(db, order.order_no):
        return False
    state = assessment(db, order.order_no)['state']
    if state == 'stale':
        return True
    from app.services import order_sheet_archive_service as sheets
    sku = db.scalar(select(PricingSku).where(PricingSku.sku_code == line.sku_code))
    return bool(state == 'missing' and not _placeholder(sku)
        and Decimal(str(order.paid_amount or 0)) >= max(Decimal('400'), Decimal(str(sheets._push_min_amount(db))))
        and any(k in ' '.join(str(getattr(order, n, '') or '') for n in ('remark','seller_memo','buyer_message','production_note'))
                for k in sheets._PARTS_TOPUP_KEYWORDS))


def apply_to_sheet(db, order, sheet, line_id=None):
    from app.services.factory_sheet import FactorySheetWarning
    result = assessment(db, order.order_no)
    if result['state'] == 'missing':
        return
    if result['state'] != 'valid':
        sheet.size_info = '本单定制关联已变化，尺寸待重新确认'
        sheet.warnings.append(FactorySheetWarning(code='linked_topup_binding_stale',severity='error',
            message='原定制主/补差事实已变化，禁止退回标准SKU尺寸生产'))
        return
    record = result['record']; identity = record['identity']['main']; dims = record['dimensions_mm']
    if line_id is not None and line_id != identity['line_id']:
        raise ValueError('定制绑定不能套用到其他子单')
    sheet.size_info = f"长{dims['length']} × 深{dims['depth']} × 高{dims['height']} mm"
    sheet.is_custom_variant = True
    sheet.qty = 1
    sheet.dimension_changes = dict(dims)
    sheet.reference_image_notice = '标准款结构/材质参考图，图内尺寸不用于本次生产。本单确认尺寸：'+sheet.size_info
    sheet.production_note = (sheet.production_note or '')+'\n'+f"本主单与补差单{record['identity']['topup']['order_no']}合为1件，仅本主单制作；定制尺寸：{sheet.size_info}。"
    sheet.quantity_confirmation = {'physical_qty': 1, 'actor': record['actor'], 'evidence_ref': record['evidence_ref'],
        'schema': 'factory-linked-topup-v1', 'binding_sha256': record['identity_sha256'],
        'identity': {**identity, 'purchase_qty': identity['qty']},
        'dimensions_mm': dims, 'topup_order_no': record['identity']['topup']['order_no']}
    sheet.purchase_qty = 1
