"""Isolated API contract tests; never compute or send a production quote."""
from copy import deepcopy
from uuid import uuid4
from decimal import Decimal
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.customization import router
from app.database import get_db
from app.models.product import Product
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo


def confirmed(body):
    return dict(**body,confirmation=dict(request_id=str(uuid4()),price_confirmed=True,
        material_confirmed=True,identity_confirmed=True,inputs=deepcopy(body)))


@pytest.fixture
def client(db_session,monkeypatch):
    db=db_session
    db.add(Product(code='P',name='樱桃木床',category='卧室-床',main_material='樱桃木'))
    db.add(PricingSku(product_code='P',sku_code='P0116',sku='樱桃木-1.5米',daily_price=Decimal('3000'),big_promo=Decimal('2510'),mid_promo=Decimal('2600'),is_custom_placeholder=False))
    db.add(PricingSkuPromo(sku_code='P0116',big_buyer_price=Decimal('2734.69'),mid_buyer_price=Decimal('2800')))
    db.commit()
    db.connection()  # Keep this in-memory SQLite connection across TestClient's worker thread.
    from app.api import customization
    monkeypatch.setattr(customization,'_log_quote',lambda *a,**k:None)
    app=FastAPI();app.include_router(router)
    app.dependency_overrides[get_db]=lambda:db
    return TestClient(app)


def inputs():
    return dict(base_product_code='P',base_sku_code='P0116',target_material='樱桃木',price_tier='big_buyer',target_length_m=1.5)


@pytest.mark.parametrize('endpoint',['quote-light','quote-both'])
@pytest.mark.parametrize('case',['missing','false','string_bool','stale_sku','stale_material','stale_tier','stale_size','missing_tier','missing_material','missing_sku','unknown_tier','empty_material'])
def test_unconfirmed_or_changed_request_never_reaches_engine(client,monkeypatch,endpoint,case):
    from app.services import custom_quote_v2_service as engine
    monkeypatch.setattr(engine,'quote_light',lambda *a,**k:pytest.fail('must not calculate'))
    monkeypatch.setattr(engine,'quote_both',lambda *a,**k:pytest.fail('must not calculate'))
    body=inputs();request=confirmed(body)
    if case=='missing':request.pop('confirmation')
    elif case=='false':request['confirmation']['material_confirmed']=False
    elif case=='string_bool':request['confirmation']['price_confirmed']='true'
    elif case.startswith('stale_'):
        key={'sku':'base_sku_code','material':'target_material','tier':'price_tier','size':'target_length_m'}[case[6:]]
        request[key]='changed'
    else:
        if case.startswith('missing_'):body.pop({'tier':'price_tier','material':'target_material','sku':'base_sku_code'}[case[8:]])
        if case=='unknown_tier':body['price_tier']='other'
        if case=='empty_material':body['target_material']=''
        request=confirmed(body)
    assert client.post('/api/customization/v2/'+endpoint,json=request).status_code==422


@pytest.mark.parametrize('tier',['big','big_buyer','mid','mid_buyer','daily'])
@pytest.mark.parametrize('endpoint',['quote-light','quote-both'])
def test_explicit_exact_values_forwarded_without_conversion(client,monkeypatch,tier,endpoint):
    from app.services import custom_quote_v2_service as engine
    calls=[]
    def compute(*a,**kw):calls.append(kw);return dict(final_price=None, spec={},custom=None)
    monkeypatch.setattr(engine,endpoint.replace('-','_'),compute)
    body=inputs();body['price_tier']=tier
    assert client.post('/api/customization/v2/'+endpoint,json=confirmed(body)).status_code==200
    assert len(calls)==1 and calls[0]['price_tier']==tier and calls[0]['base_sku_code']=='P0116' and calls[0]['target_material']=='樱桃木'


@pytest.mark.parametrize('change',[{'base_sku_code':'other'},{'base_product_code':'other'},{'price_tier':'list'},{'target_material':'岩板'}])
def test_no_fallback_to_other_sku_price_or_surface_material(client,monkeypatch,change):
    from app.services import custom_quote_v2_service as engine
    monkeypatch.setattr(engine,'quote_both',lambda *a,**k:pytest.fail('must not calculate'))
    assert client.post('/api/customization/v2/quote-both',json=confirmed(dict(inputs(),**change))).status_code==422


@pytest.mark.parametrize('path,body',[
    ('/v2/quote-heavy',dict(product_type='柜',length_m=1.5,boards=[])),
    ('/v2/quote-from-template',dict(category='柜',length_cm=150)),
    ('/board-quote',dict(product_type='柜',length_m=1.5,boards=[])),
])
def test_all_board_calculation_routes_require_both_confirmations(client,path,body):
    assert client.post('/api/customization'+path,json=body).status_code==422
    assert client.post('/api/customization'+path,json=confirmed(body)).status_code==422


def test_legacy_image_cannot_compute_without_confirmation(client,monkeypatch):
    from app.services import customization_ai_service
    monkeypatch.setattr(customization_ai_service,'ai_quote',lambda *a,**k:pytest.fail('old bypass'))
    assert client.post('/api/customization/ai-quote',files={'image':('test.png',b'fake','image/png')}).status_code==409
