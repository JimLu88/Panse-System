from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_price_incident_review as c


def seeds():
    first=[]
    for n,item in enumerate(c.OFFERS['147717819883']):
        for j in range(42 if n==0 else 1):
            first.append(dict(item=item,sku=str(6000000000000+n*100+j),erp_code='code',frozen_target='5423.27',
                              base='999999',deduct='999'))
    second=[]
    for n,item in enumerate(c.OFFERS['147928680448']):
        for j in range(5 if n==0 else 4):
            second.append(dict(item=item,sku=str(6100000000000+n*100+j),erp_code='table',target='1881.33',base='wrong'))
    user=dict(reported_withdrawals=deepcopy(c.OFFERS),reported_relisted_item='1035582527998',
              independently_verified=False,reupload_old_files_allowed=False)
    return user,dict(rows=first),dict(no_official_rows=second)


def doc():
    rows=c.source_rows(*seeds());scope={}
    for r in rows:scope.setdefault(r['item'],[]).append(r['sku'])
    return dict(rows=rows,request=dict(payload=dict(items=sorted(scope),sku_scope=scope,price_window=dict(c.WINDOW))),
                relisting=dict(item='1035582527998',user_reported=True,independently_verified=False))


def review_args():
    d=doc();p=d['request']['payload'];lists={(i,m):dict(complete=True,offers=[]) for i in p['items'] for m in ('商品级','SKU级')}
    surface=dict(rows=[dict(item=r['item'],sku=r['sku'],gap='missing_effective_composition',composition_complete=False) for r in d['rows']])
    return [d,{},lists,surface]


def test_exact_frozen_targets_no_legacy_base_or_deduction():
    rows=c.source_rows(*seeds())
    assert len(rows)==59 and len({r['item'] for r in rows})==11
    assert all(r['effective_base'] is None and r['replacement_deduction'] is None for r in rows)
    assert all('base' not in r and 'deduct' not in r for r in rows)


@pytest.mark.parametrize('fault',['scope','relisting','verified','old_file','missing50','missing9','duplicate','target'])
def test_bad_scope_rejected(fault):
    user,old,tables=seeds()
    if fault=='scope':user['reported_withdrawals']['147717819883'].pop()
    elif fault=='relisting':user['reported_relisted_item']='other'
    elif fault=='verified':user['independently_verified']=True
    elif fault=='old_file':user['reupload_old_files_allowed']=True
    elif fault=='missing50':old['rows'].pop()
    elif fault=='missing9':tables['no_official_rows'].pop()
    elif fault=='duplicate':old['rows'][1]['sku']=old['rows'][0]['sku']
    else:old['rows'][0]['frozen_target']='NaN'
    with pytest.raises(ValueError):c.source_rows(user,old,tables)


def test_confirmed_absence_is_not_generation_permission():
    args=review_args();before=deepcopy(args);r=c.consume(*args)
    assert r['counts']['verified_withdrawal_skus']==59
    assert r['counts']['upload_ready_skus']==0 and not r['files'] and args==before
    assert r['original_success_unknown_retained'] and r['old_327_and_two_tables_9_files_not_reused']


@pytest.mark.parametrize('mode',['商品级','SKU级'])
def test_incomplete_list_not_withdrawal(mode):
    args=review_args();item=args[0]['rows'][0]['item'];args[2].pop((item,mode))
    r=c.consume(*args)
    assert all(not x['withdrawal_member_absent_verified'] for x in r['rows'] if x['item']==item)


def test_original_offer_still_present_is_not_withdrawn():
    args=review_args();first=args[0]['rows'][0]
    args[2][first['item'],'SKU级']['offers']=[dict(offer_id=first['original_offer_id'],**c.WINDOW)]
    r=c.consume(*args)
    assert r['rows'][0]['original_offer_still_displayed'] and not r['rows'][0]['withdrawal_member_absent_verified']


def test_other_overlap_is_retained_even_when_original_absent():
    args=review_args();item=args[0]['rows'][0]['item']
    args[2][item,'商品级']['offers']=[dict(offer_id='999999999999',**c.WINDOW)]
    r=c.consume(*args)
    assert r['rows'][0]['withdrawal_member_absent_verified']
    assert r['rows'][0]['other_overlap_offer_ids']==['999999999999']
    assert not r['rows'][0]['upload_ready']


@pytest.mark.parametrize('fault',['missing','duplicate','foreign'])
def test_missing_or_foreign_price_rows_block(fault):
    args=review_args()
    if fault=='missing':args[3]['rows'].pop()
    elif fault=='duplicate':args[3]['rows'].append(args[3]['rows'][0])
    else:args[3]['rows'][0]['sku']='foreign'
    with pytest.raises(ValueError):c.consume(*args)


def test_caller_ready_flag_does_not_enable_price_generation():
    args=review_args()
    for r in args[3]['rows']:r.update(composition_complete=True,effective_base='10580',replacement_deduction='5156.73')
    r=c.consume(*args)
    assert all(x['effective_base'] is None and x['replacement_deduction'] is None and not x['upload_ready'] for x in r['rows'])


def test_new_request_retains_relisted_bed_without_erp_writes():
    r=c.consume(*review_args())
    assert any(x['item']=='1035582527998' for x in r['rows'])
    assert not r['relisting']['independently_verified'] and not r['database_write']


def test_existing_artifact_is_never_overwritten(tmp_path):
    path=tmp_path/'result.json';c.write_new(path,{'first':1})
    with pytest.raises(FileExistsError):c.write_new(path,{'second':2})
    assert json.loads(path.read_text())=={'first':1}


def test_existing_capture_receipt_stops_before_token_or_network(tmp_path,monkeypatch):
    path=tmp_path/'receipt.json';c.write_new(path,dict(stage='admitted_unknown'))
    monkeypatch.setattr(c,'verified_request',lambda _: (dict(request={}),{}))
    with pytest.raises(FileExistsError):c.capture('unused',path)
    assert json.loads(path.read_text())['stage']=='admitted_unknown'


def test_old_job_cannot_be_substituted_for_post_withdrawal_read(monkeypatch):
    d=doc();d['request']['payload'].update(identity=c.IDENTITY,read_request_id='a'*64)
    monkeypatch.setattr(c,'verified_request',lambda _: (d,{}))
    with pytest.raises(ValueError,match='exact_new_incident_read'):
        c.review('unused','807d6fd40f9fe97025b0d2bf2086e73ac88d410ae8667d367f761cdacd4e51ae')
