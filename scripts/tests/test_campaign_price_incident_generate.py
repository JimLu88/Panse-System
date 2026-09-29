import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_price_incident_generate as g


def facts():
    items=[('1035582527998',12),('1037237545657',8),('1038071596128',8)]
    items += [(str(1000000000000+i),n) for i,n in enumerate([2,4,4,4,5,4,4,4])]
    doc={'rows':[]};rows=[];withdrawal={'rows':[]};lists={};seq=0
    for item,count in items:
        for mode in ('商品级','SKU级'):
            lists[item,mode]={'complete':True,'offers':[dict(offer_id='147685482568',
                start='2026-09-28 00:00:00',end=g.END,status='已暂停')]}
        for _ in range(count):
            seq+=1;sku=str(6000000000000+seq)
            doc['rows'].append(dict(item=item,sku=sku,frozen_target='50.00',original_offer_id='147717819883'))
            withdrawal['rows'].append(dict(item=item,sku=sku,withdrawal_member_absent_verified=True))
            r=dict(B=item,D=sku,E='100.00',F='100.00',G='100.00',source_row=seq+3)
            if item in g.BEDS:
                r.update(E='75.00',G='75.00',M='25.00',N=g.PROMO_WINDOW)
                r.update(zip('HIJKL',g.PROMO))
            rows.append(r)
    return doc,rows,withdrawal,lists


def test_all_59_keep_targets_and_existing_bed_promotion():
    inputs=facts();before=deepcopy(inputs);result=g.calculate(*inputs)
    assert len(result)==59 and inputs==before
    assert sum(r['replacement_deduction']=='25.00' for r in result)==28
    assert sum(r['replacement_deduction']=='50.00' for r in result)==31
    assert all(r['expected_final']=='50.00' for r in result)
    assert all(not r['current_transaction_price_proven'] for r in result)


@pytest.mark.parametrize('fault',[
    'missing','duplicate','foreign_item','withdrawal','list_missing','list_incomplete',
    'active_overlap','unknown_overlap','bed_rate','bed_amount','bed_time','bed_final',
    'bed_offer','plain_discount','plain_base','empty_price','zero_price','nan_price',
    'target_above_base','fractional_target','duplicate_frozen',
])
def test_incomplete_or_changed_evidence_never_generates_prices(fault):
    doc,rows,withdrawal,lists=facts();bed=rows[0];plain=rows[-1]
    if fault=='missing':rows.pop()
    if fault=='duplicate':rows.append(deepcopy(bed))
    if fault=='foreign_item':plain['B']='9999999999999'
    if fault=='withdrawal':withdrawal['rows'][0]['withdrawal_member_absent_verified']=False
    if fault=='list_missing':lists.pop(next(iter(lists)))
    if fault=='list_incomplete':lists[next(iter(lists))]['complete']=False
    if fault in ('active_overlap','unknown_overlap'):
        lists[next(iter(lists))]['offers'][0]['status']='进行中' if fault=='active_overlap' else None
    if fault=='bed_rate':bed['K']='7折'
    if fault=='bed_amount':bed['M']='24.99'
    if fault=='bed_time':bed['N']='2026-09-01 00:00:00-2026-09-30 23:59:59'
    if fault=='bed_final':bed['E']='67.50'
    if fault=='bed_offer':bed['L']='999999999'
    if fault=='plain_discount':plain['H']='超级立减'
    if fault=='plain_base':plain['G']='99.00'
    if fault=='empty_price':bed['G']=None
    if fault=='zero_price':plain.update(E='0',F='0',G='0')
    if fault=='nan_price':plain['E']='NaN'
    if fault=='target_above_base':doc['rows'][0]['frozen_target']='76.00'
    if fault=='fractional_target':doc['rows'][0]['frozen_target']='50.001'
    if fault=='duplicate_frozen':doc['rows'][-1]=deepcopy(doc['rows'][0])
    with pytest.raises(ValueError):g.calculate(doc,rows,withdrawal,lists)


def test_expired_nonoverlapping_offer_does_not_block():
    inputs=facts();offer=inputs[3][next(iter(inputs[3]))]['offers'][0]
    offer.update(start='2026-09-01 00:00:00',end='2026-09-27 23:59:59',status='进行中')
    assert len(g.calculate(*inputs))==59


def test_output_cannot_bypass_shared_reservation(tmp_path,monkeypatch):
    ledger=tmp_path/'ledger';ledger.mkdir()
    monkeypatch.setattr(g,'build',lambda:pytest.fail('must stop before build'))
    with pytest.raises(ValueError,match='already_reserved'):
        g.generate(tmp_path/'different-output',ledger=ledger)


def test_existing_output_not_overwritten(tmp_path,monkeypatch):
    out=tmp_path/'existing';out.mkdir();keep=out/'keep';keep.write_text('user')
    monkeypatch.setattr(g,'build',lambda:pytest.fail('must stop before build'))
    with pytest.raises(ValueError,match='directory_exists'):
        g.generate(out,ledger=tmp_path/'ledger')
    assert keep.read_text()=='user' and not (tmp_path/'ledger').exists()


def test_generation_does_not_offer_network_or_platform_operations():
    source=Path(g.__file__).read_text(encoding='utf-8')
    for banned in ('requests.','httpx.','EdgeClient','submit(','.commit(','.execute('):
        assert banned not in source


def test_cent_math_regression_original_list_price_must_not_be_base():
    inputs=facts();r=inputs[1][0]
    r.update(F='10580.00',G='7935.00',E='7935.00',M='2645.00')
    inputs[0]['rows'][0]['frozen_target']='5423.27'
    result=g.calculate(*inputs)
    actual=next(x for x in result if x['sku']==r['D'])
    assert actual['replacement_deduction']=='2511.73'
    assert Decimal(actual['effective_base'])-Decimal(actual['replacement_deduction'])==Decimal('5423.27')
