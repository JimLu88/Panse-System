from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo
import json
import pytest
from sqlalchemy import select, func
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo
from app.models.pricing_version import PricingSkuVersion
from app.services import pricing_scoped_sync as sync
from app.services.pricing_calc_service import recompute, recompute_promo
from app.services.pricing_version_service import record_dated_change, prune, values_at


def setup(db):
    day = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    sku = PricingSku(product_code='PPS_TEST', sku_code='PPS_TEST01', sku='test',
                     wood_cost=D('700'), packaging_cost=D('50'), external_parts_cost=D('0'),
                     logistics_cost=D('44'), install_cost=D('0'), list_price=D('4000'),
                     daily_price=D('3000'), small_promo=D('2000'), mid_promo=D('1900'), big_promo=D('1800'))
    other = PricingSku(product_code='OTHER', sku_code='OTHER01', sku='untouched', big_promo=D('999'))
    db.add_all([sku, other, PricingSkuCosts(sku_code=sku.sku_code), PricingSkuPromo(sku_code=sku.sku_code)])
    db.commit()
    row = {'sku_code':sku.sku_code, 'product_code':sku.product_code, 'sku':sku.sku,
           'components':dict(wood_cost='700', packaging_cost='50', external_parts_cost='0', logistics_cost='44', install_cost='0'),
           'bases':dict(base_list='.4',base_small='.79',base_mid='.8',base_big='.82'),
           'parts':dict.fromkeys(sync.PARTS, '0'),
           'source_prices':dict(list_price='2040',daily_price='1530',small_promo='1040',mid_promo='1020',big_promo='1000')}
    return {'schema':'pricing-scoped-sync-v1','request_id':'test','source_sha256':'a'*64,
            'authorization_ref':'unit-test-only','margin_policy':'source_cost_plus_keep_erp_mid_floor',
            'effective_from':day.isoformat(),'authorized_sku_codes':[sku.sku_code],'rows':[row]}


def reviewed(db,m):
    plan=sync.preview(db,m); db.rollback()
    return plan,sync.digest(plan)


def test_atomic_apply_and_idempotency(db_session):
    db=db_session;m=setup(db);plan,h=reviewed(db,m)
    assert plan['rows'][0]['erp_mid']=='1030'
    assert plan['rows'][0]['source_mid']=='1020'
    sync.apply(db,m,h);db.commit()
    sku=db.execute(select(PricingSku).where(PricingSku.sku_code=='PPS_TEST01')).scalar_one()
    assert sku.big_promo==1000 and sku.base_big==D('.82')
    assert db.execute(select(PricingSku).where(PricingSku.sku_code=='OTHER01')).scalar_one().big_promo==999
    old=values_at(db,sku_code=sku.sku_code,product_code=sku.product_code,
                  on_date=datetime.now(ZoneInfo('Asia/Shanghai')).date()-timedelta(days=1))
    assert old.big_promo==1800
    db.rollback()
    result=sync.apply(db,m,h);db.commit()
    assert result['already_applied'] is True
    assert db.scalar(select(func.count()).select_from(PricingSkuVersion))==1


def test_cas_drift_blocks_whole_apply(db_session):
    db=db_session;m=setup(db);_,h=reviewed(db,m)
    sku=db.execute(select(PricingSku).where(PricingSku.sku_code=='PPS_TEST01')).scalar_one()
    sku.wood_cost=D('701');db.commit()
    with pytest.raises(ValueError,match='changed'):sync.apply(db,m,h)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(PricingSkuVersion))==0


def test_all_history_retained_beyond_thirty(db_session):
    db=db_session;m=setup(db)
    sku=db.execute(select(PricingSku).where(PricingSku.sku_code=='PPS_TEST01')).scalar_one()
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    for i in range(35):record_dated_change(db,sku,today-timedelta(days=36-i),preserve_all=True)
    db.commit();_,h=reviewed(db,m);sync.apply(db,m,h);db.commit()
    assert prune(db,sku.sku_code)==0
    assert db.scalar(select(func.count()).select_from(PricingSkuVersion))==36


@pytest.mark.parametrize('bad',[None,'#VALUE!','NaN','Infinity','-1','1.001'])
def test_invalid_components_rejected(db_session,bad):
    m=setup(db_session);m['rows'][0]['components']['wood_cost']=bad
    with pytest.raises(ValueError):sync.preview(db_session,m)


def test_duplicate_scope_and_unauthorized_field(db_session):
    m=setup(db_session);m['rows'].append(deepcopy(m['rows'][0]))
    with pytest.raises(ValueError,match='scope'):sync.preview(db_session,m)
    m['rows'].pop();m['rows'][0]['components']['daily_price']='1'
    with pytest.raises(ValueError,match='complete'):sync.preview(db_session,m)


def test_parts_sum_and_source_price_guards(db_session):
    m=setup(db_session);m['rows'][0]['parts']['glass']='1'
    with pytest.raises(ValueError,match='sum'):sync.preview(db_session,m)
    m['rows'][0]['parts']['glass']='0';m['rows'][0]['source_prices']['big_promo']='999'
    with pytest.raises(ValueError,match='formula'):sync.preview(db_session,m)


def test_same_day_history_blocks(db_session):
    db=db_session;m=setup(db)
    sku=db.execute(select(PricingSku).where(PricingSku.sku_code=='PPS_TEST01')).scalar_one()
    record_dated_change(db,sku,datetime.now(ZoneInfo('Asia/Shanghai')).date());db.commit()
    with pytest.raises(ValueError,match='same_day'):sync.preview(db,m)


def test_restored_cost_linkage_keeps_global_formula(db_session):
    db=db_session;m=setup(db);_,h=reviewed(db,m);sync.apply(db,m,h);db.commit()
    sku=db.execute(select(PricingSku).where(PricingSku.sku_code=='PPS_TEST01')).scalar_one()
    sku.wood_cost+=D('100');recompute(sku)
    assert sku.big_promo==1120 and sku.daily_price==1725


def test_rollback_on_history_failure(db_session, monkeypatch):
    db=db_session;m=setup(db);_,h=reviewed(db,m)
    def fail(*a, **k):raise RuntimeError('injected failure')
    monkeypatch.setattr(sync,'record_dated_change',fail)
    with pytest.raises(RuntimeError):sync.apply(db,m,h)
    db.rollback()
    assert db.execute(select(PricingSku).where(PricingSku.sku_code=='PPS_TEST01')).scalar_one().big_promo==1800
    assert db.scalar(select(func.count()).select_from(PricingSkuVersion))==0


def test_request_binding(db_session):
    db=db_session;m=setup(db);_,h=reviewed(db,m);sync.apply(db,m,h);db.commit()
    m['source_sha256']='b'*64
    with pytest.raises(ValueError,match='already_bound'):sync.apply(db,m,h)
    db.rollback()


def test_promo_parameter_drift(db_session):
    from app.models.settings import SystemSetting
    db=db_session;m=setup(db);_,h=reviewed(db,m)
    db.add(SystemSetting(key='promo_big_vip_commission',value_plain='0.02',is_secret=False));db.commit()
    with pytest.raises(ValueError,match='changed'):sync.apply(db,m,h)
    db.rollback()


def test_buyer_target_is_not_silently_equal_to_source_k(db_session):
    from app.models.settings import SystemSetting
    db=db_session;m=setup(db)
    db.add(SystemSetting(key='promo_big_vip_commission',value_plain='0.02',is_secret=False));db.commit()
    plan,_=reviewed(db,m)
    assert plan['rows'][0]['after']['promo']['big_buyer_price']=='1020.41'
    assert plan['rows'][0]['event_target_not_erp_buyer']=='1000'
