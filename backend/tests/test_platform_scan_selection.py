import json

import pytest

from app.services import platform_scan_service as S, feishu_bot_service as B
from app.services import agent_ingest_service as I, settings_service as settings


@pytest.mark.parametrize('word', sorted(S.SCAN_WORDS))
def test_generic_scan_only_offers_choices(db_session, monkeypatch, word):
    replies = []
    monkeypatch.setattr(B, '_safe_reply', lambda *args: replies.append(args[-1]))
    monkeypatch.setattr(S, 'start_login', lambda *a, **k: pytest.fail('must not login'))
    monkeypatch.setattr(I, 'start_pending_scans', lambda *a, **k: pytest.fail('must not run pending'))
    r = B.on_message_event(db_session, {'message': {'message_id': 'generic', 'message_type': 'text', 'content': json.dumps({'text': word})}})
    assert r['kind'] == 'scan_picker'
    assert '店铺后台扫码' in str(replies) and '支付宝扫码' in str(replies)


@pytest.mark.parametrize('word,platform', list(S.CHOICES.items()))
def test_explicit_choice_passes_exact_platform_and_message(db_session, monkeypatch, word, platform):
    seen = []
    monkeypatch.setattr(B, '_safe_reply', lambda *a: None)
    monkeypatch.setattr(S, 'start_login', lambda db, p, **kw: seen.append((p, kw)) or {'started': True})
    r = B.on_message_event(db_session, {'message': {'message_id': 'm1', 'message_type': 'text', 'content': json.dumps({'text': '@_user_1 '+word})}})
    assert r['started'] and seen == [(platform, {'request_id': 'm1'})]


@pytest.mark.parametrize('word', ['1', '2', '微信扫码', '万师傅扫码', '淘宝支付宝扫码', 'shop'])
def test_no_ambiguous_numeric_or_other_platform_choice(word):
    assert S.selection(word) is None


def harness(db, monkeypatch, final=None, post=None):
    pending = ['taobao_orders', 'alipay_main', 'wechat_bill', 'wanshifu', 'bal_wanshifu', 'bal_alipay_9a']
    settings.set_value(db, I.KEY_PENDING_SCAN, json.dumps(pending)); db.commit()
    calls = []; workers = []
    class Thread:
        def __init__(self, target, **kw): self.target = target
        def start(self): workers.append(self.target)
    monkeypatch.setattr(S.threading, 'Thread', Thread)
    monkeypatch.setattr('app.database.SessionLocal', lambda: db)
    monkeypatch.setattr(db, 'close', lambda: None)
    monkeypatch.setattr(S.web_agent_service, 'ensure_online', lambda *a, **k: {'online': True})
    monkeypatch.setattr(S.web_agent_service, '_post', lambda db, path, payload, **kw: calls.append((path, kw)) or (post if post is not None else {'job': 'login-job'}))
    monkeypatch.setattr(S.web_agent_service, 'wait_job', lambda *a, **k: final or {'status': 'done', 'result': {'platform': 'taobao' if calls[0][0].endswith('taobao_orders') else 'alipay_main', 'saved_at': '2026-10-21', 'cookie_count': 2}})
    monkeypatch.setattr(S.web_agent_service, 'request_stop', lambda *a, **k: calls.append(('stop', {})))
    monkeypatch.setattr(S.web_agent_service, 'run_task', lambda *a, **k: pytest.fail('business task forbidden'))
    monkeypatch.setattr(I, 'run_ingest', lambda *a, **k: pytest.fail('business ingest forbidden'))
    return pending, calls, workers


@pytest.mark.parametrize('platform,task', [('shop', 'taobao_orders'), ('alipay', 'alipay_main')])
def test_login_only_exact_endpoint_preserves_pending(db_session, monkeypatch, platform, task):
    pending, calls, workers = harness(db_session, monkeypatch)
    assert S.start_login(db_session, platform, request_id='m1')['started']
    assert not S.start_login(db_session, platform, request_id='m2')['started']
    workers.pop()()
    assert calls == [('/api/login/'+task, {'auto_wake': False}), ('stop', {})]
    assert I.get_pending_scans(db_session) == pending
    assert not S.start_login(db_session, platform, request_id='m1')['started']
    assert S._load(db_session, S.ACTIVE_KEY)['state'] == 'success'


@pytest.mark.parametrize('final', [{'status': 'running'}, {'status': 'unknown'}, {'status': 'timeout'}])
def test_unknown_job_never_replays_or_stops(db_session, monkeypatch, final):
    pending, calls, workers = harness(db_session, monkeypatch, final=final)
    assert S.start_login(db_session, 'shop', request_id='m1')['started']
    workers.pop()()
    assert len(calls) == 1
    assert not S.start_login(db_session, 'alipay', request_id='m2')['started']
    assert S._load(db_session, S.ACTIVE_KEY)['state'] == 'unknown'
    assert I.get_pending_scans(db_session) == pending


def test_submission_timeout_retains_unknown_claim(db_session, monkeypatch):
    _, calls, workers = harness(db_session, monkeypatch, post={'error': 'timeout'})
    S.start_login(db_session, 'shop', request_id='m1'); workers.pop()()
    assert len(calls) == 1 and S._load(db_session, S.ACTIVE_KEY)['state'] == 'unknown'
    assert not S.start_login(db_session, 'shop', request_id='m1')['started']


def test_orchestration_busy_and_invalid_selection_do_nothing(db_session, monkeypatch):
    _, calls, workers = harness(db_session, monkeypatch)
    assert not S.start_login(db_session, 'wechat', request_id='m1')['started']
    I._orch_lock.acquire()
    try: assert not S.start_login(db_session, 'shop', request_id='m1')['started']
    finally: I._orch_lock.release()
    assert not calls and not workers


def test_empty_pending_still_login_only(db_session, monkeypatch):
    _, calls, workers = harness(db_session, monkeypatch)
    settings.set_value(db_session, I.KEY_PENDING_SCAN, '[]'); db_session.commit()
    S.start_login(db_session, 'alipay', request_id='m1'); workers.pop()()
    assert calls[0][0] == '/api/login/alipay_main'
    assert I.get_pending_scans(db_session) == []


@pytest.mark.parametrize('meta', [{}, {'ok': True}, {'platform': 'wechat', 'saved_at': 'now', 'cookie_count': 2}])
def test_done_without_matching_session_remains_unknown(db_session, monkeypatch, meta):
    _, calls, workers = harness(db_session, monkeypatch, final={'status': 'done', 'result': meta})
    S.start_login(db_session, 'shop', request_id='missing-session'); workers.pop()()
    assert S._load(db_session, S.ACTIVE_KEY)['state'] == 'unknown'
    assert len(calls) == 1


def test_status_query_is_read_only(db_session, monkeypatch):
    _, calls, workers = harness(db_session, monkeypatch)
    monkeypatch.setattr(B, '_safe_reply', lambda *a: None)
    result = B.on_message_event(db_session, {'message': {'message_id': 'status', 'message_type': 'text', 'content': json.dumps({'text': '扫码状态'})}})
    assert result['kind'] == 'scan_status'
    assert not calls and not workers


@pytest.mark.parametrize('final', [{'status': 'failed'}, {'status': 'done', 'result': {'ok': False}}])
def test_terminal_failure_releases_for_new_message(db_session, monkeypatch, final):
    _, calls, workers = harness(db_session, monkeypatch, final=final)
    S.start_login(db_session, 'shop', request_id='failed'); workers.pop()()
    assert S._load(db_session, S.ACTIVE_KEY)['state'] == 'failed'
    assert not S.start_login(db_session, 'shop', request_id='failed')['started']
    assert S.start_login(db_session, 'alipay', request_id='new')['started']
    workers.pop()()
