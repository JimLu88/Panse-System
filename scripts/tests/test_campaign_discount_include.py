import sys
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_discount_include as module


def test_claim_is_single_use_and_unknown_locks_exact_item(tmp_path,monkeypatch):
    db=sqlite3.connect(tmp_path/'test.sqlite',isolation_level=None);db.row_factory=sqlite3.Row
    a=SimpleNamespace(db=db)
    request=dict(offer_id='144956016253')
    body=dict(request=request,campaign='1/2/3',start='start',end='end',shop='shop',rows=[
        dict(item='720234422814',sku='6135325229596',offer_id='144956016253',deduct='25.00')])
    monkeypatch.setattr(module,'derive',lambda a,r:deepcopy(body))
    claim=module.prepare(a,request);cid=claim['claim_id']
    with pytest.raises(ValueError,match='never_replay'):module.prepare(a,request)
    assert module.verify(a,cid)['dispatch_consumed'] is False
    assert module.verify(a,cid,'a'*64)['dispatch_consumed'] is True
    with pytest.raises(ValueError,match='no_replay'):module.verify(a,cid,'a'*64)
    offers=[dict(offer_id='144956016253',start='start',end='end',
        rows=[],items=[dict(item='720234422814',status='success'),dict(item='other',status='success')])]
    result=module.overlay(a,offers)
    assert result[0]['items'][0]['status']=='unknown'
    assert result[0]['items'][1]['status']=='success'
    assert offers[0]['items'][0]['status']=='success'
    db.close()


def test_changed_plan_or_invalid_dispatch_does_not_consume(tmp_path,monkeypatch):
    db=sqlite3.connect(tmp_path/'test.sqlite',isolation_level=None);db.row_factory=sqlite3.Row
    a=SimpleNamespace(db=db);body=dict(request={},rows=[dict(item='1',sku='2',offer_id='3',deduct='1')])
    monkeypatch.setattr(module,'derive',lambda a,r:deepcopy(body))
    cid=module.prepare(a,{})['claim_id']
    with pytest.raises(ValueError,match='invalid_job'):module.verify(a,cid,'bad')
    body['rows'][0]['deduct']='2'
    with pytest.raises(ValueError,match='changed'):module.verify(a,cid,'a'*64)
    assert db.execute('SELECT state FROM missing_discount_includes').fetchone()[0]=='ready'
    db.close()
