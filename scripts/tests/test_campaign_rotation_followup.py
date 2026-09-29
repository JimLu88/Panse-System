import json
from pathlib import Path
import sys
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_rotation_followup as f


def seed():
    bed=[dict(item=f.BED,sku=s,original_sku=s,activity_price='217.50') for s in sorted(f.ACCESSORIES)]
    bed += [dict(item=f.BED,sku=str(7000000000000+n),original_sku=str(7000000000000+n),activity_price='1000.00') for n in range(12)]
    receipt=dict(activity_rows=bed,conditional_round_rows=[dict(item='918692510350',sku=str(8000000000000+n),deduct='50') for n in range(4)],missing_price_rows=[{}]*4)
    facts=[dict(item=f.BED,sku=s,attributes='床板材质:榉木;颜色分类:mini床头柜-配件;') for s in f.ACCESSORIES]
    erp=[]
    for n,s in enumerate(sorted(f.ROCK_ORDINARY)):
        old_sku='6126676768463' if s=='6141883480156' else s
        facts.append(dict(item=f.ROCK,sku=s,sku_code='code'+str(n),price='300.00'))
        erp.append(dict(item=f.ROCK,sku=old_sku,alt=[],code='code'+str(n),custom=False,medium_target='150.00',big_target='140.00'))
    data=dict(scope=dict(sku_facts=[dict(facts=x) for x in facts]),mapping=dict(rows=[dict(item=f.ROCK,new_sku='6141883480156',old_sku='6126676768463')]),snapshot=dict(all_erp_rows=erp))
    result=dict(status='failed',no_sales_items=[f.ROCK])
    protection=dict(signup_pairs=set(),signup_items=set(),no_sales_items=set(),historical_changed_scope_candidates=[])
    old=dict(rows=[dict(item=f.BED,sku=s,deduct='59.12',frozen_target='136.63',calculated_final='136.38') for s in f.ACCESSORIES])
    return receipt,data,result,protection,old


def test_one_batch_keeps_bed_full_and_removes_rock_activity():
    r=f.calculate(*seed())
    assert len(r['activity_rows'])==14 and {x['item'] for x in r['activity_rows']}=={f.BED}
    assert len(r['accessory_rows'])==2 and len(r['no_official_rows'])==9
    assert len([x for x in r['no_official_rows'] if x['item']==f.ROCK])==5
    assert all(x['deduct']=='59.12' and x['target']=='136.63' and x['calculated_final']=='136.38'
               and not x['additional_discount_authorized'] for x in r['accessory_rows'])
    assert all(x['deduct'] is None and x['base'] is None for x in r['no_official_rows'])
    assert all(x['reason']=='effective_sku_price_composition_required_no_g_fallback' for x in r['no_official_rows'])
    assert r['prior_nine_discount_not_replayed'] and not r['upload_ready'] and not r['platform_write']


def test_build_omits_g_fallback_file_even_for_old_numeric_plan(monkeypatch):
    plan=dict(activity_rows=[],accessory_rows=[dict(deduct='59.12')],no_official_rows=[dict(deduct='5156.73')])
    monkeypatch.setattr(f,'calculate',lambda *args:plan)
    monkeypatch.setattr(f.c,'load_fixed_discount_template',lambda:b'fixed')
    calls=[]
    def writer(master,rows):
        assert rows is plan['accessory_rows']
        calls.append(rows)
        return b'accessories-only'
    monkeypatch.setattr(f.c,'fill_single_discount_rows',writer)
    _,files=f.build(None,None,None,None,None)
    assert len(calls)==1 and len(files)==1
    assert not any('无官方' in name for name in files)


@pytest.mark.parametrize('change',['result','no_sales','bed_partial','bed_duplicate','accessory_deduct',
    'target','final','name','accessory_price','mapping','custom','big_floor','negative','round'])
def test_changed_constraints_fail_closed(change):
    receipt,d,result,p,old=seed()
    if change=='result':result['status']='unknown'
    if change=='no_sales':result['no_sales_items']=[]
    if change=='bed_partial':receipt['activity_rows'].pop()
    if change=='bed_duplicate':receipt['activity_rows'][-1]=deepcopy(receipt['activity_rows'][0])
    if change=='accessory_deduct':old['rows'][0]['deduct']='118.24'
    if change=='target':old['rows'][0]['frozen_target']='136.38'
    if change=='final':old['rows'][0]['calculated_final']='136.63'
    if change=='name':d['scope']['sku_facts'][0]['facts']['attributes']='other'
    if change=='accessory_price':receipt['activity_rows'][0]['activity_price']='218.00'
    if change=='mapping':d['snapshot']['all_erp_rows'][0]['code']='different'
    if change=='custom':d['snapshot']['all_erp_rows'][0]['custom']=True
    if change=='big_floor':d['snapshot']['all_erp_rows'][0]['big_target']='160.00'
    if change=='negative':d['snapshot']['all_erp_rows'][0]['medium_target']='310.00'
    if change=='round':receipt['conditional_round_rows'].pop()
    with pytest.raises(ValueError):f.calculate(receipt,d,result,p,old)


@pytest.mark.parametrize('kind',['pair','item','sales','historical'])
def test_success_unknown_protect_whole_bed(kind):
    receipt,d,result,p,old=seed()
    if kind=='pair':p['signup_pairs'].add((f.BED,receipt['activity_rows'][0]['sku']))
    if kind in ('item','historical'):p['signup_items'].add(f.BED)
    if kind=='sales':p['no_sales_items'].add(f.BED)
    if kind=='historical':p['historical_changed_scope_candidates']=[f.BED]
    r=f.calculate(receipt,d,result,p,old)
    assert len(r['activity_rows'])==(14 if kind=='historical' else 0)
    assert len(r['accessory_rows'])==2  # Still HOLD, not a released discount retry.


def parsed():
    warning='mini床头柜-配件;松木 mini床头柜-配件;榉木 '+('活动普惠券后价：195.50元，最低普惠券后价：136.38元 '*2)
    rows=[dict(item=f.BED,failure_kind='other_official_failure',warning=warning) for _ in range(14)]
    rows += [dict(item=f.ROCK,failure_kind='no_sales_this_campaign') for _ in range(7)]
    return dict(status='failed',rows=rows,no_sales_items=[f.ROCK])


@pytest.mark.parametrize('change',['none','success','unknown','partial','classification','constraint','wrong_accessory','no_sales'])
def test_full_official_terminal_classification(monkeypatch,change):
    value=parsed()
    if change in ('success','unknown'):value['status']=change
    if change=='partial':value['rows'].pop()
    if change=='classification':value['rows'][-1]['failure_kind']='other_official_failure'
    if change=='constraint':value['rows'][0]['warning']=value['rows'][0]['warning'].replace('136.38','130.00')
    if change=='wrong_accessory':value['rows'][0]['warning']=value['rows'][0]['warning'].replace('mini床头柜','other')
    if change=='no_sales':value['no_sales_items']=[]
    monkeypatch.setattr(f,'import_terminal',lambda *_:value)
    if change=='none':
        r=f.terminal(dict(activity_rows=[]),b'fixture')
        assert not r['platform_write'] and r['discount_result'].startswith('unknown')
    else:
        with pytest.raises(ValueError):f.terminal(dict(activity_rows=[]),b'fixture')


def test_record_idempotent_preserves_old_files(tmp_path):
    sentinel=tmp_path/'reservation.json';sentinel.write_text('original')
    value=dict(status='failed',source={'sha256':'a'*64})
    path=f.record(value,tmp_path);assert f.record(value,tmp_path)==path
    assert sentinel.read_text()=='original' and len(list(tmp_path.glob('official-result-*.json')))==1
    with pytest.raises(ValueError):f.record(dict(value,status='success'),tmp_path)


def test_registered_pending_holds_do_not_replay(monkeypatch,tmp_path):
    monkeypatch.setattr(f,'LEDGER',tmp_path)
    pending=tmp_path/'failed-followup';pending.mkdir()
    rows=seed()[0]['activity_rows']
    (pending/'reservation.json').write_text(json.dumps(dict(result_sha256=f.RESULT_SHA,activity_rows=rows)),encoding='utf-8')
    pairs,sales=f.registered_protection()
    assert pairs=={(r['item'],r['sku']) for r in rows} and sales==set()


def test_registered_official_failure_consumed_not_rewritten(monkeypatch,tmp_path):
    monkeypatch.setattr(f,'LEDGER',tmp_path)
    value=dict(successful_pairs=[],unknown_pairs=[],no_sales_items=[f.ROCK])
    monkeypatch.setattr(f,'original',lambda:{})
    monkeypatch.setattr(f.c,'pinned',lambda *_:b'fixture')
    monkeypatch.setattr(f,'terminal',lambda *_:value)
    f.record(value,tmp_path)
    assert f.registered_protection()==(set(),{f.ROCK})
    (tmp_path/'official-result-21.json').write_text('{}')
    with pytest.raises(ValueError):f.registered_protection()


@pytest.mark.parametrize('fault',['none','wrong_item','partial','price','prior','status','failed','unknown'])
def test_bed_success_bound_to_original_fourteen(monkeypatch,fault):
    rows=seed()[0]['activity_rows']
    reservation=dict(result_sha256=f.RESULT_SHA,activity_rows=deepcopy(rows))
    receipt=dict(activity_rows=deepcopy(rows),official_terminal=dict(source=dict(sha256=f.RESULT_SHA)))
    result=dict(status='success',successful_pairs=[[r['item'],r['sku']] for r in rows],failed_pairs=[],unknown_pairs=[])
    if fault=='wrong_item':reservation['activity_rows'][0]['item']=f.ROCK
    if fault=='partial':reservation['activity_rows'].pop()
    if fault=='price':reservation['activity_rows'][0]['activity_price']='218.00'
    if fault=='prior':receipt['official_terminal']['source']['sha256']='bad'
    if fault=='status':result['status']='unknown'
    if fault=='failed':result['failed_pairs']=[['x','y']]
    if fault=='unknown':result['unknown_pairs']=[['x','y']]
    monkeypatch.setattr(f,'import_terminal',lambda request,raw:result)
    if fault=='none':
        value=f.bed_success_value(reservation,receipt,b'fixture')
        assert len(value['successful_pairs'])==14 and not value['whole_event_complete']
        assert value['remaining_no_official_rows']==9 and not value['platform_write']
    else:
        with pytest.raises(ValueError):f.bed_success_value(reservation,receipt,b'fixture')


def test_success_record_only_appends_and_is_idempotent(monkeypatch,tmp_path):
    folder=tmp_path/'failed-followup';folder.mkdir()
    original=tmp_path/'official-result-21.json';original.write_text('old failed result')
    reservation=folder/'reservation.json';reservation.write_text('old reserved scope')
    value=dict(status='success',successful_pairs=[['item','sku']],source={'sha256':'a'*64})
    monkeypatch.setattr(f,'read_bed_success',lambda *_:value)
    first=f.record_bed_success(tmp_path);second=f.record_bed_success(tmp_path)
    assert first==second and first['successful_rows']==14 and not first['whole_event_complete']
    assert original.read_text()=='old failed result' and reservation.read_text()=='old reserved scope'
    saved=folder/'official-result-bed14.json';saved.write_text('{}')
    with pytest.raises(ValueError):f.record_bed_success(tmp_path)


def test_moved_original_availability_does_not_change_official_identity(tmp_path):
    original=dict(status='failed',source={'sha256':'a'*64},missing_original_files=[],verified_original_files=[{'path':'old'}])
    f.record(original,tmp_path)
    now=dict(original,missing_original_files=[{'path':'old'}],verified_original_files=[])
    f.record(now,tmp_path)
    assert json.loads((tmp_path/'official-result-21.json').read_text())==original
    with pytest.raises(ValueError):f.record(dict(now,status='success'),tmp_path)
