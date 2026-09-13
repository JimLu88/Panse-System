"""Isolated cost-version tests. No real claims, files or browser writes."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_cost_revision as c


def inputs():
    erp=[dict(item=c.ITEM,sku=s,alt=[],code='CODE'+str(i),custom=False,
              daily=str(1000+i*10),medium_target=str(700+i),big_target='650') for i,s in enumerate(c.SKUS)]
    snapshot={'all_erp_rows':erp,'resolved_price_version_sha256':c.digest(erp)}
    group=dict(item=c.ITEM,window=dict(c.WINDOW,offer_id=c.OFFER),values={s:'100' for s in c.SKUS},
               not_enrolled=sorted(c.EXCLUDED),requested_scope_verified=True)
    read=dict(state='readback',platform_write=False,shop_name='畔色木作',price_window=c.WINDOW,rows=[group])
    return snapshot,read


def body():
    s,r=inputs()
    with patch.object(c,'VERSION',s['resolved_price_version_sha256']):rows=c.calculate(s,r)
    return dict(grant_id=c.GRANT,campaign=c.CAMPAIGN,shop_name='畔色木作',**c.WINDOW,
                rows=rows,sources=[],price_version=s['resolved_price_version_sha256'])


def test_generator_exact_new_target_not_two_yuan():
    s,r=inputs()
    with patch.object(c,'VERSION',s['resolved_price_version_sha256']):rows=c.calculate(s,r)
    assert len(rows)==5 and all(x['custom'] is False for x in rows)
    assert rows[0]['new_deduct']=='200' and rows[0]['old_deduct']=='100'
    assert rows[0]['final']=='700' and rows[0]['daily']=='1000'


@pytest.mark.parametrize('bad',['version','custom','missing','extra','not_enrolled','window','item','offer','nan','snapshot_tamper'])
def test_exact_scope_rejects(bad):
    s,r=inputs();version=s['resolved_price_version_sha256'];g=r['rows'][0]
    if bad=='version':s['resolved_price_version_sha256']='bad'
    if bad=='custom':s['all_erp_rows'][0]['custom']=True;s['resolved_price_version_sha256']=version=c.digest(s['all_erp_rows'])
    if bad=='missing':g['values'].pop(c.SKUS[0])
    if bad=='extra':g['values']['123']='1'
    if bad=='not_enrolled':g['not_enrolled']=[]
    if bad=='window':g['window']['end']='2026-09-27 23:59:59'
    if bad=='item':g['item']='717418169535'
    if bad=='offer':g['window']['offer_id']='145963566885'
    if bad=='nan':g['values'][c.SKUS[0]]='NaN'
    if bad=='snapshot_tamper':s['all_erp_rows'][0]['daily']='1'
    with patch.object(c,'VERSION',version),pytest.raises(ValueError):c.calculate(s,r)


def authority():
    db=sqlite3.connect(':memory:',isolation_level=None);db.row_factory=sqlite3.Row
    return SimpleNamespace(db=db)


def test_claim_idempotent_consume_once_no_unknown_retry():
    a=authority();b=body()
    with patch.object(c,'derive',return_value=b),patch.object(c,'check_current'):
        first=c.claim(a);assert c.claim(a)['claim_id']==first['claim_id']
        verified=c.verify(a,first['claim_id']);assert not verified['dispatch_consumed']
        consumed=c.verify(a,first['claim_id'],consume_job=c.fingerprint(['discount_reprice',first['claim_id']]));assert consumed['dispatch_consumed']
        with pytest.raises(ValueError,match='not_fresh'):c.verify(a,first['claim_id'])
        with pytest.raises(ValueError,match='do_not_replay'):c.claim(a)
    a.db.close()


def test_claim_body_change_rolls_back():
    a=authority();b=body()
    with patch.object(c,'derive',return_value=b),patch.object(c,'check_current'):
        cid=c.claim(a)['claim_id'];bad=deepcopy(b);bad['rows'][0]['new_deduct']='999'
        a.db.execute('UPDATE campaign_cost_revisions SET body=?',(json.dumps(bad),))
        with pytest.raises(ValueError,match='claim_changed'):c.verify(a,cid,consume_job=c.fingerprint(['discount_reprice',cid]))
        assert not a.db.in_transaction
        assert a.db.execute('SELECT state FROM campaign_cost_revisions').fetchone()[0]=='claimed_not_dispatched'
    a.db.close()


def test_unknown_overlay_blocks_old_reuse_and_preserves_future():
    a=authority();c.table(a);b=body()
    a.db.execute('INSERT INTO campaign_cost_revisions VALUES(?,?,?,?,?,NULL)',('a'*32,c.GRANT,json.dumps(b),'dispatched_unknown','b'*64))
    offers=[dict(offer_id=c.OFFER,**c.WINDOW,items=[dict(item=c.ITEM,status='success')],rows=[]),
            dict(offer_id='144956016253',start='2026-09-16 20:00:00',end='2026-09-27 23:59:59',items=[dict(item=c.ITEM,status='success')],rows=[])]
    assert c.overlay(a,offers)[0]['items'][0]['status']=='unknown'
    assert offers[1]['items'][0]['status']=='success'
    a.db.close()


def test_verified_overlay_changes_only_five_original_amounts(tmp_path):
    a=authority();c.table(a);b=body();p=saved(b);path=tmp_path/'saved.json';path.write_text(json.dumps(p))
    ref=dict(path=str(path),sha256=c.file_sha(path))
    a.db.execute('INSERT INTO campaign_cost_revisions VALUES(?,?,?,?,?,?)',('a'*32,c.GRANT,json.dumps(b),'verified','b'*64,json.dumps(ref)))
    offers=[dict(offer_id=c.OFFER,**c.WINDOW,items=[dict(item=c.ITEM,status='success')],
        rows=[dict(item=r['item'],sku=r['sku'],deduct=r['old_deduct']) for r in b['rows']]+[dict(item='717418169535',sku='999',deduct='100')])]
    with patch.object(c,'derive',return_value=b):c.overlay(a,offers)
    assert offers[0]['rows'][0]['deduct']==b['rows'][0]['new_deduct']
    assert offers[0]['rows'][-1]['deduct']=='100'
    a.db.close()


def saved(b):
    return dict(state='verified_saved',claim_id='a'*32,job_id='b'*64,rows=[dict(
        item=c.ITEM,offer_id=c.OFFER,state='verified_saved',window=dict(c.WINDOW,offer_id=c.OFFER),
        before={r['sku']:r['old_deduct'] for r in b['rows']},
        values={r['sku']:r['new_deduct'] for r in b['rows']})])


@pytest.mark.parametrize('bad',['none','amount','old','missing','offer','window','state','claim'])
def test_saved_proof_cannot_forge_scope_or_values(bad):
    b=body();p=saved(b);g=p['rows'][0]
    if bad=='amount':g['values'][c.SKUS[0]]='0'
    if bad=='old':g['before'][c.SKUS[0]]='0'
    if bad=='missing':g['values'].pop(c.SKUS[-1])
    if bad=='offer':g['offer_id']='145963566885'
    if bad=='window':g['window']['end']='2026-09-30 23:59:59'
    if bad=='state':p['state']='unknown'
    if bad=='claim':p['claim_id']='c'*32
    if bad=='none':c.validate_proof(b,'a'*32,'b'*64,p)
    else:
        with pytest.raises(ValueError):c.validate_proof(b,'a'*32,'b'*64,p)


@pytest.mark.parametrize('bad',['none','unknown','overlap','old','pending'])
def test_current_offer_guards(bad):
    a=authority();b=body();offers=[dict(offer_id=c.OFFER,**c.WINDOW,
        items=[dict(item=c.ITEM,status='success')],rows=[dict(item=r['item'],sku=r['sku'],deduct=r['old_deduct']) for r in b['rows']])]
    if bad=='overlap':offers.append(dict(offers[0],offer_id='999'))
    if bad=='old':offers[0]['rows'][0]['deduct']='1'
    if bad=='pending':
        a.db.execute('CREATE TABLE continuous_discount_repairs(body TEXT,state TEXT)')
        a.db.execute('INSERT INTO continuous_discount_repairs VALUES(?,?)',(json.dumps(b),'dispatched_unknown'))
    a.blocked=lambda *args,**kw:{c.ITEM:'unknown'} if bad=='unknown' else {}
    a.discount_offers=lambda:offers
    if bad=='none':c.check_current(a,b)
    else:
        with pytest.raises(ValueError):c.check_current(a,b)
    a.db.close()
