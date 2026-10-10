import sys
from pathlib import Path
from decimal import Decimal
import pytest
sys.path.insert(0,'D:/AI/畔色ERP系统/ERP程序/scripts')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_new_three_revision as p

def test_exact_21_ordinary_rows_and_target():
    rows=[r for item in p.ITEMS for r in p.Policy(item).derive()['rows']]
    assert len(rows)==21
    assert len({(r['item'],r['sku']) for r in rows})==21
    for r in rows:
        assert r['offer_id']==p.OFFER
        assert Decimal(r['daily'])-p.official_cut(Decimal(r['daily']),Decimal('.12'))-Decimal(r['new_deduct'])==Decimal(r['target'])

def test_unknown_grant_and_item_rejected():
    for item in ('1234567890','',p.ITEMS[0]+'0'):
        with pytest.raises(ValueError):p.Policy(item)
    with pytest.raises(ValueError):p.engine('double11:'+p.ITEMS[0])

def test_changed_evidence_rejected(monkeypatch):
    monkeypatch.setattr(p,'PINS',{'snapshot.json':'0'*64})
    with pytest.raises(ValueError,match='pinned_evidence_changed'):p.Policy(p.ITEMS[0])

def test_unknown_or_wrong_saved_amount_not_success():
    policy=p.Policy(p.ITEMS[0]);body=policy.derive()
    with pytest.raises(ValueError):policy.validate_proof(body,'c','j',{'state':'unknown'})

def test_custom_kept_not_repriced_or_other_campaign_admitted():
    row={'item':p.ITEMS[0],'sku':'6308609774683','activity_price':'2000.00'}
    actual=[{'deduct':'200.00'}];offer={'offer_id':p.OFFER}
    assert p.unchanged_custom_reuse(row,actual,offer,p.CAMPAIGN,**p.WINDOW)
    assert p.unchanged_custom_reuse(row,actual,offer,'49646/49651/3555037899',**p.WINDOW) is None
    with pytest.raises(ValueError):p.unchanged_custom_reuse(dict(row,activity_price='500'),actual,offer,p.CAMPAIGN,**p.WINDOW)
    with pytest.raises(ValueError):p.unchanged_custom_reuse(row,[{'deduct':'201'}],offer,p.CAMPAIGN,**p.WINDOW)
