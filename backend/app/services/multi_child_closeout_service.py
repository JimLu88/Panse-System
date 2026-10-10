"""One reviewed closeout: proven derived costs, cache and visible unresolved facts.

No order imports, factory messages, bill modifications, quantity assumptions or
period reopening. The factory table has its own exact-target synchronization.
"""
import json
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from sqlalchemy import select, text, func
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.models.exception import DataException
from app.models.sales_rollup import SalesDailyRollup
from app.services import order_completeness_incident as incident
from app.services import sales_rollup_service as rollup, accounting_period_service as periods

KEY = 'multi-child-closeout-20260921'
PROTECTED = ('paid_amount','refund_amount','actual_cost','actual_parts','actual_freight',
             'install_fee','upstairs_fee','compensation_fee','status')


def prepare(db):
    plan = incident.all_product_financial_plan(db)
    changes, unresolved = [], list(plan['unresolved'])
    for item in plan['changes']:
        order=db.scalar(select(Order).where(Order.order_no==item['order_no']))
        if order.order_date is None or not periods.is_writable(db,order.order_date):
            unresolved.append({'order_no':order.order_no,'reason':'accounting_period_not_writable'})
            continue
        changes.append({**item,'protected':{k:str(getattr(order,k)) for k in PROTECTED}})
    # Rebuild only the current-year derived cache, not source history. This also
    # removes stale cache rows for orders whose status/date has since changed.
    today=date.today()
    first=db.scalar(select(func.min(SalesDailyRollup.day)).where(SalesDailyRollup.day>=date(today.year,1,1)))
    start=first or date(today.year,1,1)
    days=[start+timedelta(days=i) for i in range((today-start).days+1)]
    result={'changes':changes,'unresolved':unresolved,'cache_start':str(start),'cache_end':str(today),
            'source_fingerprints':{str(k):v for k,v in rollup._source_fingerprints(db,days).items()}}
    result['plan_sha256']=sha256(json.dumps(result,sort_keys=True,default=str).encode()).hexdigest()
    return result


def apply(db, expected_hash):
    prior=db.scalar(select(SystemSetting).where(SystemSetting.key==KEY))
    if prior:
        return {**json.loads(prior.value_plain),'existing_receipt':True}
    if db.get_bind().dialect.name=='postgresql':
        if not db.scalar(text('SELECT pg_try_advisory_xact_lock(2026092101)')):
            raise ValueError('closeout_busy')
    # Lock source rows before the exact plan comparison, so neither pricing nor
    # new child facts can silently alter this reviewed repair inside the batch.
    list(db.scalars(select(Order).with_for_update()))
    list(db.scalars(select(OrderDetail).where(OrderDetail.source=='import').with_for_update()))
    plan=prepare(db)
    if plan['plan_sha256'] != expected_hash:
        raise ValueError('source_changed_read_only_review_required')
    before={o.order_no:{k:str(getattr(o,k)) for k in PROTECTED} for o in db.scalars(select(Order))}
    try:
        for item in plan['changes']:
            order=db.scalar(select(Order).where(Order.order_no==item['order_no']))
            periods.ensure_writable(db,order.order_date)
            for key,value in item['after'].items():
                if key not in {'theoretical_cost','wood_cost_est','est_parts'}:
                    raise ValueError('forbidden_financial_field')
                setattr(order,key,Decimal(value))
        exception_type='multi_child_financial_source_unverified'
        old={e.source_pk:e for e in db.scalars(select(DataException).where(DataException.exception_type==exception_type))}
        unresolved={r['order_no']:r for r in plan['unresolved']}
        for number,item in unresolved.items():
            entry=old.get(number)
            if entry is None:
                entry=DataException(source_table='orders',source_pk=number,exception_type=exception_type,
                    severity='warning',status='open',description='多子订单财务来源待核实；不是已确认漏发。')
                db.add(entry)
            entry.context={'reason':item['reason'],'actual_bills_changed':False}
            entry.suggestion_action='核对原始购买明细、精确SKU和成本依据；禁止用母SKU代替全部商品'
        for number,entry in old.items():
            if number not in unresolved and entry.status=='open':entry.status='resolved'
        db.flush()
        cache=rollup.rollup_range(db,date.fromisoformat(plan['cache_start']),date.fromisoformat(plan['cache_end']))
        after={o.order_no:{k:str(getattr(o,k)) for k in PROTECTED} for o in db.scalars(select(Order))}
        if before!=after:raise ValueError('protected_financial_fields_changed')
        for item in plan['changes']:
            order=db.scalar(select(Order).where(Order.order_no==item['order_no']))
            if any(Decimal(str(getattr(order,k)))!=Decimal(v) for k,v in item['after'].items()):
                raise ValueError('derived_cost_readback_mismatch')
        result={'ok':True,'plan_sha256':expected_hash,'changes':plan['changes'],
                'unresolved':plan['unresolved'],'cache':cache,'actual_bills_changed':0,
                'order_imports':0,'factory_messages_sent':0}
        db.add(SystemSetting(key=KEY,value_plain=json.dumps(result,ensure_ascii=False),is_secret=False,
            description='多子订单派生财务和销售缓存收口回执'))
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
