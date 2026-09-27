import sys
from pathlib import Path
from copy import deepcopy
from decimal import Decimal
import json
import pytest
from sqlalchemy import select

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import campaign_custom_correspondence_remote as m
from campaign_custom_correspondence_policy import APPROVED, ROWS, KEY, PRODUCT, ITEM, approved_rows
from campaign_price_snapshot import SnapshotRows, build_snapshot
import campaign_sku_fact_store as facts
from campaign_generate_current_files import build_rows
from app.models.product import Product
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.settings import SystemSetting


def setup(db):
    db.add(Product(code=PRODUCT,name='柜',taobao_id=ITEM))
    for n,row in enumerate(ROWS):
        db.add(PricingSku(product_code=PRODUCT,sku_code=row['code'],sku=['其他尺寸定制','黑胡桃木定制','白色岩板定制'][n],daily_price=Decimal('2000'),is_custom_placeholder=True))
        db.add(PricingSkuPromo(sku_code=row['code'],taobao_item_id='717388593550',taobao_sku_id=str(100+n),alt_taobao_sku_ids=[str(200+n)]))
    db.commit()


def test_preview_apply_readback_idempotent_only_setting(db_session):
    db=db_session;setup(db)
    before=m.inspect(db)
    assert not before['already_applied']
    result=m.apply(db,before['precondition_sha256'])
    assert result['status']=='applied'
    assert result['after']['protected_sha256']==before['protected_sha256']
    assert json.loads(db.execute(select(SystemSetting.value_plain).where(SystemSetting.key==KEY)).scalar_one())==APPROVED
    assert m.apply(db,before['precondition_sha256'])['status']=='already_applied_no_write'
    assert len(db.execute(select(SystemSetting)).scalars().all())==1


@pytest.mark.parametrize('case',['hash','price','primary','alt','product','custom','missing','foreign','conflicting_record','secret'])
def test_guarded_apply_rejects_drift(db_session,case):
    db=db_session;setup(db);before=m.inspect(db)
    sku=db.execute(select(PricingSku)).scalars().first()
    promo=db.execute(select(PricingSkuPromo)).scalars().first()
    if case=='price':sku.daily_price=1999
    if case=='primary':promo.taobao_sku_id='999'
    if case=='alt':promo.alt_taobao_sku_ids=['999']
    if case=='product':db.execute(select(Product)).scalar_one().taobao_id='other'
    if case=='custom':sku.is_custom_placeholder=False
    if case=='missing':db.delete(sku)
    if case=='foreign':db.add(PricingSkuPromo(sku_code='FOREIGN',taobao_item_id=ITEM,taobao_sku_id=ROWS[0]['sku']))
    if case=='conflicting_record':db.add(SystemSetting(key=KEY,value_plain='{}',is_secret=False))
    if case=='secret':db.add(SystemSetting(key=KEY,value_plain=json.dumps(APPROVED),is_secret=True))
    db.commit()
    with pytest.raises(ValueError):m.apply(db,'bad' if case=='hash' else before['precondition_sha256'])
    db.rollback()
    if case not in ('conflicting_record','secret'):assert not db.execute(select(SystemSetting)).first()


def snapshot(monkeypatch, approved=True):
    raw=[dict(item=t['item'],sku=t['sku'],merchant_code=t['raw_code'],attributes=t['attributes'],sheet='发布模板',row=n+39,issues=[]) for n,t in enumerate(ROWS)]
    monkeypatch.setattr(facts,'read_version',lambda *a,**k: ({},deepcopy(raw)))
    erp=SnapshotRows([dict(product_code=PRODUCT,code=t['code'],sku_name=name,custom=True,daily='2000',item='717388593550',product_item_id=ITEM,product_alt_item_ids=[],sku=str(100+n),alt=[str(200+n)],listing_status='在售') for n,(t,name) in enumerate(zip(ROWS,['其他尺寸定制','黑胡桃木定制','白色岩板定制']))])
    if approved:erp.registered_custom_correspondence=deepcopy(APPROVED)
    s=build_snapshot(erp);s['sku_fact_source']=dict(sha256='test',root='test')
    return s,raw


def test_three_pairs_resolve_and_build_without_touching_raw_prices_or_bases(monkeypatch):
    s,_=snapshot(monkeypatch);before=deepcopy(s)
    baseline,_=snapshot(monkeypatch,False)
    assert s['all_erp_rows']==baseline['all_erp_rows']
    assert s['resolved_price_version_sha256']==baseline['resolved_price_version_sha256']
    ids=[dict(item=t['item'],sku=t['sku'],state='') for t in ROWS]
    bases={(t['item'],t['sku']):dict(original='2000',floor='400',erp_code=t['raw_code'],source_sha256='original-fixed-proof') for t in ROWS}
    preserved=deepcopy(bases)
    rows,discounts,issues=build_rows(s,ids,Decimal('.15'),'big',bases)
    assert not issues and not discounts and len(rows)==3
    assert [r['erp_code'] for r in rows]==[t['code'] for t in ROWS]
    assert all(r['sku_fact_evidence']['resolution']=='erp_exact_user_custom_correspondence' for r in rows)
    assert all(Decimal(r['activity_price'])==Decimal('2000') for r in rows)
    assert bases==preserved and s==before


@pytest.mark.parametrize('case',['raw_code','attributes','duplicate_fact','duplicate_target','ordinary','other_product','binding_conflict'])
def test_consumer_rejects_drift_not_names(monkeypatch,case):
    s,raw=snapshot(monkeypatch)
    if case=='raw_code':raw[0]['merchant_code']='other'
    if case=='attributes':raw[0]['attributes']='颜色分类:黑胡桃木;'
    if case=='duplicate_fact':raw.append(deepcopy(raw[0]))
    if case=='duplicate_target':s['all_erp_rows'].append(deepcopy(s['all_erp_rows'][0]))
    if case=='ordinary':s['all_erp_rows'][0]['custom']=False
    if case=='other_product':s['all_erp_rows'][0]['product_code']='other'
    monkeypatch.setattr(facts,'read_version',lambda *a,**k: ({},deepcopy(raw)))
    resolver=facts.FactResolver(s)
    if case in ('ordinary','other_product'):
        with pytest.raises(ValueError):resolver.resolve(ITEM,ROWS[0]['sku'],[])
    else:
        existing=[s['all_erp_rows'][1]] if case=='binding_conflict' else []
        match,issue,_=resolver.resolve(ITEM,ROWS[0]['sku'],existing)
        assert issue and not match


def test_scope_only_exact_pair_missing_source_not_guessed(monkeypatch):
    s,_=snapshot(monkeypatch);r=facts.FactResolver(s)
    assert r.resolve('other',ROWS[0]['sku'],[])==([],None,None)
    assert r.resolve(ITEM,'unknown',[])==([],None,None)
    s.pop('registered_custom_correspondence');r=facts.FactResolver(s)
    assert r.resolve(ITEM,ROWS[1]['sku'],[])[1]


def test_missing_current_fact_blocks_even_existing_mapping(monkeypatch):
    s,_=snapshot(monkeypatch)
    monkeypatch.setattr(facts,'read_version',lambda *a,**k: ({},[]))
    assert facts.FactResolver(s).resolve(ITEM,ROWS[0]['sku'],[s['all_erp_rows'][0]])[1]=='authorized_correspondence_current_fact_missing'


@pytest.mark.parametrize('case',['code','item','extra','source'])
def test_registry_not_expandable(case):
    value=deepcopy(APPROVED)
    if case in ('code','item'):value['rows'][0][case]='other'
    if case=='extra':value['rows'].append(deepcopy(ROWS[0]))
    if case=='source':value['source_export_sha256']='unknown'
    with pytest.raises(ValueError):approved_rows(value)
