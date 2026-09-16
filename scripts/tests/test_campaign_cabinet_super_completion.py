from copy import deepcopy
from decimal import Decimal
import pytest

import campaign_cabinet_super_completion as c


def snapshot():
    return {'all_erp_rows':[dict(code=r['code'],item=c.ITEM,alt=[s],custom=False,
        daily=r['daily'],medium_target=r['target'],big_target=r['floor']) for s,r in c.TARGETS.items()]}


def inventory():
    return {'rows':[dict(sku_id=s,inputs=[dict(type='text',value='' if s in c.TARGETS else '332.22')]) for s in c.SKUS]}


def observation():
    return dict(item=c.ITEM,window=dict(c.WINDOW,offer_id=c.OFFER),requested_scope_verified=True,
        values={s:'0.00' for s in c.PRESERVED},not_enrolled=list(c.TARGETS))


def test_unified_rounding():
    assert c.deductions(snapshot()) == {'6134178749284':'1735.70','6134178749285':'1848.97'}


@pytest.mark.parametrize('field',['item','marketing_id','offer_id','authorization','price_window','identity','targets'])
def test_scope_cannot_expand(field):
    value=c.payload();value[field]='changed'
    with pytest.raises(ValueError):c.validate(value)


@pytest.mark.parametrize('field,value',[('custom',True),('daily','6284'),('medium_target','3800'),('alt',[]),('item','1001358847694')])
def test_source_conflict_blocks(field,value):
    data=snapshot();data['all_erp_rows'][0][field]=value
    with pytest.raises(ValueError):c.deductions(data)


def test_missing_and_already_saved_editors():
    values=c.editor_prices(inventory())
    assert values[c.SKUS[0]] is None
    for row in inventory()['rows']:
        assert row['sku_id'] in c.SKUS
    raw=inventory()
    for row in raw['rows']:
        if row['sku_id'] in c.TARGETS:row['inputs'][0]['value']=c.TARGETS[row['sku_id']]['daily']
    assert c.editor_prices(raw)[c.SKUS[0]]=='6285.00'


@pytest.mark.parametrize('kind',['duplicate','missing','other_price','blank_preserved'])
def test_editor_conflict(kind):
    raw=inventory()
    if kind=='duplicate':raw['rows'].append(deepcopy(raw['rows'][0]))
    if kind=='missing':raw['rows'].pop()
    if kind=='other_price':raw['rows'][0]['inputs'][0]['value']='5000'
    if kind=='blank_preserved':raw['rows'][-1]['inputs'][0]['value']=''
    with pytest.raises((ValueError,ArithmeticError)):c.editor_prices(raw)


def test_only_missing_discounts_are_added():
    wanted=c.deductions(snapshot());obs=observation()
    assert c.missing_discounts(obs,wanted)==wanted
    sku=next(iter(wanted));obs['not_enrolled'].remove(sku);obs['values'][sku]=wanted[sku]
    assert c.missing_discounts(obs,wanted)=={s:v for s,v in wanted.items() if s!=sku}
    obs['values'].update(wanted);obs['not_enrolled']=[]
    assert c.missing_discounts(obs,wanted)=={}


@pytest.mark.parametrize('kind',['wrong_amount','wrong_window','incomplete','ambiguous'])
def test_discount_conflicts(kind):
    obs=observation();wanted=c.deductions(snapshot());sku=next(iter(wanted))
    if kind=='wrong_amount':obs['not_enrolled'].remove(sku);obs['values'][sku]='1736.20'
    if kind=='wrong_window':obs['window']['end']='2026-09-30 00:00:00'
    if kind=='incomplete':obs['values'].pop(c.PRESERVED[0])
    if kind=='ambiguous':obs['values'][sku]=wanted[sku]
    with pytest.raises(ValueError):c.missing_discounts(obs,wanted)


def test_preserved_prices_are_immutable():
    before=c.editor_prices(inventory());after=dict(before)
    after[c.SKUS[0]]='6285.00';c.verify_preserved(before,after,c.TARGETS)
    after[c.PRESERVED[0]]='1'
    with pytest.raises(ValueError):c.verify_preserved(before,after,c.TARGETS)


def test_official_export_is_required_and_exact():
    expected={s:c.TARGETS[s]['daily'] if s in c.TARGETS else '332.22' for s in c.SKUS}
    rows=[dict(item=c.ITEM,sku=s,state='活动中',marketing_id=c.MARKETING,activity_price=v) for s,v in expected.items()]
    assert c.final_acceptance(rows,expected)['registered_skus']==5
    rows[0]['activity_price']=''
    with pytest.raises((ValueError,ArithmeticError)):c.final_acceptance(rows,expected)


def test_cancelled_history_cannot_hide_live_row():
    expected={s:c.TARGETS[s]['daily'] if s in c.TARGETS else '332.22' for s in c.SKUS}
    rows=[dict(item=c.ITEM,sku=s,state='活动中',marketing_id=c.MARKETING,activity_price=v) for s,v in expected.items()]
    rows.append(dict(rows[0],state='撤销报名',marketing_id='old'))
    assert c.final_acceptance(rows,expected)['all_registered']
    rows.append(dict(rows[0]))
    with pytest.raises(ValueError):c.final_acceptance(rows,expected)
