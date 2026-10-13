import pytest
import campaign_submission_checklist_contract as c


@pytest.mark.parametrize('mutation',[False,True])
def test_metadata_does_not_change_original_export(monkeypatch,tmp_path,mutation):
    import campaign_product_scope as p
    f=tmp_path/'data';f.write_bytes(b'export')
    reference={'path':'receipt','sha256':'hash'}
    actual={'page_evidence':reference,'sku_facts':[{'item':'1','sku':'2'}],'page_count':1}
    scope=dict(actual,status='terminal',mapping_issues=[],prior_outcomes={'x':'success'})
    if mutation:scope['sku_facts']=[{'item':'1','sku':'3'}]
    monkeypatch.setattr(c,'file_sha',lambda _: 'hash')
    monkeypatch.setattr(c,'load',lambda _: {'files':[{'path':str(f),'sha256':'hash'}],
        'observed_item_ids':['1'],'page_count':1,'observed_total':1})
    monkeypatch.setattr(p,'complete_scope',lambda *a,**k:actual)
    if mutation:
        with pytest.raises(ValueError,match='not_original_export'):c.validate_scope(scope)
    else:assert c.validate_scope(scope)==scope
