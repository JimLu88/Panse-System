"""Offline controller integration, never a real platform acceptance receipt."""
import pytest
from campaign_continuous_flow import Store,run,REQUIRED_CAPABILITIES
from campaign_continuous_policy import ENTRY,RULE_SHA,fingerprint

@pytest.mark.parametrize('case',['normal','missing_input','unknown_write'])
def test_one_program_owns_full_sequence_and_resume_never_resends(tmp_path,case):
    page={'entry':ENTRY,'url':'https://myseller.taobao.com/example','shop_id':'fixture',
          'campaign_id':'1','phase_id':'2','sign_record_id':'3','title':'fixture',
          'start':'2026-10-09 00:00:00','end':'2026-10-12 23:59:59',
          'official_rate':'0.12','page_evidence':'offline-fixture'}
    calls=[]
    class Transport:
        def capabilities(self):return REQUIRED_CAPABILITIES
        def execute(self,step,aid,p):
            calls.append((step,p))
            r={'status':'terminal','evidence':'offline-only','action_id':aid,'request_sha':fingerprint(p)}
            if step=='scope':r.update(complete=True,erp_sellable=['11','22'],platform_rows=[{'item':'11','on_sale':True},{'item':'22','on_sale':True}],observed_item_count=2,prior_outcomes={},prior_outcomes_evidence='offline-only',price_version='v1')
            elif step=='template':r.update(registered_items=[])
            elif step=='generate':
                if case=='missing_input' and '22' in p['items']:
                    r['input_issues']=[{'item':'22','sku':'222','error':'erp_mapping_missing_or_not_unique'}]
                else:r.update(validated_rule_sha=RULE_SHA,price_version='v1',items=p['items'],file_sha='fixture',full_active_skus=True,discount_items=p['items'])
            elif step in ('discount','signup'):
                if case=='unknown_write' and step=='signup':raise TimeoutError('fixture')
                r.update(batch='fixture-'+step,items=[{'item':i,'outcome':'success'} for i in p['items']])
            elif step=='verify_discount_window':r.update(start=page['start'],end=page['end'],all_correct=True,items=p['items'])
            else:pytest.fail(step)
            return r
    store=Store(tmp_path/'controller.sqlite3');transport=Transport()
    try:
        first=run(store,transport,page,expected_shop='fixture',observed_links=[page['url']])
        before=list(calls)
        second=run(store,transport,page,expected_shop='fixture',observed_links=[page['url']])
        assert calls==before
        assert sum(s=='template' for s,p in calls)==1
        assert sum(s=='discount' for s,p in calls)==1
        assert sum(s=='signup' for s,p in calls)==1
        if case=='unknown_write':
            assert first['status']==second['status']=='blocked' and not second['all_signed_up']
        else:
            assert first['status']==second['status']=='complete'
            assert set(first['success'])==({'11'} if case=='missing_input' else {'11','22'})
            assert first['all_signed_up']==(case=='normal')
        if case=='missing_input':
            assert first['exceptions']['22'][0]['reason']=='erp_mapping_missing_or_not_unique'
            assert all(p['items']==['11'] for s,p in calls if s in ('discount','signup'))
    finally:store.close()
