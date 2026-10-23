from copy import deepcopy
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_walnut_revision as w


def fixture():
    offers = [dict(offer_id=w.OFFER, **w.WINDOW,
                   items=[{'item': '1', 'status': 'unknown'}],
                   rows=[dict(item='1', sku='old', erp_code='CODE', custom=False, deduct='10'),
                         dict(item='1', sku='custom', erp_code='CUSTOM', custom=True, deduct='20')])]
    rows = [dict(item='1', sku='new', erp_code='CODE', daily='100', old_deduct='15')]
    return offers, rows


def test_exact_code_reconciliation_is_ephemeral_and_preserves_unknown():
    offers, rows = fixture()
    before = deepcopy(offers)
    result = w.reconcile_view(offers, rows)
    assert offers == before
    assert result[0]['rows'][0]['sku'] == 'new'
    assert result[0]['rows'][0]['deduct'] == '15'
    assert result[0]['rows'][1] == before[0]['rows'][1]
    assert result[0]['items'] == before[0]['items']
    assert result[0]['historical_rows_before_current_fact_reconciliation'] == [before[0]['rows'][0]]


@pytest.mark.parametrize('bad', ['code', 'duplicate', 'custom', 'offer', 'window', 'collision'])
def test_reconciliation_does_not_guess_or_expand(bad):
    offers, rows = fixture()
    if bad == 'code': rows[0]['erp_code'] = 'NO'
    if bad == 'duplicate': offers[0]['rows'].append(deepcopy(offers[0]['rows'][0]))
    if bad == 'custom': offers[0]['rows'][0]['custom'] = True
    if bad == 'offer': offers[0]['offer_id'] = 'NO'
    if bad == 'window': offers[0]['end'] = '2027-01-01 00:00:00'
    if bad == 'collision': offers[0]['rows'][1]['sku'] = 'new'
    with pytest.raises(ValueError): w.reconcile_view(offers, rows)


def test_unrecognized_grant_is_rejected():
    with pytest.raises(ValueError, match='grant_unknown'):
        w.engine('september-grant')


def test_verified_overlay_requires_saved_proof_and_preserves_history(tmp_path, monkeypatch):
    import sqlite3
    import json
    from types import SimpleNamespace
    offers, rows = fixture()
    rows[0].update(new_deduct='25', deduct='25', target='60', final='60', offer_id=w.OFFER)
    monkeypatch.setattr(w, 'derive_all', lambda: deepcopy(rows))
    policy = w.Policy('1')
    body = policy.derive()
    proof = dict(state='verified_saved', claim_id='a'*32, job_id='b'*64,
                 rows=[dict(item='1', offer_id=w.OFFER, state='verified_saved',
                            window=dict(w.WINDOW, offer_id=w.OFFER),
                            before={'new': '15'}, values={'new': '25'})])
    path = tmp_path / 'proof.json'
    path.write_text(json.dumps(proof))
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    a = SimpleNamespace(db=db)
    w.core.table(a)
    ref = {'path': str(path), 'sha256': w.file_sha(path)}
    db.execute('INSERT INTO campaign_cost_revisions VALUES(?,?,?,?,?,?)',
               ('a'*32, policy.GRANT, json.dumps(body), 'verified', 'b'*64, json.dumps(ref)))
    result = w.verified_overlay(a, offers)
    assert result[0]['rows'][0]['deduct'] == '25'
    assert offers[0]['rows'][0]['sku'] == 'old'
    assert result[0]['items'][0]['status'] == 'unknown'
    assert result[0]['rows'][1] == offers[0]['rows'][1]
    path.write_text('{}')
    with pytest.raises(ValueError, match='receipt_changed'):
        w.verified_overlay(a, offers)
    db.close()
