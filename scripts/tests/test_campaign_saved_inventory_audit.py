from copy import deepcopy
import pytest
import campaign_saved_inventory_audit as audit
from campaign_continuous_policy import fingerprint


@pytest.fixture
def data(tmp_path,monkeypatch):
    p={'identity':{'shop':'s'},'price_window':{'start':'a','end':'b'}}
    directory=tmp_path/'original/recording/r';directory.mkdir(parents=True)
    report=tmp_path/'feedback/report.xlsx';report.parent.mkdir();report.write_bytes(b'xlsx')
    o={'id':'original','operation':'final_inventory','state':'interrupted_read','request':{'payload':p,'request_sha':fingerprint(p)},'result':{'recording':{'directory':str(directory),'video':str(directory/'recording.mp4')}}}
    payload={'identity':p['identity'],'batch':'1'}
    f={'id':'feedback','operation':'feedback','state':'finished','request':{'payload':payload,'request_sha':fingerprint(payload)},'result':{'state':'downloaded','batch':'1','record':{'历史记录ID':'1','执行操作':'已报商品导出','执行状态':'成功','执行时间':'t','执行结果':'总数量2件，已成功2件，失败0件。'},'path':str(report),'sha256':'hash'}}
    review={'batch':'1','record_time':'t','settings':{'dimension':'SKU','status':'全部'},'frames':{'000001.jpg':'hash'},'video_sha256':'hash','file_sha256':'hash'}
    monkeypatch.setattr(audit,'file_sha',lambda p:'hash')
    monkeypatch.setattr(audit,'parse_inventory',lambda b:[{'marketing_id':'1'},{'marketing_id':'2'}])
    return o,f,review,tmp_path


def test_original_export_has_exact_coverage(data):
    rows,p,source,total=audit.validate_source(*data)
    assert total==2 and len(rows)==2


@pytest.mark.parametrize('change',['identity','hash','count','time','state','settings','frame_path'])
def test_changed_export_evidence_refused(data,change):
    o,f,r,p=deepcopy(data)
    if change=='identity':f['request']['payload']['identity']={'shop':'different'}
    elif change=='hash':r['file_sha256']='bad'
    elif change=='count':f['result']['record']['执行结果']='总数量3件，已成功3件，失败0件。'
    elif change=='time':r['record_time']='other'
    elif change=='state':o['state']='running'
    elif change=='settings':r['settings']['dimension']='商品'
    elif change=='frame_path':r['frames']={'../outside.jpg':'hash'}
    with pytest.raises(ValueError):audit.validate_source(o,f,r,p)
