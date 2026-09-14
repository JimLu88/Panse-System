import sys
from pathlib import Path
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_discount_reuse import verify_partial_added_scope

def test_unverified_extra_and_removed_rows_rejected():
    old=[dict(item='1',sku='2',deduct='10')]
    for rows in [[],old+[dict(item='1',sku='3',deduct='20')],old+old]:
        with pytest.raises(ValueError):verify_partial_added_scope({'rows':rows},old)
    verify_partial_added_scope({'rows':old},old)

def test_only_pinned_saved_new_row_may_extend_partial(monkeypatch):
    import campaign_discount_include
    old=[dict(item='1',sku='2',deduct='10')];new=dict(item='1',sku='3',deduct='20')
    row=dict(state='verified_saved',item='1',window=dict(offer_id='o',start='a',end='b'),values={'3':'20'})
    raw=dict(state='verified_saved',claim_id='c',rows=[row])
    job=dict(operation='discount_include',state='finished',result=deepcopy(raw))
    monkeypatch.setattr(campaign_discount_include,'pinned',lambda ref:job if ref['path']=='job' else raw)
    offer=dict(offer_id='o',start='a',end='b',rows=old+[new],verified_include_evidence=[dict(rows=[new],receipt=dict(path='job',original=dict(path='raw')))])
    verify_partial_added_scope(offer,old)
    offer['rows'][1]['deduct']='21'
    with pytest.raises(ValueError):verify_partial_added_scope(offer,old)
