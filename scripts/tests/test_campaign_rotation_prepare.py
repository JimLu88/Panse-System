from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
from zipfile import ZipFile
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_rotation_prepare as c
from campaign_official_template import template_rows,read_rows


def fixture():
    facts=[];current=[];erp=[];mapping=[];custom=[]
    for item,(marketing,count) in c.ITEMS.items():
        rotated=8 if count==14 else 1
        for n in range(count):
            sku=str((6000000000000 if count==14 else 6100000000000)+n)
            if count==7 and n in (5,6):sku=('5602711422165','6056644376634')[n-5]
            code='PPS'+sku
            current.append(dict(item=item,sku=sku,marketing_id=marketing,state='异常',activity_price='1000.00'))
            facts.append(dict(facts=dict(item=item,sku=sku,attributes='规格'+sku,sku_code=code,price='2000.00'),sources=[{'sha256':'a'*64}]))
            if n<rotated:
                new=str(int(sku)+100)
                mapping.append(dict(item=item,old_sku=sku,new_sku=new,erp_code=code,
                    old_attributes='规格'+sku,new_attributes='规格'+new,list_price='2000.00',
                    activity_price='1000.00',target='700.00',deduct='200.00',old_offer='147717819883'))
                facts.append(dict(facts=dict(item=item,sku=new,attributes='规格'+new,sku_code=code,price='2000.00'),sources=[{'sha256':'a'*64}]))
                erp.append(dict(item=item,sku=sku,code=code,daily='1000.00',medium_target='700.00',big_target='650.00',custom=False))
            if count==7 and n in (5,6):
                b=dict(original='1000.00',floor='200.00',uncertain=False,source='test-basis',source_sha256='a'*64)
                custom.append(dict(item=item,sku=sku,marketing_id=marketing,old_price='1000.00',new_price='733.33',custom=True,details={'basis':b}))
    rounds=[]
    for n in range(4):
        item='918692510350';sku=str(6200000000000+n);code='PPS'+sku
        facts.append(dict(facts=dict(item=item,sku=sku,attributes='规格',sku_code=code,price='2000.00'),sources=[{'sha256':'a'*64}]))
        erp.append(dict(code=code,medium_target='700.00',custom=False))
        rounds.append(dict(item=item,sku=sku,erp_code=code,base='2000.00',target='700.00',deduct='1300.00'))
    return dict(scope=dict(complete=True,page_evidence={'sha256':'b'*64},sku_facts=facts),
        mapping=dict(schema='rotation_mapping_evidence_v1',platform_write=False,erp_write=False,rows=mapping),
        snapshot=dict(resolved_price_version_sha256=c.VERSION,all_erp_rows=erp),
        current=dict(current_rows=current,missing_price_rows=[dict(item='793202812082',sku=str(6400000000000+n),activity_price=None) for n in range(4)]),
        custom={'fixed_basis':custom},round=dict(scope_skus=rounds,release_conditions=['未撤旧优惠','模式未知']))


def protection():return dict(signup_items={},signup_pairs=set(),discount_pairs=set(),no_sales_items=set())


def test_full_21_new_nine_custom_two_and_unknown_preserved(monkeypatch):
    monkeypatch.setattr(c,'pinned',lambda *args:b'test')
    d=fixture();r=c.calculate(d,protection())
    assert len(r['activity_rows'])==21 and len(r['discount_rows'])==9
    assert len(r['conditional_round_rows'])==4 and len(r['missing_price_rows'])==4
    pairs={(x['item'],x['sku']) for x in r['activity_rows']}
    assert all((x['item'],x['new_sku']) in pairs and (x['item'],x['old_sku']) not in pairs for x in d['mapping']['rows'])
    assert all(x['new_cap'] is None and x['old_enabled']=='unknown' for x in r['rotation_lineage'])
    assert not r['upload_ready'] and not r['whole_event_complete']


@pytest.mark.parametrize('bad',['version','mapping_count','mapping_duplicate','erp_code','attributes','price','target','deduct','source_missing','state','marketing','scope_count','custom_floor','custom_raise','round_g','round_target','missing_p'])
def test_changed_input_refused_not_guessed(monkeypatch,bad):
    monkeypatch.setattr(c,'pinned',lambda *args:b'test');d=fixture()
    m=d['mapping']['rows'][0]
    if bad=='version':d['snapshot']['resolved_price_version_sha256']='x'
    if bad=='mapping_count':d['mapping']['rows'].pop()
    if bad=='mapping_duplicate':d['mapping']['rows'].append(deepcopy(m))
    if bad=='erp_code':m['erp_code']='wrong'
    if bad=='attributes':m['new_attributes']='wrong'
    if bad=='price':m['activity_price']='999.00'
    if bad=='target':m['target']='699.00'
    if bad=='deduct':m['deduct']='201.00'
    if bad=='source_missing':d['scope']['sku_facts'].pop(1)
    if bad=='state':d['current']['current_rows'][0]['state']='活动中'
    if bad=='marketing':d['current']['current_rows'][0]['marketing_id']='wrong'
    if bad=='scope_count':d['current']['current_rows'].pop()
    if bad=='custom_floor':d['custom']['fixed_basis'][0]['details']['basis']['floor']='1.00'
    if bad=='custom_raise':d['custom']['fixed_basis'][0]['new_price']='1100.00'
    if bad=='round_g':d['round']['scope_skus'][0]['base']='3000.00'
    if bad=='round_target':d['round']['scope_skus'][0]['target']='701.00'
    if bad=='missing_p':d['current']['current_rows'][0]['activity_price']=None
    with pytest.raises(ValueError):c.calculate(d,protection())


@pytest.mark.parametrize('kind',['item','pair','old_pair','no_sales'])
def test_success_unknown_held_whole_item_without_losing_plan(monkeypatch,kind):
    monkeypatch.setattr(c,'pinned',lambda *args:b'test');d=fixture();p=protection();m=d['mapping']['rows'][0]
    if kind=='item':p['signup_items'][m['item']]='success'
    if kind=='pair':p['signup_pairs'].add((m['item'],m['new_sku']))
    if kind=='old_pair':p['signup_pairs'].add((m['item'],m['old_sku']))
    if kind=='no_sales':p['no_sales_items'].add(m['item'])
    r=c.calculate(d,p)
    assert len(r['activity_rows'])==7 and len(r['held_activity_rows'])==14
    assert len(r['discount_rows'])==9 and len(r['held'])==1


def test_discount_protection_does_not_erase_activity_plan(monkeypatch):
    monkeypatch.setattr(c,'pinned',lambda *args:b'test');d=fixture();p=protection();m=d['mapping']['rows'][0]
    p['discount_pairs'].add((m['item'],m['new_sku']));r=c.calculate(d,p)
    assert len(r['discount_rows'])==8 and len(r['activity_rows'])==21


@pytest.mark.parametrize('case',['valid','no_original','active','other_marketing','price_changed','new_trial_hold'])
def test_historical_success_only_conditional_exact_newer_marketing(monkeypatch,case):
    monkeypatch.setattr(c,'pinned',lambda *args:b'test');d=fixture();p=protection()
    item='1036273574687';p['signup_items'][item]='success'
    p['historical_changed_scope_candidates']=[item]
    d['official_current']=deepcopy(d['current']['current_rows'])
    if case=='no_original':d['official_current']=[]
    if case=='active':d['official_current'][0]['state']='活动中'
    if case=='other_marketing':d['official_current'][0]['marketing_id']='another'
    if case=='price_changed':d['official_current'][0]['activity_price']='999.00'
    if case=='new_trial_hold':p['signup_pairs'].add((item,d['mapping']['rows'][0]['old_sku']))
    r=c.calculate(d,p)
    assert len(r['activity_rows'])==(21 if case=='valid' else 7)
    assert r['historical_success_preserved'] and not r['upload_ready']


def test_projector_full_scope_nondata_preserved(monkeypatch):
    from test_campaign_fixed_projection import master
    monkeypatch.setattr(c,'pinned',lambda *args:b'test')
    d=fixture();r=c.calculate(d,protection());wanted={(x['item'],x['sku']) for x in r['activity_rows']}
    subset=deepcopy(d['scope']);subset['sku_facts']=[x for x in subset['sku_facts'] if (x['facts']['item'],x['facts']['sku']) in wanted]
    raw=master();b=BytesIO()
    with ZipFile(BytesIO(raw)) as src,ZipFile(b,'w') as dest:
        for n in src.namelist():dest.writestr(n,src.read(n).replace(b'name="1"',b'name="property1"') if n=='docProps/custom.xml' else src.read(n))
    raw=b.getvalue();output=c.fill_selected_rows(c.project(raw,subset,list(c.ITEMS)),r['activity_rows'],official_rate='10%')
    assert len(template_rows(output))==21
    with ZipFile(BytesIO(raw)) as src,ZipFile(BytesIO(output)) as out:
        assert src.namelist()==out.namelist()
        assert all(src.read(n)==out.read(n) for n in src.namelist() if n!='xl/worksheets/sheet1.xml')
    values=read_rows(output,'商品SKU导入列表')
    assert all(values[n]['D']=='' and values[n]['C']=='' and values[n]['X']=='10' for n in range(4,25))
