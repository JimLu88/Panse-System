from decimal import Decimal
from datetime import date
from types import SimpleNamespace

import pytest

from app.models.finance import AlipayFlow
from app.models.order import Order
from app.models.marketing import AfterSales
from app.services import customer_refund_remark as parser
from app.services import aftersales_payment_link_service as ledger
from app.services import alipay_flow_router_service as router
from app.services import alipay_import, smart_matching_service as matching

NO = '3316520247104008964'
OTHER = '3316520247104008965'


def data(remark, amount=-938, related=None, provided=None):
    return SimpleNamespace(remark=remark, amount=amount, related_order_no=related,
                           platform_order_no=provided, transaction_type='转账')


@pytest.mark.parametrize('word', ['退差价', '差价', '差价补偿', '双十一价保', '售后退款', '退款', '售后赔偿', '补偿', '退钱'])
@pytest.mark.parametrize('template', ['{no}订单{word}938元', '{word}{no}订单938元', '{word}938元订单{no}'])
def test_keywords_and_order_positions(word, template):
    result = parser.resolve(data(template.format(no=NO, word=word)))
    assert result.order_no == NO and result.issue is None


@pytest.mark.parametrize('remark,related', [
    (f'{NO}退差价100元', None),
    (f'旧订单{NO}新订单{OTHER}差价938元', None),
    (f'{NO}差价938元', OTHER),
    ('20261021220014000003316520247104008964退款938元', None),
])
def test_ambiguous_or_mismatched_does_not_bind(remark, related):
    result = parser.resolve(data(remark, related=related))
    assert result.order_no is None and result.issue


def seed(db, remark, *, status='signed', amount=-938):
    order = Order(platform='淘宝', order_no=NO, customer_name='测试客户',
                  status=status, order_date=date(2026, 10, 1), paid_amount=5000, refund_amount=0)
    db.add(order)
    csv = '交易时间,交易流水号,交易类型,交易对象,收支金额,备注\n2026-10-21 10:00:00,TEST-REFUND,转账,测试客户,'+str(amount)+','+remark+'\n'
    alipay_import.import_alipay_csv(db, csv, account='主力号', commit=False)
    db.flush()
    return order, db.query(AlipayFlow).one()


@pytest.mark.parametrize('status', ['signed', 'cancelled'])
def test_real_import_to_ledger_and_idempotent_accounting(db_session, status):
    order, flow = seed(db_session, f'{NO}订单退差价938元', status=status)
    assert flow.platform_order_no == NO
    assert flow.related_order_no is None
    matching.run(db_session)
    assert flow.reconciliation_type == 'refund'  # not factory/wood purchase
    assert router.create_aftersales_from_flows(db_session) == 0  # ledger is sole owner
    result = ledger.persist_scan(db_session, auto_confirm_safe=True)
    assert result['confirmed'] == 1
    row = db_session.query(AfterSales).one()
    assert row.platform_order_no == NO
    assert row.out_platform_total == Decimal('938.00')
    assert row.good_review_refund == Decimal('938.00')
    assert flow.reconciliation_type == 'aftersales'
    matching.run(db_session)
    assert flow.reconciliation_type == 'aftersales'  # no oscillation after managed confirmation
    assert ledger.persist_scan(db_session, auto_confirm_safe=True)['created'] == 0
    assert router.create_aftersales_from_flows(db_session) == 0
    assert db_session.query(AfterSales).count() == 1
    assert order.status == status and order.refund_amount == 0
    assert flow.amount == Decimal('-938') and flow.remark == f'{NO}订单退差价938元'


@pytest.mark.parametrize('remark', [f'{NO}退差价100元', f'{NO}和{OTHER}差价938元'])
def test_exception_remains_proposed_without_creating_aftersales(db_session, remark):
    _, flow = seed(db_session, remark)
    matching.run(db_session)
    assert router.create_aftersales_from_flows(db_session) == 0
    result = ledger.persist_scan(db_session, auto_confirm_safe=True)
    assert result['created'] == 1 and result['confirmed'] == 0
    assert db_session.query(AfterSales).count() == 0


def test_positive_transfer_does_not_become_customer_expense(db_session):
    _, flow = seed(db_session, f'{NO}退差价938元', amount=938)
    assert parser.resolve(flow).category is None
    assert ledger.persist_scan(db_session, auto_confirm_safe=True)['created'] == 0


def test_changed_evidence_rechecked_before_auto_confirmation(db_session):
    _, flow = seed(db_session, f'{NO}退差价938元')
    scan = ledger.persist_scan(db_session)
    flow.remark = f'{NO}退差价100元'
    with pytest.raises(ValueError, match='重新核对'):
        ledger.confirm(db_session, scan['link_ids'][0], expected_version=1, actor='test', auto=True)
    assert db_session.query(AfterSales).count() == 0


@pytest.mark.parametrize('word', ['售后退款', '退钱', '售后赔偿', '补偿'])
def test_refund_and_compensation_reach_existing_category(db_session, word):
    _, flow = seed(db_session, f'{word}938元订单{NO}')
    result = ledger.persist_scan(db_session, auto_confirm_safe=True)
    assert result['confirmed'] == 1
    row = db_session.query(AfterSales).one()
    assert row.direct_compensation == Decimal('938')
    assert row.platform_order_no == NO


def test_excel_import_enriches_order_without_overwriting_reference(db_session):
    import io
    import openpyxl
    from app.services import excel_importer
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(['交易流水号', '交易类型', '收支金额', '备注', '关联订单号'])
    sheet.append(['XLSX-REFUND', '支出', 938, f'双十一差价{NO}补偿938元', 'P-payment-reference'])
    buf = io.BytesIO(); book.save(buf)
    report = excel_importer.commit_sheet(db_session, file_bytes=buf.getvalue(), sheet_name=sheet.title,
        entity_type='alipay_flow', sheet_account='主力号', mapping={
        'transaction_no':'交易流水号', 'transaction_type':'交易类型', 'amount':'收支金额',
        'remark':'备注', 'related_order_no':'关联订单号'})
    assert report.inserted_parents == 1, report.errors
    flow = db_session.query(AlipayFlow).one()
    assert flow.platform_order_no == NO
    assert flow.related_order_no == 'P-payment-reference'
    assert flow.amount == Decimal('-938')


def test_manual_classification_is_preserved(db_session):
    from app.services import field_change_service
    _, flow = seed(db_session, f'{NO}退差价938元')
    flow.reconciliation_type = 'other'
    field_change_service.record(db_session, table='alipay_flows', pk=flow.id,
                               field='reconciliation_type', old=None, new='other', source='web')
    db_session.flush()
    matching.run(db_session)
    assert flow.reconciliation_type == 'other'
    assert ledger.persist_scan(db_session, auto_confirm_safe=True)['confirmed'] == 0
    assert db_session.query(AfterSales).count() == 0


def test_other_account_uses_exact_order_and_real_amount(db_session):
    _, flow = seed(db_session, f'退差价938元订单{NO}')
    flow.account = '企业号'
    db_session.flush()
    matching.run(db_session)
    assert router.create_aftersales_from_flows(db_session) == 1
    assert router.create_aftersales_from_flows(db_session) == 0
    row = db_session.query(AfterSales).one()
    assert row.platform_order_no == NO and row.out_platform_total == Decimal('938')
