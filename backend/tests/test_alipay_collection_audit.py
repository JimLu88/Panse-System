from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from app.services import alipay_collection_audit as a
from app.services import notify_service, settings_service


def bill(start="2026-06-30 00:00:00", end="2026-07-02 00:00:01", rows=""):
    return f"支付宝交易记录明细查询\n起始时间：{start} 终止时间：{end}\n交易号,交易创建时间,类型,金额（元）,收/支\n{rows}"


def test_zero_transaction_days_are_covered():
    r=a.reconcile([('1',bill())],[],end=date(2026,7,1))
    assert r['ok'] and not r['uncovered_days']


def test_source_missing_in_database_and_money_mismatch():
    body=bill(rows='123,2026-06-30 12:00:00,支付,20,支出')
    r=a.reconcile([('1',body)],[('123','支付',Decimal('20'),date(2026,6,30))],end=date(2026,7,1))
    assert not r['ok'] and r['missing_rows']==1


def test_overlapping_source_dedup_and_type_alias():
    body=bill(rows='123,2026-06-30 12:00:00,交易分账,20,支出')
    r=a.reconcile([('1',body),('2',body)],[('123','分账',-20,date(2026,6,30))],end=date(2026,7,1))
    assert r['ok'] and r['source_rows']==1


def test_source_range_not_filename_or_transaction_max():
    r=a.reconcile([('alipay_2026-06-30_2026-07-10.zip',bill(end='2026-07-01 18:00:00'))],[],end=date(2026,7,1))
    assert r['uncovered_days']==['2026-07-01']


def test_date_mismatch_detected():
    body=bill(rows='123,2026-06-30 12:00:00,支付,20,支出')
    r=a.reconcile([('1',body)],[('123','支付',-20,date(2026,7,1))],end=date(2026,7,1))
    assert r['missing_rows']==1


def test_malformed_archive_is_not_a_green_pass():
    r=a.reconcile([('1','broken')],[],end=date(2026,6,30))
    assert not r['ok'] and any(i['key']=='unreadable:1' for i in r['issues'])


def test_normal_completion_never_sends(db_session,monkeypatch):
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY',raising=False)
    send=Mock()
    r=a.record_and_notify(db_session,{'ok':True,'issues':[]},send=send)
    assert r['notification']=='quiet'
    send.assert_not_called()


def test_new_failure_sent_once_with_message_receipt(db_session,monkeypatch):
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY',raising=False)
    monkeypatch.setattr(notify_service,'get_alert_chat_id',lambda db:'test-chat')
    send=Mock(return_value={'message_id':'test-receipt'})
    def result():return {'ok':False,'issues':[{'key':'missing:1','message':'有一条未入账'}]}
    first=a.record_and_notify(db_session,result(),send=send)
    second=a.record_and_notify(db_session,result(),send=send)
    assert first['notification']['message_id']=='test-receipt'
    assert second['notification']=='quiet'
    assert send.call_count==1


def test_unknown_delivery_is_not_retried(db_session,monkeypatch):
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY',raising=False)
    monkeypatch.setattr(notify_service,'get_alert_chat_id',lambda db:'test-chat')
    send=Mock(side_effect=TimeoutError())
    def result():return {'ok':False,'issues':[{'key':'coverage:2026-07-01','message':'需补拉'}]}
    assert a.record_and_notify(db_session,result(),send=send)['notification']['state']=='unknown'
    a.record_and_notify(db_session,result(),send=send)
    assert send.call_count==1


def test_new_issue_after_old_failure_still_notifies(db_session,monkeypatch):
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY',raising=False)
    monkeypatch.setattr(notify_service,'get_alert_chat_id',lambda db:'test-chat')
    send=Mock(return_value={'message_id':'test'})
    a.record_and_notify(db_session,{'ok':False,'issues':[{'key':'a','message':'a'}]},send=send)
    a.record_and_notify(db_session,{'ok':False,'issues':[{'key':'a','message':'a'},{'key':'b','message':'b'}]},send=send)
    assert send.call_count==2 and send.call_args.args[2].endswith('b')


def test_disabled_notifications_do_not_consume_dedupe(db_session,monkeypatch):
    monkeypatch.setenv('PANSE_DISABLE_NOTIFY','1')
    send=Mock()
    r=a.record_and_notify(db_session,{'ok':False,'issues':[{'key':'a','message':'a'}]},send=send)
    assert r['new_issues']==1
    send.assert_not_called()


def test_ingest_failure_runs_audit_after_rollback(db_session,monkeypatch,tmp_path):
    from app.services import agent_ingest_service as ingest
    path=tmp_path/'2026-10-29'/'alipay'/'主力'/'broken.csv'
    path.parent.mkdir(parents=True)
    path.write_text('bad')
    monkeypatch.setattr(ingest,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr(ingest,'_import_one',Mock(side_effect=ValueError('bad file')))
    check=Mock(return_value={'ok':False})
    monkeypatch.setattr(a,'after_collection',check)
    r=ingest.run_ingest(db_session,only_paths=[str(path)])
    assert r['errors']==1
    check.assert_called_once_with(collection_failed=True)


def test_order_only_ingest_does_not_trigger_main_audit(db_session,monkeypatch,tmp_path):
    from app.services import agent_ingest_service as ingest
    monkeypatch.setattr(ingest,'OUTPUT_DIR',tmp_path)
    check=Mock()
    monkeypatch.setattr(a,'after_collection',check)
    ingest.run_ingest(db_session,only_paths=[str(tmp_path/'orders.xlsx')])
    check.assert_not_called()


def test_main_artifact_missing_triggers_failure(db_session,monkeypatch,tmp_path):
    from app.services import agent_ingest_service as ingest
    monkeypatch.setattr(ingest,'OUTPUT_DIR',tmp_path)
    check=Mock(return_value={'ok':False})
    monkeypatch.setattr(a,'after_collection',check)
    ingest.run_ingest(db_session,only_paths=[str(tmp_path/'alipay'/'主力'/'missing.zip')])
    check.assert_called_once_with(collection_failed=True)
