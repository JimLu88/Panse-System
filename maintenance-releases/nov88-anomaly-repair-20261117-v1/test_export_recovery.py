import copy,json,hashlib
import pytest
import export_recovery as m

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def original():
    payload={'identity':dict(campaign_id='49727',phase_id='49734',sign_record_id='3594587083',start='2026-11-16 20:00:00',end='2026-11-19 23:59:59')}
    request=dict(payload=payload,request_sha=fingerprint(payload))
    result=dict(error='AttributeError',program_location=[dict(file='campaign_transfer_jobs.py',function='_execute',line=944),dict(file='campaign_bound_transfers.py',function='execute',line=442)],recording=dict(state='unavailable_before_page_binding',video=None))
    return dict(id=m.JOB,operation='final_inventory',state='interrupted_read',result=json.dumps(result),request=json.dumps(request),request_sha=fingerprint(dict(operation='final_inventory',request=request)))

def test_exact_original_pre_page_stop_can_resume(tmp_path):
    row=original();assert m.validate(row,tmp_path,fingerprint)==json.loads(row['request'])

@pytest.mark.parametrize('field,value',[('id','other'),('operation','signup'),('state','unknown'),('request_sha','changed')])
def test_non_original_or_unknown_rejected(tmp_path,field,value):
    row=original();row[field]=value
    with pytest.raises(ValueError):m.validate(row,tmp_path,fingerprint)

def test_existing_export_intent_rejected(tmp_path):
    (tmp_path/'export-request.json').write_text('{}')
    with pytest.raises(ValueError,match='already_attempted'):m.validate(original(),tmp_path,fingerprint)

def test_recorded_page_failure_cannot_use_pre_page_recovery(tmp_path):
    row=original();r=json.loads(row['result']);r['recording']['state']='recorded';row['result']=json.dumps(r)
    with pytest.raises(ValueError,match='pre_page'):m.validate(row,tmp_path,fingerprint)
