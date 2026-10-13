from types import SimpleNamespace
import pytest
import campaign_completion_contract as m


@pytest.mark.parametrize('audit', [{}, {'verified':True,'segments':[]}, {'gaps':[]}])
def test_historical_success_never_completes_without_export(audit):
    r={'status':'complete','all_signed_up':True,'segments':[{'segment_id':'one','success':{'cabinet':'old'}}], 'final_audit':audit}
    assessment=m.assess(r,'.',None)
    result=m.apply(r,assessment)
    assert result['all_completed'] is False
    assert result['segments']==r['segments'] and result['status']=='complete'


@pytest.mark.parametrize('price,custom,wanted', [('100',False,'matches_erp_daily'),('99',False,'differs_from_erp_daily'),('',False,'unknown'),('100',True,'fixed_original_floor_requires_verification')])
def test_actual_export_price_not_historical_claim(tmp_path,monkeypatch,price,custom,wanted):
    import campaign_final_audit as f
    import campaign_failure_remediation as p
    source=tmp_path/'export';source.write_bytes(b'fixture')
    monkeypatch.setattr(m,'file_sha',lambda _: 'hash')
    monkeypatch.setattr(m,'load',lambda _: {})
    monkeypatch.setattr(f,'parse_inventory',lambda _: [{'item':'1','sku':'2','state':'活动中','activity_price':price}])
    monkeypatch.setattr(p,'mapped_erp_rows',lambda _: [{'item':'1','sku':'2','code':'PPS1','daily':'100','custom':custom}])
    r={'segments':[{'segment_id':'one'}], 'final_audit':{'verified':True,'segments':[{'segment_id':'one','source_file':{'path':str(source),'sha256':'hash'},'rows':[{'item':'1','sku':'2','registered':True}]}]}}
    a=m.assess(r,tmp_path,SimpleNamespace(resolve_snapshot=lambda x:x))
    assert a['rows'][0]['signup_price_status']==wanted
    assert not a['complete'] # export cannot prove actual single-discount/window


def test_unrecognized_completion_policy_not_accepted():
    assert not m.apply({'all_signed_up':True},{'complete':True})['all_completed']
