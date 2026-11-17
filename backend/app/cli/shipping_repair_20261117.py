"""One bounded protection of five shipping-only placeholders; no import/send/delete."""
import argparse
import hashlib
import json
from sqlalchemy import select
from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.services import import_storage, settings_service, taobao_order_import as imp

SOURCE_ID = 3963
SOURCE_HASH = '900ee9eee958a6fb5c776261768003c101d80332ff805b0d3b600188c9219ea2'
ORDERS = ['3316569963241107867','3317204784012026783','5127600782603038217',
          '3317193084231034374','3316573959248034869']
PROTECTED = {840:'om_x100b60984683808cb4c5d9273eb075b',841:'om_x100b608851e52c88b08b9f89fabf926'}
KEY = 'shipping_placeholder_repair:20261117:3963'


def repair(db, *, apply=False):
    old = db.scalar(select(SystemSetting).where(SystemSetting.key == KEY))
    if old:
        return {'existing': True, **json.loads(old.value_plain)}
    source = db.get(ImportedFile, SOURCE_ID)
    if not source or source.file_hash != SOURCE_HASH or (source.row_summary or {}).get('agent_status') != 'imported':
        raise ValueError('source_changed')
    raw = import_storage.read(source.stored_path)
    if hashlib.sha256(raw).hexdigest() != SOURCE_HASH:
        raise ValueError('source_hash_changed')
    raw = imp.maybe_decrypt(raw, settings_service.get(db, 'taobao_shipping_pwd_latest', env_fallback=False))
    parsed = imp._parse_sales_detail(source.original_filename, raw, imp.TaobaoImportReport())
    protected = list(db.scalars(select(OrderDetail).where(OrderDetail.factory_no.in_(PROTECTED)).with_for_update()))
    if len(protected) != 2 or any(x.factory_delivery_state != 'sent' or x.factory_delivery_message_id != PROTECTED[x.factory_no] for x in protected):
        raise ValueError('sent_evidence_changed')
    changes=[]; targets=[]
    for no in ORDERS:
        source_order=parsed.get(no)
        if (not source_order or len(source_order.lines)!=1 or source_order.lines[0].get('explicit_child')
                or source_order.lines[0].get('qty') not in (None,'')):
            raise ValueError('source_purchase_facts_changed')
        order=db.scalar(select(Order).where(Order.order_no==no).with_for_update())
        children=list(db.scalars(select(OrderDetail).where(OrderDetail.order_no==no).with_for_update()))
        if not order or len(children)!=1:raise ValueError('order_lines_changed')
        line=children[0]
        if (line.source!='import' or line.sub_order_no!=no or line.qty!=1 or order.qty!=1
                or line.sku_code or line.sku_name or line.factory_delivery_state or line.factory_delivery_message_id
                or line.factory_delivery_sent_at or line.factory_no):
            raise ValueError('placeholder_changed_no_overwrite')
        sku=source_order.lines[0].get('sku')
        if not sku or order.sku:raise ValueError('sku_conflict')
        changes.append({'order_no':no,'line_id':line.id,'before':{'qty':1,'source':'import','sku':None,
                        'factory_delivery_required':line.factory_delivery_required},
                        'after':{'qty':0,'source':'shipping_placeholder','sku':sku,'factory_delivery_required':False}})
        targets.append((order,line,sku))
    result={'source_file_id':SOURCE_ID,'source_sha256':SOURCE_HASH,'changes':changes,
            'protected_sent':PROTECTED,'applied':apply,'imported':False,'sent':False,'deleted':False}
    if not apply:
        db.rollback();return result
    for order,line,sku in targets:
        order.qty=0;order.sku=sku
        order.platform_field_state={**(order.platform_field_state or {}),
            'shipping_only':{'state':'missing_sales_detail','source_file_id':SOURCE_ID,'sha256':SOURCE_HASH}}
        line.qty=0;line.sku_name=sku;line.source='shipping_placeholder';line.factory_delivery_required=False
        line.factory_delivery_error='仅发货资料，缺真实子单及购买数量；禁止据此生产'
    db.add(SystemSetting(key=KEY,value_plain=json.dumps(result,ensure_ascii=False),is_secret=False))
    db.commit()
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    from app.database import SessionLocal
    with SessionLocal() as db:print(json.dumps(repair(db,apply=args.apply),ensure_ascii=False))


if __name__=='__main__':main()
