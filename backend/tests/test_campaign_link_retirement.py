"""Exact one-link exclusion; shared product, SKU, prices and aliases stay intact."""
import sys
from pathlib import Path
from decimal import Decimal
import pytest
from sqlalchemy import select

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import campaign_link_retirement_remote as m
from campaign_price_snapshot import SnapshotRows,build_snapshot,digest,exclusion_ids
from campaign_generate_current_files import build_rows
from app.models.product import Product
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.models.campaign import CampaignItemExclusion
from app.services.campaign_item_exclusion_service import explicit_items


def setup(db):
    db.add(Product(code=m.PRODUCT,name='曜黑',taobao_id=m.KEEP,alt_taobao_ids=[m.OLD],listing_status='在售'))
    for n,item in enumerate([m.OLD,m.KEEP]):
        code=m.PRODUCT+str(n)
        db.add(PricingSku(product_code=m.PRODUCT,sku_code=code,daily_price=Decimal('1000'),is_custom_placeholder=False))
        db.add(PricingSkuPromo(sku_code=code,taobao_item_id=item,taobao_sku_id=str(100+n),big_buyer_price=Decimal('700')))
    db.commit()


def test_preview_apply_readback_and_idempotency(db_session):
    db=db_session;setup(db)
    before=m.inspect(db)
    assert not explicit_items(db) and before['new_link_platform_status']=='unknown'
    result=m.apply(db,before['precondition_sha256'],m.PROOF)
    assert result['status']=='applied' and list(explicit_items(db))==[m.OLD]
    assert result['after']['protected_sha256']==before['protected_sha256']
    assert db.execute(select(Product.listing_status)).scalar_one()=='在售'
    assert len(db.execute(select(PricingSku)).scalars().all())==2
    assert m.apply(db,before['precondition_sha256'],m.PROOF)['status']=='already_applied_no_write'
    assert len(db.execute(select(CampaignItemExclusion)).scalars().all())==1


@pytest.mark.parametrize('case',['token','proof','price_drift','existing_marker','missing_keep','foreign_mapping','foreign_product'])
def test_apply_refuses_drift_or_broader_scope(db_session,case):
    db=db_session;setup(db);before=m.inspect(db)
    expected=before['precondition_sha256'];proof=m.PROOF
    if case=='token':expected='wrong'
    if case=='proof':proof='wrong'
    if case=='price_drift':db.execute(select(PricingSku)).scalars().first().daily_price=Decimal('950')
    if case=='existing_marker':db.add(CampaignItemExclusion(taobao_item_id=m.OLD,reason='other',source='other',active=False))
    if case=='missing_keep':
        prod=db.execute(select(Product)).scalar_one();prod.taobao_id=m.OLD
        for p in db.execute(select(PricingSkuPromo)).scalars():p.taobao_item_id=m.OLD
    if case=='foreign_mapping':db.add(PricingSkuPromo(sku_code='FOREIGN',taobao_item_id=m.OLD))
    if case=='foreign_product':db.add(Product(code='OTHER',name='Other',taobao_id=m.OLD))
    db.commit()
    with pytest.raises(ValueError):m.apply(db,expected,proof)
    assert not any(r.active for r in db.execute(select(CampaignItemExclusion)).scalars())


def test_keep_marker_unchanged(db_session):
    db=db_session;setup(db)
    db.add(CampaignItemExclusion(taobao_item_id=m.KEEP,reason='keep existing independent state',source='other',active=True));db.commit()
    before=m.inspect(db);after=m.apply(db,before['precondition_sha256'],m.PROOF)['after']
    assert after['protected_sha256']==before['protected_sha256']
    assert explicit_items(db)[m.KEEP]['reason']=='keep existing independent state'


def snapshot_rows():
    return SnapshotRows([dict(code='X',item=m.OLD,product_item_id=m.KEEP,product_alt_item_ids=[m.OLD],sku='100',
        alt=[],daily='1000',medium_target='800',big_target='700',listing_status='在售',custom=False)])


def test_exact_link_filter_never_deletes_price_rows_or_changes_fingerprint():
    rows=snapshot_rows();before=build_snapshot(rows)
    rows.registered_item_exclusions=[dict(item=m.OLD,reason='仓库中',source=m.SOURCE)]
    after=build_snapshot(rows)
    assert before['resolved_price_version_sha256']==after['resolved_price_version_sha256']==digest(rows)
    assert after['all_erp_rows']==before['all_erp_rows']
    assert after['current_sellable_item_ids']==[m.KEEP]
    ids=[dict(item=i,sku='100',state='') for i in (m.OLD,m.KEEP)]
    a,d,issues=build_rows(after,ids,Decimal('.15'),'big',{})
    assert not issues and [r['item'] for r in a]==[m.KEEP] and [r['item'] for r in d]==[m.KEEP]
    assert before['current_sellable_item_ids']==sorted([m.OLD,m.KEEP])


@pytest.mark.parametrize('bad',[None,{},[{}],[dict(item='bad',reason='r',source='s')],
    [dict(item='1',reason='r',source='s')]*2])
def test_exclusion_evidence_malformed_not_silently_ignored(bad):
    with pytest.raises(ValueError):exclusion_ids(dict(registered_item_exclusions=bad))
