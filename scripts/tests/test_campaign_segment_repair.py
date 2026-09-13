import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_segment_repair import derive, prepare
from campaign_entry_authority import file_sha


def fixture(tmp_path):
    def save(name,doc):
        p=tmp_path/name;p.write_text(json.dumps(doc),encoding='utf-8')
        return {'path':str(p),'sha256':file_sha(p)}
    start='2026-09-14 00:00:00';end='2026-09-16 19:59:59';campaign='legacy/itemApply/3172207691'
    snapshot=save('snapshot.json',{'resolved_price_version_sha256':'same',
        'all_erp_rows':[dict(code='sample',custom=False,daily='30.00',medium_target='21.02',big_target='20.00')]})
    terminal=save('terminal.json',dict(claim_id='a'*32,campaign=campaign,phase='signup',terminal=True,batch_id='123'))
    error=dict(item='1',sku='11',kind='coupon_price',batch='123',terminal='failed',official_evidence={'sha256':'a'*64},
               message='official cap',submitted_price='30.00',observed_final='21.02',official_cap='21.01')
    # Old future segment was repaired without changing the official result.
    # Preserve it; only the new actual before-window evidence is recomputed.
    report=save('report.json',dict(bundle_id='bundle',batch='123',source_terminal=terminal['path'],
        errors=[dict(error,kind='unknown',parse_issue='verified_price_adjustment_had_no_effect_on_official_price',
                     current_deduct='5.99',proposed_deduct='6.00',constraints=[error])]))
    read=save('read.json',dict(state='readback',platform_write=False,shop_name='shop',
        price_window=dict(start=start,end=end),rows=[dict(item='1',values={'11':'5.98'},
            window=dict(start=start,end=end,offer_id='offer'))]))
    body=dict(campaign=campaign,target='medium',official_rate='0.1',snapshot_path=snapshot['path'],
        snapshot_sha256=snapshot['sha256'],signup_rows=[dict(item='1',sku='11',erp_code='sample',activity_price='30.00')])
    db=sqlite3.connect(':memory:',isolation_level=None);db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE attempts(id,bundle_id,campaign,phase,item,status,evidence)')
    db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',('a'*32+':1','bundle',campaign,'signup','1','failed',json.dumps(terminal)))
    offer=dict(offer_id='offer',start=start,end=end,rows=[dict(item='1',sku='11',deduct='5.98')],
               items=[dict(item='1',status='success')])
    a=SimpleNamespace(db=db,blocked=lambda *x:{},get_bundle=lambda _:body,discount_offers=lambda:[offer])
    r=dict(schema='campaign_segment_repair_v1',campaign=campaign,shop_name='shop',start=start,end=end,
           offer_id='offer',readback=read,failures=[dict(report=report,claim_id='a'*32)])
    return a,r,save,body,offer


def test_bridge_uses_exact_window_without_rewriting_old_report(tmp_path):
    a,r,save,body,offer=fixture(tmp_path)
    original=Path(r['failures'][0]['report']['path']).read_bytes()
    result=derive(a,r)
    assert result['rows']==[dict(item='1',sku='11',offer_id='offer',old_deduct='5.98',new_deduct='5.99')]
    assert result['errors'][0]['feasible_final_price']=='21.01'
    assert Path(r['failures'][0]['report']['path']).read_bytes()==original


@pytest.mark.parametrize('bad',['window','shop','offer','unknown','success','snapshot','report','missing','duplicate','rate','price','floor','custom'])
def test_bridge_rejects_changed_or_unprotected_inputs(tmp_path,bad):
    a,r,save,body,offer=fixture(tmp_path)
    if bad=='window':r['start']='2026-09-28 00:00:00'
    if bad=='shop':r['shop_name']='other'
    if bad=='offer':r['offer_id']='other'
    if bad in ('unknown','success'):a.blocked=lambda *x:{'1':bad}
    if bad=='snapshot':Path(body['snapshot_path']).write_text('{}')
    if bad=='report':Path(r['failures'][0]['report']['path']).write_text('{}')
    if bad=='missing':r['failures']=[]
    if bad=='duplicate':r['failures']*=2
    if bad=='rate':body['official_rate']='0.12'
    if bad=='price':body['signup_rows'][0]['activity_price']='29.99'
    if bad in ('floor','custom'):
        doc=json.loads(Path(body['snapshot_path']).read_text())
        if bad=='floor':doc['all_erp_rows'][0]['medium_target']='24.00'
        else:doc['all_erp_rows'][0]['custom']=True
        ref=save('snapshot.json',doc);body['snapshot_sha256']=ref['sha256']
    with pytest.raises((ValueError,KeyError)):derive(a,r)


def test_claim_is_idempotent_and_dispatched_is_never_released(tmp_path):
    a,r,save,body,offer=fixture(tmp_path);ref=save('request.json',r)
    first=prepare(a,ref['path'],tmp_path/'out',claim=True)
    second=prepare(a,ref['path'],tmp_path/'out2',claim=True)
    assert first['claim_id']==second['claim_id']
    a.db.execute("UPDATE continuous_discount_repairs SET state='dispatched_unknown'")
    with pytest.raises(ValueError,match='no_replay'):prepare(a,ref['path'],tmp_path/'out3',claim=True)
    assert a.db.execute('SELECT COUNT(*) FROM continuous_discount_repairs').fetchone()[0]==1


def test_other_pending_claim_blocks_same_scope(tmp_path):
    from campaign_continuous_repairs import table
    a,r,save,body,offer=fixture(tmp_path);ref=save('request.json',r);table(a)
    previous=dict(start=r['start'],end=r['end'],rows=[dict(item='1',sku='11')])
    a.db.execute('INSERT INTO continuous_discount_repairs VALUES(?,?,?,NULL,NULL)',
                 ('other',json.dumps(previous),'dispatched_unknown'))
    with pytest.raises(ValueError,match='existing_claim_protected'):prepare(a,ref['path'],tmp_path/'out',claim=True)
    assert a.db.execute('SELECT COUNT(*) FROM continuous_discount_repairs').fetchone()[0]==1
