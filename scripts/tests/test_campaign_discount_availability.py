from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
import campaign_discount_availability as mod
from campaign_entry_authority import file_sha, Authority


@pytest.mark.parametrize('fault',[None,'missing_audit','wrong_folder','old_video','new_video',
    'new_capture','old_click','wrong_phase','wrong_disposition'])
def test_recovered_offer_readback_requires_same_job_audit_and_two_intact_recordings(tmp_path,fault):
    import sqlite3
    jid='a'*64;root=tmp_path;folder=root/jid
    old_video=folder/'recording'/'original'/'recording.mp4'
    new_video=folder/'read-recovery-1'/'recording'/'new'/'recording.mp4'
    old_video.parent.mkdir(parents=True);new_video.parent.mkdir(parents=True)
    old_video.write_bytes(b'old-recording');new_video.write_bytes(b'new-recording')
    proof=folder/'read-recovery-1'/'discount-readback.json';proof.write_text('{}')
    failure=folder/'failure-disposition.json'
    failure.write_text(json.dumps(dict(job_id=jid,operation='discount_readback',
        disposition=dict(action='manual_program_repair'))))
    old=dict(error='TimeoutError',automatic_retry=False,
        program_location=[dict(file='campaign_bound_transfers.py',function='execute',line=352)],
        failure_disposition_path=str(failure),recording=dict(events=0,video=str(old_video),
            video_sha256=hashlib.sha256(old_video.read_bytes()).hexdigest()))
    result=dict(evidence_path=str(proof),recording=dict(active=False,frames=8,capture_errors=0,
        error=None,video=str(new_video),video_sha256=hashlib.sha256(new_video.read_bytes()).hexdigest()))
    with sqlite3.connect(root/'jobs.sqlite') as db:
        db.execute('CREATE TABLE campaign_offer_availability_navigation_recovery(id TEXT,previous_result TEXT)')
        if fault!='missing_audit':db.execute('INSERT INTO campaign_offer_availability_navigation_recovery VALUES(?,?)',
                                             (jid,json.dumps(old)))
    if fault=='wrong_folder':result['evidence_path']=str(folder/'other'/'discount-readback.json')
    elif fault=='old_video':old_video.write_bytes(b'changed')
    elif fault=='new_video':new_video.write_bytes(b'changed')
    elif fault=='new_capture':result['recording']['capture_errors']=1
    elif fault=='old_click':old['recording']['events']=1
    elif fault=='wrong_phase':old['program_location'][0]['line']=353
    elif fault=='wrong_disposition':failure.write_text(json.dumps(dict(job_id=jid,operation='discount_readback',
        disposition=dict(action='automatic_retry'))))
    if fault in ('old_click','wrong_phase'):
        with sqlite3.connect(root/'jobs.sqlite') as db:
            db.execute('UPDATE campaign_offer_availability_navigation_recovery SET previous_result=? WHERE id=?',
                       (json.dumps(old),jid))
    if fault:
        with pytest.raises(ValueError):mod._verified_navigation_recovery(root,jid,result)
    else:
        mod._verified_navigation_recovery(root,jid,result)


@pytest.mark.parametrize('fault',[None,'write','job','campaign','window','stale','future','filter','no_user_removal','scope','terminal','price_proof','order'])
def test_fixed_receipt_accepts_only_bound_current_absence(tmp_path,monkeypatch,fault):
    import sqlite3
    from datetime import datetime,timezone,timedelta
    from campaign_continuous_policy import fingerprint
    import campaign_segmented_time
    now=datetime.now(timezone.utc)
    identity=dict(campaign_id='legacy',phase_id='itemApply',sign_record_id='1',shop_name='shop',
                  title='super',phase_title='items',start='2025-01-01 00:00:00',end='2028-01-01 00:00:00')
    scope=dict(campaign='legacy/itemApply/1',shop='shop',start='2026-09-28 00:00:00',end='2026-10-07 19:59:59')
    request=dict(calendar={},pages={scope['campaign']:{**{k:v for k,v in identity.items() if k!='shop_name'},'shop_id':'shop'}})
    monkeypatch.setattr(campaign_segmented_time,'build_plan',lambda c:dict(segments=[dict(campaign=scope['campaign'],shop_id='shop',price_window={k:scope[k] for k in ('start','end')})]))
    request_path=tmp_path/'request.json';request_path.write_text(json.dumps(request))
    payload=dict(identity=identity,read_request_id='a'*64,offer_status_only=True,price_window=dict(start=scope['start'],end='2026-09-30 23:59:59'),offers=[dict(offer_id='123',item='1',sku_ids=[])])
    jid=fingerprint(['discount_readback','shop','a'*64])
    proof=tmp_path/jid/'discount-readback.json';proof.parent.mkdir()
    observed=now-timedelta(seconds=1801 if fault=='stale' else -1 if fault=='future' else 0)
    row=dict(offer_id='123',observed_state='not_found',unfiltered_exact_query=fault!='filter',search_value='123',observed_at=observed.isoformat(),platform_write=False)
    result=dict(state='offer_status_readback',platform_write=fault=='write',shop_name='shop',rows=[row])
    if fault=='scope':result['rows']=[]
    if fault=='price_proof':result['state']='readback'
    proof.write_text(json.dumps(result));result['evidence_path']=str(proof)
    db=sqlite3.connect(tmp_path/'jobs.sqlite');db.execute('CREATE TABLE campaign_transfer_jobs (id,operation,state,request,result)')
    req=dict(payload=payload,request_sha=fingerprint(payload))
    db.execute('INSERT INTO campaign_transfer_jobs VALUES (?,?,?,?,?)',(jid,'discount_readback','interrupted_read' if fault=='terminal' else 'finished',json.dumps(req),json.dumps(result)));db.commit();db.close()
    doc=dict(schema='campaign_discount_availability_v1',controller_id=fingerprint(request),request_path=str(request_path),
             job_id='b'*64 if fault=='job' else jid,scope=dict(scope),readback_sha256=file_sha(proof),offer_ids=['123'],user_removed_old_offers=fault!='no_user_removal')
    if fault=='order':doc['observed_at']='2000-01-01T00:00:00+00:00'
    if fault=='campaign':doc['scope']['campaign']='legacy/itemApply/2'
    if fault=='window':doc['scope']['end']='2026-10-08 19:59:59'
    path=tmp_path/'receipt.json';path.write_text(json.dumps(doc));ref=dict(path=str(path),sha256=file_sha(path))
    monkeypatch.setattr(mod,'TRANSFER_ROOT',tmp_path)
    if fault:
        with pytest.raises(ValueError):mod.verified_scope(ref,now=now)
    else:assert mod.verified_scope(ref,now=now)['offer_ids']==['123']


def fixture(tmp_path,monkeypatch):
    scope=dict(campaign='legacy/itemApply/1',shop='shop',start='2026-09-28 00:00:00',end='2026-10-07 19:59:59',
               old_window=dict(start='2026-09-28 00:00:00',end='2026-09-30 23:59:59'),offer_ids=['123'],
               requested_offer_ids=['123'],observed_at='2026-09-23T05:00:00+00:00')
    path=tmp_path/'scope.json';path.write_text(json.dumps(dict(scope=scope,offer_ids=['123'])),encoding='utf-8')
    monkeypatch.setattr(mod,'verified_scope',lambda ref:scope)
    offer=dict(offer_id='bundle:old',platform_offer_id='123',start=scope['old_window']['start'],end=scope['old_window']['end'],
               items=[dict(item='1',status='success'),dict(item='2',status='unknown')],rows=[],
               availability_refs=[dict(path=str(path),sha256=file_sha(path))])
    return scope,offer


@pytest.mark.parametrize('change',['none','campaign','window','original_window','other_offer','missing_reference'])
def test_only_exact_new_window_can_ignore_old_offer(tmp_path,monkeypatch,change):
    scope,offer=fixture(tmp_path,monkeypatch);before=deepcopy(offer)
    campaign,start,end=scope['campaign'],scope['start'],scope['end']
    if change=='campaign':campaign='legacy/itemApply/2'
    if change=='window':end='2026-10-08 19:59:59'
    if change=='original_window':end=offer['end']
    if change=='other_offer':offer['platform_offer_id']='124';before=deepcopy(offer)
    if change=='missing_reference':offer.pop('availability_refs');before=deepcopy(offer)
    assert mod.inactive_for_window(offer,campaign,start,end)==(change=='none')
    assert offer==before


def test_changed_receipt_never_unblocks(tmp_path,monkeypatch):
    scope,offer=fixture(tmp_path,monkeypatch)
    Path(offer['availability_refs'][0]['path']).write_text('{}')
    with pytest.raises(ValueError,match='receipt_changed'):mod.inactive_for_window(offer,scope['campaign'],scope['start'],scope['end'])


def test_discount_guard_preserves_unrelated_unknown_and_history(tmp_path,monkeypatch):
    scope,old=fixture(tmp_path,monkeypatch);other=dict(offer_id='new',start=scope['start'],end=scope['end'],items=[dict(item='3',status='unknown')])
    class Fake:
        def discount_offers(self):return [old,other]
    before=deepcopy(old)
    assert Authority.blocked(Fake(),scope['campaign'],'discount',scope['start'],scope['end'])=={'3':'unknown'}
    assert old==before
    assert Authority.blocked(Fake(),scope['campaign'],'discount',old['start'],old['end'])=={'1':'success','2':'unknown','3':'unknown'}


def test_new_exact_offer_readback_precedes_stale_immutable_history():
    old={'kind':'discount_availability','path':'old','sha256':'old-sha','document':{}}
    fresh={'kind':'discount_availability','path':'fresh','sha256':'fresh-sha',
           'document':{'observed_at':'2026-09-23T05:00:00+00:00'}}
    class Fake:
        def sources(self):return [old,fresh]
    offers=[{'offer_id':'123'}]
    assert mod.overlay(Fake(),offers)==[{'offer_id':'123','availability_refs':[
        {'path':'fresh','sha256':'fresh-sha'},{'path':'old','sha256':'old-sha'}]}]
    assert Fake().sources()==[old,fresh]


def test_fresh_active_readback_overrides_stale_inactive_without_erasing_history(tmp_path,monkeypatch):
    scope,offer=fixture(tmp_path,monkeypatch)
    old=offer['availability_refs'][0]
    new_path=tmp_path/'new.json';new_path.write_text(json.dumps({'scope':scope}),encoding='utf-8')
    fresh=dict(path=str(new_path),sha256=file_sha(new_path))
    offer['availability_refs']=[old,fresh]
    def verified(ref):
        if ref==old:raise ValueError('offer_availability_readback_stale')
        return dict(scope,offer_ids=[],observed_at='2026-09-23T06:00:00+00:00')
    monkeypatch.setattr(mod,'verified_scope',verified)
    assert mod.inactive_for_window(offer,scope['campaign'],scope['start'],scope['end']) is False
    offer['availability_refs']=[old]
    with pytest.raises(ValueError,match='offer_availability_readback_stale'):
        mod.inactive_for_window(offer,scope['campaign'],scope['start'],scope['end'])
