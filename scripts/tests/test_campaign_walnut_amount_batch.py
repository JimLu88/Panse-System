import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_walnut_amount_batch as b


def transport(edge):
    obj = object.__new__(b.BoundTransport)
    obj.edge = edge
    obj.release_guard = SimpleNamespace(verify=lambda: None)
    return obj


def test_intent_written_before_post_and_unknown_never_reposted(tmp_path):
    count = []
    payload = dict(identity=b.IDENTITY, claim_id='a'*32)
    jid = b.fingerprint(['discount_reprice', 'a'*32])
    def submit(step, value):
        assert (tmp_path / 'dispatch-intent.json').exists()
        count.append(step)
        raise b.EdgeJobError('uncertain')
    edge = SimpleNamespace(submit=submit, status=lambda job: dict(job_id=job, state='finished'))
    with pytest.raises(b.EdgeJobError): transport(edge).job('discount_reprice', payload, tmp_path)
    assert transport(edge).job('discount_reprice', payload, tmp_path)['job_id'] == jid
    assert count == ['discount_reprice']


def test_no_other_operation_or_scope(tmp_path):
    with pytest.raises(ValueError, match='amount_only'):
        transport(None).job('signup', dict(identity=b.IDENTITY, claim_id='a'*32), tmp_path)


def setup_batch(monkeypatch, failure=None):
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    a = SimpleNamespace(db=db)
    calls = []
    monkeypatch.setattr(b, 'derive_all', lambda: [dict(item=i, sku=i) for i in ('1', '2', '3')])
    monkeypatch.setattr(b, 'Policy', lambda i: SimpleNamespace(ITEM=i, GRANT='g'+i, SKUS=[i]))
    def claim(authority, policy):
        if failure == 'before' and policy.ITEM == '1': raise ValueError('missing_current_fact')
        calls.append(policy.ITEM)
        return {'claim_id': policy.ITEM}
    monkeypatch.setattr(b.core, 'claim', claim)
    monkeypatch.setattr(b.core, 'record', lambda *args: {'receipt': {'path': 'verified'}})
    class FakeTransport:
        def __init__(self, *args, **kwargs):
            self.release_guard = SimpleNamespace(evidence=lambda: {'test': True})
        def job(self, step, payload, folder):
            assert step == 'discount_reprice'
            if failure == 'unknown': raise b.EdgeJobError('unknown', 'job')
            return {'job_id': payload['claim_id'], 'state': 'finished'}
    monkeypatch.setattr(b, 'BoundTransport', FakeTransport)
    return a, calls


def test_one_run_all_items_and_terminal_restart_is_readonly(tmp_path, monkeypatch):
    a, calls = setup_batch(monkeypatch)
    result = b.execute(None, a, root=tmp_path)
    assert result['verified_items'] == 3
    assert calls == ['1', '2', '3']
    assert b.execute(None, a, root=tmp_path) == result
    assert calls == ['1', '2', '3']
    a.db.close()


def test_local_failure_does_not_drop_independent_items(tmp_path, monkeypatch):
    a, calls = setup_batch(monkeypatch, 'before')
    result = b.execute(None, a, root=tmp_path)
    assert result['items'][0]['state'] == 'blocked_before_dispatch'
    assert result['verified_items'] == 2 and calls == ['2', '3']
    a.db.close()


def test_transport_uncertainty_stops_queue_and_never_retries(tmp_path, monkeypatch):
    a, calls = setup_batch(monkeypatch, 'unknown')
    result = b.execute(None, a, root=tmp_path)
    assert calls == ['1']
    assert result['items'][0]['state'] == 'unknown_no_replay'
    assert result['items'][1]['state'] == 'not_started_after_transport_block'
    assert b.execute(None, a, root=tmp_path) == result
    assert calls == ['1']
    a.db.close()


def test_exclusive_runner_lock(tmp_path):
    with b.exclusive(tmp_path):
        with pytest.raises(ValueError, match='already_running'):
            with b.exclusive(tmp_path): pass
