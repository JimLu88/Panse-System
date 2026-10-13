from decimal import Decimal
from copy import deepcopy
import pytest
import campaign_lift_oct13_repair as c


def test_exact_seven_small_changes_and_four_custom_preserved():
    doc=c.source();specs,prices,wanted=c.derive(doc)
    old=doc['original_offer_readback']['values']
    assert len(specs)==8 and len(prices)==12
    assert sum(Decimal(v)!=Decimal(old[s]) for s,v in wanted.items())==7
    assert wanted['6241447059630']==old['6241447059630']
    assert all(Decimal(v)-Decimal(old[s]) in map(Decimal,['0','.25','.50','.75']) for s,v in wanted.items())
    assert set(prices)-set(specs)=={'6301497267888','5916518666444','6137591788516','6137591788517'}
    assert len(c.adjustment(doc['original_offer_readback'],wanted))==7


def test_changed_current_amount_or_window_cannot_write():
    doc=c.source();read=deepcopy(doc['original_offer_readback'])
    read['values']['6241447059623']='1'
    with pytest.raises(ValueError,match='drift'):c.adjustment(read,c.deductions())
    read=deepcopy(doc['original_offer_readback']);read['window']['end']='2026-11-13 23:59:59'
    with pytest.raises(ValueError,match='identity'):c.adjustment(read,c.deductions())


def test_saved_amounts_do_not_get_adjusted_twice():
    read=deepcopy(c.source()['original_offer_readback']);read['values'].update(c.deductions())
    assert c.adjustment(read,c.deductions())=={}
