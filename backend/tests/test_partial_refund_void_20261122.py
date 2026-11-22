from decimal import Decimal
from types import SimpleNamespace
import json

import pytest
from app.models.import_file import ImportedFile
from app.services import order_line_delivery_service as lines
from app.services import order_sheet_archive_service as sheets
from app.services import partial_refund_void_recovery_service as recovery
from app.services import settings_service
from tests.test_order_line_factory_delivery_0812 import _order, _line


@pytest.mark.parametrize('amount,refund,status,full', [
    (2678.17,8.05,None,False), (100,99.99,'退款成功',False),
    (100,100,'退款成功',True), (100,100,None,True),
    (0,100,'退款成功',False), (100,0,'退款成功',False),
    (100,100,'退款处理中',False), (100,100,'退款关闭',False),
])
def test_void_requires_full_amount(amount, refund, status, full):
    assert lines.is_full_refund(amount,refund,status) is full


def test_parent_net_paid_is_not_original_refund_denominator():
    order=SimpleNamespace(paid_amount=50,buyer_payable_amount=100,refund_amount=50,refund_status='退款成功')
    assert not sheets._is_refunded(order)


def seed(db, *, original=True):
    order=_order(db,'REFUND-TEST')
    line=_line(db,order.order_no,order.order_no,'餐桌','PPS2421007090112')
    line.factory_no=718;line.refund_status=None;line.refund_amount=Decimal('8.05')
    line.factory_delivery_required=True;line.factory_delivery_state='sent'
    record=ImportedFile(kind='order_sheet_void',stored_path='/kept/original.jpg',source='line_refund',
        row_summary={'line_id':line.id,'order_no':line.order_no,'sub_order_no':line.sub_order_no,'pushed':True,
                     'delivery_message_id':'old' if original else None})
    db.add(record);settings_service.set_value(db,'feishu_push_chat_id','chat');db.commit()
    return line,record


def remote(monkeypatch, *, fail_send=False):
    messages={'old':{'chat_id':'chat','body':{'content':'original'}}}; calls=[]
    def req(db, method, url, **kwargs):
        calls.append(method)
        if method=='GET':return {'items':[messages[url.rsplit('/',1)[-1]]]}
        if fail_send:raise TimeoutError('unknown send')
        messages['new']={'chat_id':'chat','body':{'content':kwargs['json']['content']}}
        return {'message_id':'new'}
    def patch(db, mid, card):
        messages[mid]={'chat_id':'chat','body':{'content':json.dumps(card,ensure_ascii=False)}}
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY',raising=False)
    monkeypatch.setattr(recovery.feishu_client,'_req',req)
    monkeypatch.setattr(recovery.feishu_client,'patch_card',patch)
    return calls,messages


def test_corrects_original_and_sends_once_without_changing_order(db_session,monkeypatch):
    line,record=seed(db_session);calls,messages=remote(monkeypatch)
    result=recovery.correct_partial_refund_void(db_session,record.id,apply=True)
    assert result['state']=='verified' and result['original_card_updated']
    assert record.kind==recovery.RETRACTED_KIND and record.stored_path=='/kept/original.jpg'
    assert record.row_summary['delivery_message_id']=='old'
    assert line.line_status=='paid' and line.refund_amount==Decimal('8.05') and line.factory_delivery_state=='sent'
    recovery.correct_partial_refund_void(db_session,record.id,apply=True)
    assert calls.count('POST')==1
    assert not lines.void_line_evidence(db_session)


def test_unknown_send_is_never_replayed(db_session,monkeypatch):
    line,record=seed(db_session);calls,_=remote(monkeypatch,fail_send=True)
    with pytest.raises(TimeoutError):recovery.correct_partial_refund_void(db_session,record.id,apply=True)
    with pytest.raises(RuntimeError,match='unknown'):recovery.correct_partial_refund_void(db_session,record.id,apply=True)
    assert calls.count('POST')==1


def test_signed_history_has_no_reproduction_instruction(db_session,monkeypatch):
    line,record=seed(db_session,original=False);line.line_status='signed';db_session.commit()
    calls,messages=remote(monkeypatch)
    result=recovery.correct_partial_refund_void(db_session,record.id,apply=True)
    assert '已签收' in result['text'] and '无需重新制作或发货' in result['text']
    assert calls.count('POST')==1


def test_full_refund_is_not_retracted(db_session,monkeypatch):
    line,record=seed(db_session);line.refund_amount=line.amount;db_session.commit()
    calls,_=remote(monkeypatch)
    with pytest.raises(ValueError):recovery.correct_partial_refund_void(db_session,record.id,apply=True)
    assert not calls and record.kind=='order_sheet_void'


def test_readback_failure_resumes_get_only(db_session,monkeypatch):
    line,record=seed(db_session);calls,messages=remote(monkeypatch)
    original_get=recovery._message
    def get(db,mid):
        if mid=='new':raise TimeoutError('readback failed')
        return original_get(db,mid)
    monkeypatch.setattr(recovery,'_message',get)
    with pytest.raises(TimeoutError):recovery.correct_partial_refund_void(db_session,record.id,apply=True)
    assert record.row_summary[recovery.KEY]['state']=='sent'
    monkeypatch.setattr(recovery,'_message',original_get)
    assert recovery.correct_partial_refund_void(db_session,record.id,apply=True)['state']=='verified'
    assert calls.count('POST')==1


def test_partial_refund_does_not_generate_void_even_without_status(db_session,monkeypatch):
    line,record=seed(db_session)
    record.kind='order_sheet_sent';db_session.commit()
    result=sheets.reconcile_refunded_order_lines(db_session)
    assert result['voided']==0

def test_later_full_refund_proven_by_payment_ledger_is_not_restored(db_session,monkeypatch):
    from app.models.finance import AlipayFlow
    from app.models.order import Order
    from datetime import datetime, timezone, timedelta
    line,record=seed(db_session)
    order=db_session.query(Order).filter_by(order_no=line.order_no).one()
    order.paid_amount=Decimal('2491.95');order.buyer_payable_amount=Decimal('2500');order.refund_amount=Decimal('2491.95')
    assert not sheets._is_refunded(order,db=db_session)  # Amount ambiguity alone is not enough.
    db_session.add_all([
        AlipayFlow(account='test',transaction_no='pay',amount=Decimal('2491.95'),related_order_no=line.order_no,reconciliation_type='customer_payment'),
        AlipayFlow(account='test',transaction_no='refund',amount=Decimal('-2491.95'),related_order_no=line.order_no,reconciliation_type='refund'),
    ])
    db_session.commit()
    assert sheets._is_refunded(order,db=db_session)
    assert lines.line_is_fully_refunded(line,db=db_session)
    calls,_=remote(monkeypatch)
    with pytest.raises(ValueError,match='Later full refund'):
        recovery.correct_partial_refund_void(db_session,record.id,apply=True)
    later=ImportedFile(kind='order_sheet_void',stored_path='/kept/later-full.jpg',source='auto',
        created_at=datetime.now(timezone.utc)+timedelta(days=1),row_summary={'order_no':line.order_no})
    db_session.add(later);db_session.commit()
    result=recovery.correct_partial_refund_void(db_session,record.id,apply=True,historical_only=True)
    assert result['historical_only'] and '不能恢复生产或发货' in result['text']
    assert later.kind=='order_sheet_void' and calls.count('POST')==1


def test_refund_on_different_account_cannot_prove_full_refund(db_session):
    from app.models.finance import AlipayFlow
    order=_order(db_session,'DIFFERENT-ACCOUNT')
    order.paid_amount=50;order.buyer_payable_amount=100;order.refund_amount=50
    db_session.add_all([
        AlipayFlow(account='one',transaction_no='pay2',amount=50,related_order_no=order.order_no,reconciliation_type='customer_payment'),
        AlipayFlow(account='two',transaction_no='refund2',amount=-50,related_order_no=order.order_no,reconciliation_type='refund'),
    ])
    db_session.commit()
    assert not sheets._is_refunded(order,db=db_session)
