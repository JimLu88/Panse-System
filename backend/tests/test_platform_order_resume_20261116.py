from datetime import datetime, timedelta
import pytest
from tests.test_platform_scan_selection import harness
from app.services import platform_scan_service as scan, platform_order_resume_service as resume
from app.services import automation_pipeline_service as pipeline, scheduler, agent_ingest_service as ingest


def waiting(db, monkeypatch):
    monkeypatch.setattr(pipeline, "_send_feishu", lambda *a: (True,"test"))
    now=datetime.now().astimezone()
    pipeline.record_failure(db,"order_delivery","session_expired",retry_slots=[],now=now-timedelta(minutes=2));db.commit()
    monkeypatch.setattr(scheduler,"_now_hour",lambda:20)


def test_login_releases_locks_then_schedules_original_once(db_session,monkeypatch):
    _,calls,workers=harness(db_session,monkeypatch);waiting(db_session,monkeypatch)
    scheduled=[]
    def trigger(expected,key,job):
        assert not ingest._orch_lock.locked() and not ingest._scan_lock.locked()
        scheduled.append("pull_catchup_30min");return True
    monkeypatch.setattr(resume,"_dispatch",trigger)
    expected=resume.pending_order(db_session,"shop")
    scan.start_login(db_session,"shop",request_id="resume");workers.pop()()
    assert scheduled==["pull_catchup_30min"] and len(calls)==1
    active=scan._load(db_session,scan.ACTIVE_KEY)
    assert not resume.queue_existing_order(db_session,platform="shop",key=active['request_key'],job=active['job'],expected=expected)
    assert len(scheduled)==1 and pipeline.get_pipeline(db_session,"order_delivery")["waiting_input"]

@pytest.mark.parametrize('case',['alipay','no_pending','changed_attempt','completed','off_window','unknown'])
def test_no_scope_expansion(db_session,monkeypatch,case):
    final={'status':'unknown'} if case=='unknown' else None
    _,calls,workers=harness(db_session,monkeypatch,final=final)
    if case!='no_pending':waiting(db_session,monkeypatch)
    monkeypatch.setattr(resume,'_dispatch',lambda *a:pytest.fail('must not schedule'))
    scan.start_login(db_session,'alipay' if case=='alipay' else 'shop',request_id=case)
    if case=='changed_attempt':
        state=pipeline._load(db_session);state['pipelines']['order_delivery']['last_attempt_at']=datetime.now().astimezone().isoformat();pipeline._save(db_session,state);db_session.commit()
    if case=='completed':
        state=pipeline._load(db_session);state['pipelines']['order_delivery']['success']=True;pipeline._save(db_session,state);db_session.commit()
    if case=='off_window':monkeypatch.setattr(scheduler,'_now_hour',lambda:23)
    workers.pop()()
    assert not ingest._orch_lock.locked() and not ingest._scan_lock.locked()


def test_execution_rechecks_original_wait(db_session,monkeypatch):
    _,calls,workers=harness(db_session,monkeypatch);waiting(db_session,monkeypatch)
    captured=[]
    monkeypatch.setattr(resume,'_dispatch',lambda *a:captured.append(a) or True)
    scan.start_login(db_session,'shop',request_id='delayed');workers.pop()()
    expected,key,job=captured[0]
    monkeypatch.setattr(scheduler,'_job_pull_catchup',lambda db:{'original_entry':True})
    assert resume._continue_original(db_session,expected,key,job)=={'original_entry':True}
    state=pipeline._load(db_session);state['pipelines']['order_delivery']['run_key']='new-run';pipeline._save(db_session,state);db_session.commit()
    assert resume._continue_original(db_session,expected,key,job)['skipped']=='original_login_wait_changed'
