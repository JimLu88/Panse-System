import hashlib
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_recording_evidence import collect, failure_handling


def test_recovered_export_keeps_recording_for_every_original_page(tmp_path):
    files=[]
    for i in range(2):
        root=tmp_path/str(i);root.mkdir()
        export=root/'export.xlsx';export.write_bytes(b'test-only')
        video=root/'recording.mp4';video.write_bytes(bytes([i]))
        record={'video':str(video),'video_sha256':hashlib.sha256(video.read_bytes()).hexdigest(),
                'active':False,'frames':1,'capture_errors':0}
        (root/'recording-result.json').write_text(json.dumps(record))
        files.append({'path':str(export)})
    job={'job_id':'a'*64,'operation':'product_export','result':{'files':files}}
    assert len(collect(job,[tmp_path]))==2
    video.write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):collect(job,[tmp_path])


@pytest.mark.parametrize('last',['form-recovery-1','rejected-upload-recovery-1'])
def test_discount_form_recovery_preserves_old_and_new_recordings(tmp_path,last):
    jid='a'*64;root=tmp_path/jid;records=[]
    folders=[root,root/'form-recovery-1']
    if last=='rejected-upload-recovery-1':folders.append(root/last)
    for folder in folders:
        folder.mkdir(parents=True,exist_ok=True)
        video=folder/'recording.mp4';video.write_bytes(b'fixture video')
        record={'video':str(video),'video_sha256':hashlib.sha256(video.read_bytes()).hexdigest(),
                'active':False,'frames':2,'capture_errors':0}
        (folder/'recording-result.json').write_text(json.dumps(record));records.append(record)
    job={'job_id':jid,'operation':'discount','result':{'recording':records[-1],
        'evidence_path':str(root/last/'discount-result.json')}}
    assert [r['recording'] for r in collect(job,[tmp_path])]==records


@pytest.mark.parametrize('record',[{'video':None},{'video':''},
    {'video':'missing.mp4','failure_tails':[{'new_safe_frames':0,'capture_gap':'page_closed'}]}])
def test_missing_failure_video_is_reported_not_accepted(tmp_path,record):
    with pytest.raises(ValueError):
        collect({'job_id':'a'*64,'operation':'discount','result':{'recording':record}},[tmp_path])


def test_final_summary_preserves_worker_decision_without_interpreting_prices():
    decision={'action':'manual_report','automatic_retry':False,'submission_replay_allowed':False}
    result={'failure_disposition':decision,'report_recovery':{'retries_used':1,'action':'manual_report'},
            'recording':{'video':None},'failure_disposition_path':'private-local-receipt.json'}
    summary=failure_handling({'job_id':'a'*64,'operation':'discount','result':result})
    assert summary['disposition'] is decision and summary['recording']['video'] is None
    assert summary['report_recovery']['retries_used']==1
    assert failure_handling({'result':{'failure_disposition':{'action':'continue'}}}) is None
