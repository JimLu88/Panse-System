"""Scoped user-confirmed month correction. No platform orders, sync or messages."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from sqlalchemy import select
from app.models.order import Order, OrderDetail
from app.models.import_file import ImportedFile
from app.services import field_change_service, taobao_order_import, order_flags

CONFIRMED = {'3307929459972009052': ('2026-10', 379),
             '3316168239087075694': ('2026-12', 375)}
CONFIRM_SOURCE = '用户直接确认2026-09-09；发货月份，不变更生产'
ARCHIVE_ID = 2876
ARCHIVE_SHA = 'b94c4d5eaa4d10d19da16fad0a134bd662d02be1e2373682d3dbacb00b46cab5'

def _archive_tags(db):
    archive = db.get(ImportedFile, ARCHIVE_ID)
    if archive is None:
        raise ValueError('Confirmed source archive missing')
    path = Path(archive.stored_path).resolve()
    if not path.is_relative_to(Path('/app/storage/imports').resolve()):
        raise ValueError('Archive outside source storage')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ARCHIVE_SHA:
        raise ValueError('Confirmed source archive changed')
    rep = taobao_order_import.TaobaoImportReport()
    parsed = taobao_order_import._parse_sales_detail(archive.original_filename, raw, rep)
    result = {}
    for key in CONFIRMED:
        row = parsed.get(key)
        if row is None or not row.platform_remark_tags_present:
            raise ValueError('Exact source tag absent')
        result[key] = row.platform_remark_tags or ''
    return result

def apply_confirmed_months(db):
    """01 only. Both orders validated before change, one transaction, replay no-op."""
    orders = list(db.scalars(select(Order).where(Order.order_no.in_(CONFIRMED)).with_for_update()))
    if len(orders) != len(CONFIRMED):
        raise ValueError('Exact confirmed parent scope not found')
    for order in orders:
        month, number = CONFIRMED[order.order_no]
        lines = list(db.scalars(select(OrderDetail).where(
            OrderDetail.order_no == order.order_no, OrderDetail.source == 'import')))
        if len(lines) != 1 or lines[0].sub_order_no != order.order_no or lines[0].factory_no != number:
            raise ValueError('Confirmed parent-child-factory identity changed')
        if lines[0].line_status not in ('paid', 'production') or order.status not in ('paid', 'production'):
            raise ValueError('Order lifecycle changed; do not override terminal/aftersales')
        if order.factory_no != number or any(k in order_flags.order_text(order) for k in ("暂停生产", "停止生产", "暂不制作", "暂不生产", "先不做")):
            raise ValueError('Existing production identity/permission conflict')
        if order.customer_shipping_month not in (None, '', month):
            raise ValueError('Conflicting human month; do not overwrite')
        if order.is_remote_ship or order.customer_delay_deadline is not None:
            raise ValueError('Existing shipping instruction conflict; preserve')
    tags = _archive_tags(db) if any(o.platform_remark_tags is None for o in orders) else {}
    changed = []
    for order in orders:
        month, number = CONFIRMED[order.order_no]
        updates = {'customer_shipping_month':month, 'customer_shipping_month_source':CONFIRM_SOURCE,
                   'is_customer_delayed':True,'customer_shipping_preserve_production':True}
        if order.customer_shipping_month_confirmed_at is None:
            updates['customer_shipping_month_confirmed_at'] = datetime.now(timezone.utc)
        # Never overwrite a newer imported tag, including an explicit empty value.
        if order.platform_remark_tags is None:
            updates.update(platform_remark_tags=tags[order.order_no],
                platform_remark_tags_source='淘宝归档2876：备注标签 sha256:' + ARCHIVE_SHA,
                platform_remark_tags_updated_at=datetime.now(timezone.utc))
        changed_fields=[]
        for field,value in updates.items():
            old=getattr(order,field)
            if old == value: continue
            field_change_service.record(db,table='orders',pk=order.order_no,field=field,
                old=old,new=value,actor='用户确认（01执行）',source='web',
                field_label='人工发货月份' if field.startswith('customer_shipping') else field)
            setattr(order,field,value)
            changed_fields.append(field)
        changed.append({'factory_no':number,'month':month,'changed_fields':changed_fields})
    db.commit()
    return {'ok':True,'orders':changed,'changed_orders':sum(bool(x['changed_fields']) for x in changed),
            'sync_called':False,'messages_sent':False}

def read_confirmed_months(db):
    from app.services import factory_dispatch_feishu_service as fd
    from app.services import feishu_client as fc
    app,table=fd._target(db)
    if table != fd.DEFAULT_TABLE_ID: raise ValueError('Wrong table')
    records=fc.list_records(db,app,table)
    result=[]
    for order in db.scalars(select(Order).where(Order.order_no.in_(CONFIRMED))):
        month,number=CONFIRMED[order.order_no]
        matches=[r.get('fields',{}) for r in records
                 if fd._norm(r.get('fields',{}).get('订单号'))==order.order_no
                 and fd._norm(r.get('fields',{}).get('子订单号'))==order.order_no]
        expected_note=fd._order_notes(order)
        expected_plan=fd._ship_plan(order,remote=order_flags.is_remote(order),photo_requested=fd._photo_requested(order))
        result.append({'factory_no':number,'confirmed_month':order.customer_shipping_month,
            'confirmation_source':order.customer_shipping_month_source,
            'is_customer_delayed':order.is_customer_delayed,'day_deadline':str(order.customer_delay_deadline) if order.customer_delay_deadline else None,
            'effective_deadline':order_flags.factory_schedule(order)['effective_deadline'],
            'platform_tags':order.platform_remark_tags,'platform_tags_source':order.platform_remark_tags_source,
            'production_remote':order_flags.is_remote(order),'expected_ship_plan':expected_plan,
            'remote_exact_count':len(matches),
            'remote':[{k:fd._norm(r.get(k)) for k in ('工厂下单号','订单状态','客户延期单','预计发货日期','发货安排','订单提醒','订单备注')} for r in matches],
            'readback_ok':bool(order.customer_shipping_month==month and len(matches)==1
                and fd._equivalent(matches[0].get('订单备注'),expected_note)
                and fd._equivalent(matches[0].get('发货安排'),expected_plan)
                and not matches[0].get('预计发货日期') and matches[0].get('客户延期单') is True)})
    return {'read_only':True,'orders':result,'all_confirmed':len(result)==2 and all(x['readback_ok'] for x in result)}
