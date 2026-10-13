"""Cross-stage checklist regressions; temporary DB/files, no live transport."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]))


def test_same_batch_different_failure_subsets_keep_original_reports(tmp_path):
    from campaign_scoped_failure_report import save,read
    from campaign_continuous_transport import persist
    identity={'campaign_id':'1','phase_id':'2','sign_record_id':'3'}
    def one(action,item):
        folder=tmp_path/'actions'/action
        report=dict(batch='777',errors=[dict(item=item,sku=item+'0')])
        persist(folder/'request.json',dict(step='report',payload=dict(identity=identity,batch='777',failed_phase='discount',items=[item])))
        ref=save(tmp_path,folder,report)
        return dict(identity=identity,failed_batch='777',failed_phase='discount',items=[item],report_ref=ref),report
    first,a=one('a','1');second,b=one('b','2')
    assert read(tmp_path,first)[1]==a and read(tmp_path,second)[1]==b
    assert json.loads((tmp_path/'reports/777.json').read_text())==a
    second['report_ref']=first['report_ref']
    with pytest.raises(ValueError,match='scope_mismatch'):read(tmp_path,second)


def test_late_failed_recovery_cannot_replace_saved_success_after_reopen(tmp_path):
    from campaign_continuous_flow import Store,Blocked
    from campaign_continuous_policy import fingerprint
    path=tmp_path/'controller.sqlite3';store=Store(path)
    rid=store.start('exact-activity-window','rule');payload={'items':['1']}
    transport=SimpleNamespace(execute=lambda step,aid,body:dict(status='terminal',evidence='official',action_id=aid,request_sha=fingerprint(body),items=[{'item':'1','outcome':'success'}]))
    result=store.once(rid,'signup',payload,transport);store.close()
    store=Store(path)
    try:
        late=dict(result,reconciled_readonly=True,items=[{'item':'1','outcome':'failed'}])
        with pytest.raises(Blocked,match='completed_action_is_immutable'):store.recover(result['action_id'],late)
        transport.execute=lambda *a:pytest.fail('replayed completed action')
        assert store.once(rid,'signup',payload,transport)==result
    finally:store.close()


def test_recording_failure_does_not_become_official_business_failure():
    from campaign_continuous_policy import classify
    result=classify(dict(item='1',sku='2',kind='no_sales',message='recording failure'))
    assert result['action']=='manual' and result['reason']=='official_failed_scope_not_proven'


def test_no_sales_does_not_blacklist_next_campaign_or_prove_withdrawal():
    from campaign_continuous_policy import classify,signup_scope
    result=classify(dict(item='1',kind='no_sales',terminal='failed',batch='777',official_evidence={'path':'official.json'}))
    assert result['action']=='not_eligible_this_campaign' and result['repair'] is None
    assert signup_scope(['1'],[{'item':'1','on_sale':True}],complete=True,observed_item_count=1)==['1']


def test_fixed_discount_template_keeps_metadata_and_is_not_activity_template():
    import io,zipfile
    from campaign_discount_template import load_fixed_discount_template
    from campaign_official_template import fill_single_discount_rows
    original=load_fixed_discount_template()
    filled=fill_single_discount_rows(original,[dict(item='12345678901',sku='1234567890123',deduct='10')])
    with zipfile.ZipFile(io.BytesIO(original)) as old,zipfile.ZipFile(io.BytesIO(filled)) as new:
        assert set(old.namelist())==set(new.namelist())
        for name in old.namelist():
            if name!='xl/worksheets/sheet1.xml':assert old.read(name)==new.read(name),name
