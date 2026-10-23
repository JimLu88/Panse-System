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
