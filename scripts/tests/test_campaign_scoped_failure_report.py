import json
from pathlib import Path
import pytest
from campaign_scoped_failure_report import save, read


def fixture(root, name, items, phase='discount'):
    folder=root/'actions'/name
    folder.mkdir(parents=True)
    payload=dict(identity={'campaign_id':'x'}, time_binding={'window':'exact'},
                 batch='offer1', failed_phase=phase, items=items)
    (folder/'request.json').write_text(json.dumps({'step':'report','payload':payload}))
    report=dict(batch='offer1',bundle_id='bundle',source_terminal='original',
                errors=[dict(item=i,batch='offer1') for i in items])
    ref=save(root,folder,report)
    repair=dict(payload,failed_batch=payload['batch'],report_ref=ref)
    return folder,report,ref,repair


def test_same_batch_disjoint_scopes_and_idempotence(tmp_path):
    a,old,ref_a,pa=fixture(tmp_path,'partial',['a','b'])
    legacy=(tmp_path/'reports/offer1.json').read_bytes()
    b,new,ref_b,pb=fixture(tmp_path,'full',['c'])
    assert read(tmp_path,pa)[1]==old
    assert read(tmp_path,pb)[1]==new
    assert save(tmp_path,b,new)==ref_b
    assert (tmp_path/'reports/offer1.json').read_bytes()==legacy
    assert ref_a!=ref_b


@pytest.mark.parametrize('key,value', [('failed_batch','other'),('failed_phase','signup'),
    ('items',['outside']),('identity',{'campaign_id':'other'}),('time_binding',{'window':'other'})])
def test_reject_wrong_repair_binding(tmp_path,key,value):
    _,_,_,p=fixture(tmp_path,'one',['a'])
    p[key]=value
    with pytest.raises(ValueError):read(tmp_path,p)


def test_hash_tamper_and_immutable_original(tmp_path):
    folder,report,ref,p=fixture(tmp_path,'one',['a'])
    with pytest.raises(ValueError,match='immutable'):
        save(tmp_path,folder,dict(report,errors=[]))
    Path(ref['path']).write_text('{}')
    with pytest.raises(ValueError,match='binding_invalid'):read(tmp_path,p)


def test_legacy_cached_actions_remain_readable(tmp_path):
    _,report,_,p=fixture(tmp_path,'one',['a'])
    p.pop('report_ref')
    assert read(tmp_path,p)[1]==report


def test_no_cross_segment_report(tmp_path):
    _,_,_,p=fixture(tmp_path/'segment1','one',['a'])
    with pytest.raises(ValueError,match='binding_invalid'):read(tmp_path/'segment2',p)
