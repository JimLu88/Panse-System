"""Explicit production facts; never infer physical quantity from money units."""
import json
from hashlib import sha256
from pathlib import Path
from sqlalchemy import select
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting

DIMENSION_REGISTRY = Path(__file__).parents[1] / 'assets' / 'factory_verified_dimensions.json'


def verified_dimensions(product_code, sku_code):
    from app.services.gallery_lookup import _root
    try:
        record = json.loads(DIMENSION_REGISTRY.read_text('utf-8')).get(sku_code)
        if not record or record.get('product_code') != product_code:
            return None
        root = _root().resolve()
        path = (root / record['path']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return None
        if sha256(path.read_bytes()).hexdigest() != record['sha256']:
            return None
        dimensions = record['dimensions_mm']
        if len(dimensions) != 3 or any(type(n) is not int or n <= 0 for n in dimensions):
            return None
        return '；'.join(f'{label}：{value}mm' for label, value in zip(('长度','深度','高度'), dimensions))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _identity(order, line):
    notes = [order.buyer_message, order.remark, order.seller_memo,
             getattr(order, 'production_note', None)]
    return {'order_no':order.order_no, 'line_id':line.id, 'sub_order_no':line.sub_order_no,
            'sku_code':line.sku_code, 'purchase_qty':line.qty,
            'notes_sha256':sha256(json.dumps(notes,ensure_ascii=False).encode()).hexdigest()}


def quantity_confirmation(db, order, line):
    row=db.scalar(select(SystemSetting).where(SystemSetting.key==f'factory_quantity_confirmation:{line.id}'))
    if not row:
        return None
    try:
        record=json.loads(row.value_plain)
        if record.get('identity') != _identity(order,line):
            return None
        if (record.get('schema')!='factory-quantity-v1' or not record.get('actor')
                or not record.get('evidence_ref') or type(record.get('physical_qty')) is not int
                or record['physical_qty']<=0):
            return None
        return record
    except (ValueError, TypeError):
        return None


def confirm_quantity(db, *, line_id, expected_sku, expected_purchase_qty,
                     physical_qty, actor, evidence_ref):
    """Operator-only: caller must hold explicit user confirmation; no inference."""
    if type(physical_qty) is not int or physical_qty<=0 or not actor or not evidence_ref:
        raise ValueError('缺少明确实物数量、确认人或依据')
    line=db.scalar(select(OrderDetail).where(OrderDetail.id==line_id).with_for_update())
    order=db.scalar(select(Order).where(Order.order_no==line.order_no)) if line else None
    if (not order or line.sku_code!=expected_sku or line.qty!=expected_purchase_qty
            or line.factory_delivery_message_id or line.factory_delivery_state not in (None,'','failed')):
        raise ValueError('订单事实变化或已有发送/未知记录，不能写确认')
    identity=_identity(order,line)
    key=f'factory_quantity_confirmation:{line.id}'
    old=db.scalar(select(SystemSetting).where(SystemSetting.key==key))
    record={'schema':'factory-quantity-v1','identity':identity,'physical_qty':physical_qty,
            'actor':actor,'evidence_ref':evidence_ref}
    if old:
        existing=json.loads(old.value_plain)
        if existing!=record:raise ValueError('已有不同确认，不能静默覆盖')
        return existing
    db.add(SystemSetting(key=key,value_plain=json.dumps(record,ensure_ascii=False),is_secret=False,
                         description='用户明确确认的子单实物数量，不改变购买数量'))
    db.commit()
    return record
