"""Audited repair of 11 historical multi-child financial warnings.

No order import, factory delivery, bill update, notification, or change to
payment/refund/actual-cost fields.  Only archived child identities, derived
costs, and the previously blank honey-beech cost snapshot are corrected.
"""
import json
from datetime import date
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import select, text

from app.models.exception import DataException
from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail
from app.models.pricing import PricingSku
from app.models.pricing_version import PricingSkuVersion
from app.models.settings import SystemSetting
from app.services import (accounting_period_service as periods,
                          import_storage, order_completeness_incident as incident,
                          order_cost_service as costs, sales_rollup_service as rollup)
from app.services.order_line_delivery_service import line_is_refunded


KEY = 'multi-child-historical-finance-20260926'
HONEY = 'PPS2398001060614'
BEECH = 'PPS2398001060611'
TARGETS = frozenset({
    '4502316494125276727', '4502177316184015146',
    '5115783241340027720', '5115237121779012546',
    '2701793318056034064', '2701788495053166684',
    '4991917070620599318', '3304028352501005156',
    '3302834235192039473', '3299694026699015351',
    '5112323136333117949',
})
DERIVED = ('theoretical_cost', 'wood_cost_est', 'est_parts')
PROTECTED = ('paid_amount', 'refund_amount', 'actual_cost', 'actual_parts',
             'actual_freight', 'install_fee', 'upstairs_fee', 'compensation_fee',
             'status', 'qty')
COST_FIELDS = ('physical_cost', 'factory_cost', 'wood_cost', 'packaging_cost',
               'logistics_cost', 'install_cost', 'external_parts_cost')
LINE_FIELDS = ('sync_key', 'sub_order_no', 'sku_code', 'product_code', 'sku_name')


def _money(value):
    return str(Decimal(str(value)).quantize(Decimal('0.01')))


def _version_plan(db):
    old = db.scalar(select(PricingSkuVersion).where(
        PricingSkuVersion.id == 294, PricingSkuVersion.sku_code == HONEY))
    honey = db.scalar(select(PricingSku).where(PricingSku.sku_code == HONEY))
    beech = db.scalar(select(PricingSku).where(PricingSku.sku_code == BEECH))
    if (old is None or honey is None or beech is None or
            old.period_start != date(2000, 1, 1) or
            old.period_end != date(2026, 9, 21) or
            '同成本' not in (old.note or '')):
        raise ValueError('蜜蜡色榉木历史快照证据已变化')
    if any(getattr(old, field) is not None for field in COST_FIELDS):
        raise ValueError('蜜蜡色榉木旧成本不再为空')
    if any(getattr(honey, field) != getattr(beech, field) for field in COST_FIELDS):
        raise ValueError('蜜蜡色榉木现价不再与原木色同成本')
    return {'id': old.id, 'before': {f: None for f in COST_FIELDS},
            'after': {f: _money(getattr(honey, f)) for f in COST_FIELDS},
            'old_note': old.note, 'old_snapshot_sha256': sha256((old.snapshot or '').encode()).hexdigest()}


def _archive_facts(db, order_no, cache):
    ids = [incident.SOURCE_ID, incident.FINANCE_OLD_SOURCES[order_no]]
    for file_id in ids:
        if file_id not in cache:
            file = db.get(ImportedFile, file_id)
            if file is None:
                raise ValueError(f'原始明细档案 {file_id} 不存在')
            raw = import_storage.read(file.stored_path)
            expected = (incident.SOURCE_HASH if file_id == incident.SOURCE_ID
                        else incident.FINANCE_OLD_HASHES[file_id])
            if sha256(raw).hexdigest() != expected:
                raise ValueError(f'原始明细档案 {file_id} 哈希不符')
            report = incident.imp.TaobaoImportReport()
            parsed = incident.imp._parse_sales_detail(file.original_filename, raw, report)
            if report.errors:
                raise ValueError(f'原始明细档案 {file_id} 解析错误')
            cache[file_id] = parsed
        facts = cache[file_id].get(order_no)
        if facts is not None:
            return file_id, facts
    raise ValueError(f'{order_no} 缺原始购买明细')


def _active_facts(facts):
    active = []
    for fact in facts.lines:
        candidate = OrderDetail(
            line_status=incident.imp._map_status(fact.get('status_text')),
            refund_status=fact.get('refund_status'), refund_amount=fact.get('refund'))
        if not incident.imp._is_service_line_name(fact.get('product_name')) and not line_is_refunded(candidate):
            active.append(fact)
    subs = [str(f.get('sub_order_no') or '') for f in active]
    if any(not sub for sub in subs) or len(set(subs)) != len(subs):
        raise ValueError('原始购买子单号缺失或重复')
    return active


def _exact_price(db, fact):
    title = str(fact.get('product_name') or '').strip()
    matches = [price for price in db.scalars(select(PricingSku).where(PricingSku.taobao_title == title))
               if incident._sales_fact_matches_pricing(db, fact, price)]
    if len(matches) != 1:
        raise ValueError(f'来源规格无唯一计价 SKU：{title}/{fact.get("sku")}')
    price = matches[0]
    if price.physical_cost is None or price.wood_cost is None or price.external_parts_cost is None:
        raise ValueError(f'{price.sku_code} 成本字段缺失')
    return price


def _asof_value(db, price, on, field):
    versions = list(db.scalars(select(PricingSkuVersion).where(
        PricingSkuVersion.sku_code == price.sku_code,
        PricingSkuVersion.period_start <= on,
        PricingSkuVersion.period_end > on)))
    if len(versions) > 1:
        raise ValueError(f'{price.sku_code} 历史计价区间重叠')
    if versions:
        value = getattr(versions[0], field)
        if value is None and price.sku_code == HONEY and versions[0].id == 294:
            return Decimal(str(getattr(price, field)))
        if value is None:
            raise ValueError(f'{price.sku_code} 历史 {field} 为空')
        return Decimal(str(value))
    return Decimal(str(getattr(price, field)))


def _line_before(row):
    return {field: getattr(row, field) for field in LINE_FIELDS}


def prepare(db):
    """Read-only reviewed plan; every source archive is hash pinned."""
    version = _version_plan(db)
    cache = {}
    issues = {e.source_pk: e for e in db.scalars(select(DataException).where(
        DataException.exception_type == 'multi_child_financial_source_unverified',
        DataException.status == 'open'))}
    if set(issues) != TARGETS:
        raise ValueError(f'待核异常集合变化：{sorted(set(issues) ^ TARGETS)}')
    orders = []
    for order_no in sorted(TARGETS):
        order = db.scalar(select(Order).where(Order.order_no == order_no))
        if (order is None or order.is_refill or order.is_custom or
                order.status not in ('paid', 'production', 'shipped', 'signed') or
                not periods.is_writable(db, order.order_date)):
            raise ValueError(f'{order_no} 订单状态或会计期间变化')
        file_id, facts = _archive_facts(db, order_no, cache)
        active = _active_facts(facts)
        purchase_lines, multiple = costs._purchase_lines_for_cost(db, order)
        if not multiple or len(purchase_lines) != len(active):
            raise ValueError(f'{order_no} ERP 与来源购买行数量不符')
        by_sub = {str(row.sub_order_no): row for row in purchase_lines if row.sub_order_no}
        legacy = all(row.sub_order_no is None for row in purchase_lines)
        if not legacy and len(by_sub) != len(active):
            raise ValueError(f'{order_no} 子行新旧身份混杂')
        changes = []
        totals = {field: Decimal('0') for field in ('physical_cost', 'wood_cost', 'external_parts_cost')}
        for index, fact in enumerate(active):
            sub = str(fact['sub_order_no'])
            row = (next((r for r in purchase_lines if r.sync_key == f'line:{order_no}:{index}'), None)
                   if legacy else by_sub.get(sub))
            if row is None or row.source != 'import' or row.factory_delivery_required:
                raise ValueError(f'{order_no}/{sub} 历史子行或工厂推送状态变化')
            amount = Decimal(str(fact.get('amount') or 0))
            quantity = int(fact.get('qty') or 0)
            if (row.qty != quantity or row.amount != amount or
                    row.product_name != fact.get('product_name') or
                    (row.sku_code not in (None, '', fact.get('sku_code')) and
                     row.sku_code != _exact_price(db, fact).sku_code)):
                raise ValueError(f'{order_no}/{sub} 数量、金额、产品或旧编码冲突')
            price = _exact_price(db, fact)
            if db.scalar(select(OrderDetail.id).where(OrderDetail.sub_order_no == sub,
                                                 OrderDetail.id != row.id)) is not None:
                raise ValueError(f'{sub} 已被其他 ERP 子行占用')
            after = {'sync_key': f'line:{sub}', 'sub_order_no': sub,
                     'sku_code': price.sku_code, 'product_code': price.product_code,
                     'sku_name': str(fact.get('sku') or '').strip()}
            before = _line_before(row)
            if not legacy and (before['sync_key'] != after['sync_key'] or
                               before['sub_order_no'] != sub or before['sku_name'] != after['sku_name']):
                raise ValueError(f'{sub} 已有子行身份与来源不符')
            for field in totals:
                totals[field] += _asof_value(db, price, order.order_date, field) * quantity
            changes.append({'id': row.id, 'before': before, 'after': after,
                            'source_sub': sub, 'source_sku': fact.get('sku'),
                            'qty': quantity, 'amount': _money(amount)})
        if totals['physical_cost'] <= 0 or totals['physical_cost'] > Decimal(str(order.paid_amount)) * Decimal('1.1'):
            raise ValueError(f'{order_no} 物理成本未通过实付护栏')
        after_costs = {
            'theoretical_cost': _money(totals['physical_cost']),
            'wood_cost_est': _money(totals['wood_cost']),
            'est_parts': _money(totals['external_parts_cost']),
        }
        before_costs = {field: _money(getattr(order, field)) if getattr(order, field) is not None else None
                        for field in DERIVED}
        orders.append({'order_no': order_no, 'date': str(order.order_date), 'source_file_id': file_id,
                       'exception_id': issues[order_no].id, 'legacy': legacy,
                       'protected': {field: str(getattr(order, field)) for field in PROTECTED},
                       'line_changes': changes, 'before': before_costs, 'after': after_costs})
    result = {'orders': orders, 'honey_version': version, 'source_hashes': {
        str(file_id): (incident.SOURCE_HASH if file_id == incident.SOURCE_ID
                       else incident.FINANCE_OLD_HASHES[file_id]) for file_id in sorted(cache)}}
    result['plan_sha256'] = sha256(json.dumps(result, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return result


def apply(db, expected_hash, *, commit=True):
    """One transaction, one receipt, exact-plan guard, no external side effects.

    With commit=False, the caller must roll back the session after inspecting
    the result.  This exercises every write and verification without persisting.
    """
    old = db.scalar(select(SystemSetting).where(SystemSetting.key == KEY))
    if old:
        return {**json.loads(old.value_plain), 'existing_receipt': True}
    if db.get_bind().dialect.name == 'postgresql':
        if not db.scalar(text('SELECT pg_try_advisory_xact_lock(2026092601)')):
            raise ValueError('historical_finance_repair_busy')
    list(db.scalars(select(Order).where(Order.order_no.in_(TARGETS)).with_for_update()))
    list(db.scalars(select(OrderDetail).where(OrderDetail.order_no.in_(TARGETS),
                                            OrderDetail.source == 'import').with_for_update()))
    db.scalar(select(PricingSkuVersion).where(PricingSkuVersion.id == 294).with_for_update())
    plan = prepare(db)
    if plan['plan_sha256'] != expected_hash:
        raise ValueError('reviewed_plan_changed')
    version = db.get(PricingSkuVersion, 294)
    for field, value in plan['honey_version']['after'].items():
        setattr(version, field, Decimal(value))
    version.note = (plan['honey_version']['old_note'] +
                    '；2026-09-26 按已确认同成本追溯补齐；原空值 snapshot 保留作审计')
    for item in plan['orders']:
        order = db.scalar(select(Order).where(Order.order_no == item['order_no']))
        for change in item['line_changes']:
            row = db.get(OrderDetail, change['id'])
            if _line_before(row) != change['before']:
                raise ValueError(f'{row.id} 子行在预演后变化')
            for field, value in change['after'].items():
                setattr(row, field, value)
        for field, value in item['after'].items():
            setattr(order, field, Decimal(value))
    db.flush()
    for item in plan['orders']:
        order = db.scalar(select(Order).where(Order.order_no == item['order_no']))
        if any(str(getattr(order, field)) != item['protected'][field] for field in PROTECTED):
            raise ValueError(f'{order.order_no} 受保护订单字段变化')
        actual = {'theoretical_cost': costs._multi_product_cost(db, order),
                  'wood_cost_est': costs._multi_product_wood(db, order),
                  'est_parts': costs._multi_product_parts(db, order)}
        if {key: _money(value) for key, value in actual.items()} != item['after']:
            raise ValueError(f'{order.order_no} 成本引擎回读与预演不符')
    recheck = incident.all_product_financial_plan(db)
    remaining = {r['order_no']: r['reason'] for r in recheck['unresolved'] if r['order_no'] in TARGETS}
    if remaining:
        raise ValueError(f'修复后仍有未解决异常：{remaining}')
    for item in plan['orders']:
        issue = db.get(DataException, item['exception_id'])
        if issue.status != 'open' or issue.source_pk != item['order_no']:
            raise ValueError(f'{item["order_no"]} 异常状态并发变化')
        issue.status = 'resolved'
    cache_days = sorted({date.fromisoformat(item['date']) for item in plan['orders']})
    cache_rows = {str(day): rollup.rollup_day(db, day) for day in cache_days}
    receipt = {'ok': True, 'plan_sha256': expected_hash, 'orders': plan['orders'],
               'honey_version': plan['honey_version'], 'cache_rows': cache_rows,
               'new_production_orders': 0, 'factory_messages_sent': 0,
               'actual_bills_changed': 0}
    db.add(SystemSetting(key=KEY, value_plain=json.dumps(receipt, ensure_ascii=False),
                         is_secret=False, description='11单旧子行身份与派生财务修复回执'))
    db.flush()
    if commit:
        db.commit()
    return receipt
