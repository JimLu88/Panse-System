import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_segment_failure_scope import partition_failures


CAMPAIGN = 'legacy/itemApply/3172207691'


def fixture(tmp_path, *, old_end='2026-09-30 23:59:59', status='failed', evidence=True):
    doc = dict(phase='signup', terminal=True, campaign=CAMPAIGN, claim_id='claim',
               batch_id='123', items=[dict(item='1', status=status)])
    path = tmp_path/'terminal.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    ref = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), batch='123')
    db = sqlite3.connect(':memory:'); db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE attempts(item,status,bundle_id,id,evidence,campaign,phase)')
    db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',
               ('1', status, 'bundle', 'claim:1', json.dumps(ref) if evidence else None, CAMPAIGN, 'signup'))
    authority = SimpleNamespace(db=db, get_bundle=lambda _: dict(campaign=CAMPAIGN,
                                start='2026-09-28 00:00:00', end=old_end))
    payload = dict(identity=dict(campaign_id='legacy', phase_id='itemApply', sign_record_id='3172207691'),
                   time_binding=dict(segment=dict(kind='daily', campaign=CAMPAIGN,
                       price_window=dict(start='2026-10-13 00:00:00', end='2026-10-16 23:59:59'))))
    return authority, payload, path


def test_previous_disjoint_failed_segment_is_not_current_verdict(tmp_path):
    authority, payload, _ = fixture(tmp_path)
    current, old = partition_failures(authority, payload, {})
    assert current == {} and set(old) == {'1'}
    assert old['1']['prior_window']['end'] == '2026-09-30 23:59:59'


@pytest.mark.parametrize('protected', [{'1':'success'}, {'1':'unknown'}])
def test_protected_items_never_requeued(tmp_path, protected):
    authority, payload, _ = fixture(tmp_path)
    assert partition_failures(authority, payload, protected) == ({}, {})


@pytest.mark.parametrize('status', ['success', 'unknown'])
def test_latest_nonfailed_attempt_never_requeued(tmp_path, status):
    authority, payload, _ = fixture(tmp_path, status=status)
    assert partition_failures(authority, payload, {}) == ({}, {})


def test_overlapping_failure_stays_current(tmp_path):
    authority, payload, _ = fixture(tmp_path, old_end='2026-10-14 00:00:00')
    assert partition_failures(authority, payload, {}) == ({'1':'failed'}, {})


def test_without_daily_binding_failure_remains_held(tmp_path):
    authority, payload, _ = fixture(tmp_path)
    payload.pop('time_binding')
    assert partition_failures(authority, payload, {}) == ({'1':'failed'}, {})


def test_missing_evidence_is_not_fresh_work(tmp_path):
    authority, payload, _ = fixture(tmp_path, evidence=False)
    assert partition_failures(authority, payload, {}) == ({'1':'failed'}, {})


def test_changed_receipt_is_rejected(tmp_path):
    authority, payload, path = fixture(tmp_path)
    path.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='historical_failure_terminal_changed'):
        partition_failures(authority, payload, {})


def test_latest_failure_wins_not_old_disjoint_row(tmp_path):
    authority, payload, _ = fixture(tmp_path)
    authority.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',
                        ('1','failed','new','new:1',None,CAMPAIGN,'signup'))
    assert partition_failures(authority,payload,{}) == ({'1':'failed'}, {})


def test_apply_plan_clears_only_old_selected_interpretation():
    from campaign_segment_failure_scope import apply_checkpoint_plan
    state = dict(exceptions={'1':[{'reason':'old'}], '2':[{'reason':'current'}]},
                 success={'3':'original-proof'}, previously_enrolled=['3'], pending=[], round=4,
                 corrections={'1':['old-correction'], '2':['current-correction']},
                 legacy_failures={'1':['old-error']}, repairs_seen={'1':['original-signature']})
    apply_checkpoint_plan(state, {'pending':['1']})
    assert state['pending'] == ['1'] and state['round'] == 5
    assert state['exceptions'] == {'2':[{'reason':'current'}]}
    assert state['corrections'] == {'2':['current-correction']}
    assert state['success'] == {'3':'original-proof'}
    assert state['repairs_seen'] == {'1':['original-signature']}


@pytest.mark.parametrize('change', ['success', 'previously_enrolled', 'missing_exception'])
def test_apply_plan_rejects_changed_scope(change):
    from campaign_segment_failure_scope import apply_checkpoint_plan
    state = dict(exceptions={'1':[]}, success={}, previously_enrolled=[], round=1)
    if change == 'success': state['success']['1'] = 'proof'
    elif change == 'previously_enrolled': state['previously_enrolled'] = ['1']
    else: state['exceptions'] = {}
    with pytest.raises(ValueError, match='segment_recovery_scope_changed'):
        apply_checkpoint_plan(state, {'pending':['1']})
