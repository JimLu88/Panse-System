from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_autumn_cost_revision as g
import campaign_cost_revision as c
from test_campaign_cost_revision import authority


def inputs():
    erp=[dict(item=g.ITEM,sku=s,alt=[],code='ROW'+str(i),custom=False,
              daily=str(1000+i*10),medium_target='750',big_target='650') for i,s in enumerate(g.SKUS)]
    snapshot=dict(all_erp_rows=erp,resolved_price_version_sha256=c.digest(erp))
    read=dict(state='readback',platform_write=False,shop_name='畔色木作',price_window=deepcopy(g.WINDOW),rows=[dict(
        item=g.ITEM,window=dict(g.WINDOW,offer_id=g.OFFER),values={s:'100' for s in g.SKUS},
        not_enrolled=[],requested_scope_verified=True)])
    return snapshot,read


def body():
    s,r=inputs()
    with patch.object(g,'VERSION',s['resolved_price_version_sha256']):rows=g.calculate(s,r)
    return dict(grant_id=g.GRANT,campaign=g.CAMPAIGN,**g.WINDOW,rows=rows,sources=[],price_version=s['resolved_price_version_sha256'])


def test_autumn_uses_12_percent_big_target_without_daily_changes():
    b=body()
    assert b['rows'][0]['daily']=='1000'
    assert b['rows'][0]['official_cut']=='120'
    assert b['rows'][0]['new_deduct']=='230'
    assert all(Decimal(r['final'])==Decimal(r['big_target']) for r in b['rows'])
    assert c.OFFER=='144881379873' and c.TARGET=='medium'
    assert c.WINDOW['end']=='2026-09-16 19:59:59'


@pytest.mark.parametrize('bad',['item','sku','extra','grant','money','window','parent','rule'])
def test_request_cannot_expand_current_user_grant(bad):
    r=deepcopy(g.REQUEST)
    if bad=='item':r['items']=['793052650673']
    if bad=='sku':r['skus'][0]='5602711422165'
    if bad=='extra':r['skus'].append('6056644376634')
    if bad=='grant':r['grant_id']=c.GRANT
    if bad=='money':r['price']='1'
    if bad=='window':r['end']='2026-09-30 23:59:59'
    if bad=='parent':r['parent_request_id']='f'*64
    if bad=='rule':r['rule_sha']='f'*64
    with pytest.raises(ValueError):g.validate_request(r)


@pytest.mark.parametrize('bad',['daily_offer','daily_window','extra_custom','missing','custom','nan'])
def test_read_scope_and_generator_do_not_accept_other_prices(bad):
    s,r=inputs();group=r['rows'][0]
    if bad=='daily_offer':group['window']['offer_id']=c.OFFER
    if bad=='daily_window':r['price_window']=c.WINDOW
    if bad=='extra_custom':group['values']['5602711422165']='1'
    if bad=='missing':group['values'].pop(g.SKUS[0])
    if bad=='custom':s['all_erp_rows'][0]['custom']=True;s['resolved_price_version_sha256']=c.digest(s['all_erp_rows'])
    if bad=='nan':group['values'][g.SKUS[0]]='NaN'
    with patch.object(g,'VERSION',s['resolved_price_version_sha256']),pytest.raises(ValueError):g.calculate(s,r)


def test_autumn_claim_once_verifier_selects_exact_grant():
    a=authority();b=body()
    with patch.object(g,'derive',return_value=b),patch.object(g,'check_current'):
        claim=c.claim(a,policy=g)
        assert c.engine(g.GRANT) is g and c.engine(c.GRANT) is c
        assert c.verify(a,claim['claim_id'])['campaign']==g.CAMPAIGN
        c.verify(a,claim['claim_id'],consume_job=c.fingerprint(['discount_reprice',claim['claim_id']]))
        with pytest.raises(ValueError,match='not_fresh'):c.verify(a,claim['claim_id'])
    with pytest.raises(ValueError):c.engine('unapproved')
    a.db.close()


def test_autumn_save_proof_cannot_be_satisfied_by_daily_receipt():
    b=body();proof=dict(state='verified_saved',claim_id='a'*32,job_id='b'*64,rows=[dict(
        item=g.ITEM,offer_id=g.OFFER,state='verified_saved',window=dict(g.WINDOW,offer_id=g.OFFER),
        before={r['sku']:r['old_deduct'] for r in b['rows']},values={r['sku']:r['new_deduct'] for r in b['rows']})])
    g.validate_proof(b,'a'*32,'b'*64,proof)
    proof['rows'][0]['offer_id']=c.OFFER
    with pytest.raises(ValueError):g.validate_proof(b,'a'*32,'b'*64,proof)
