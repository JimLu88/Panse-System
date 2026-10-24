from datetime import datetime,timezone,timedelta
import io,json
from concurrent.futures import ThreadPoolExecutor
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app.isolated_store import initialize,Store,create_app,safe_child
from app.isolated_store_pricing import calculate,VERSION

@pytest.fixture
def stores(tmp_path):
 a=initialize(tmp_path/'a','fuge','tester','long-local-test-password')
 b=initialize(tmp_path/'b','panse','tester','different-test-password')
 ca=TestClient(create_app(a));cb=TestClient(create_app(b))
 for c,owner,password in [(ca,'fuge','long-local-test-password'),(cb,'panse','different-test-password')]:
  c.headers['X-Store-ID']=owner
  r=c.post('/api/stores/'+owner+'/login',json={'username':'tester','password':password});assert r.status_code==200
  c.headers['Authorization']='Bearer '+r.json()['token']
 return a,b,ca,cb

def product(c,owner,code='SKU1'):
 r=c.post('/api/stores/'+owner+'/products',json={'code':code,'name':'Test product','specification':'wood, 120cm'});assert r.status_code==200;return r.json()['id']

def photo():
 b=io.BytesIO();Image.new('RGB',(40,40),'red').save(b,format='PNG');return b.getvalue()

def evidence(**overrides):
 p=dict(confirmed=True,source_item_id='item123',source_sku_id='sku456',specification='120cm beech',source_url='https://example.com/item123',price_basis='platform_pre_coupon',shop_and_personal_coupons_excluded=True,conditions='no membership',discount_components=['store and personal coupons excluded'],observed_at=datetime.now(timezone.utc).isoformat(),rule_version=VERSION,platform_pre_coupon_price='1000',list_price='1400',single_item_rate='0.75',official_rate='0.15')
 p.update(overrides);return p

def test_missing_unknown_identity_and_no_fallback(stores):
 a,b,ca,cb=stores
 for headers,path in [({'X-Store-ID':''},'/api/stores/fuge/products'),({'X-Store-ID':'panse'},'/api/stores/fuge/products'),({},'/api/products'),({},'/api/stores/unknown/products')]:
  assert ca.get(path,headers=headers).status_code==403
 assert ca.get('/api/stores/fuge/products').json()['items']==[]

def test_cross_token_and_origin(stores):
 a,b,ca,cb=stores
 assert cb.get('/api/stores/panse/products',headers={'Authorization':ca.headers['Authorization']}).status_code==401
 assert ca.get('/api/stores/fuge/products',headers={'Origin':'http://foreign.example'}).status_code==403

def test_cross_product_image_and_same_hash(stores):
 a,b,ca,cb=stores;pa=product(ca,'fuge');pb=product(cb,'panse')
 assert ca.get('/api/stores/fuge/products/'+pb).status_code==404
 assert ca.post('/api/stores/fuge/products/'+pb+'/media',files={'file':('a.png',photo(),'image/png')}).status_code==404
 ma=ca.post('/api/stores/fuge/products/'+pa+'/media',files={'file':('a.png',photo(),'image/png')}).json()
 mb=cb.post('/api/stores/panse/products/'+pb+'/media',files={'file':('a.png',photo(),'image/png')}).json()
 assert ma['id']!=mb['id'] and ma['sha256']==mb['sha256']
 assert (a.root/'media'/ma['sha256']).is_file() and (b.root/'media'/ma['sha256']).is_file()
 assert ca.get('/api/stores/fuge/media/'+mb['id']).status_code==404
 assert ca.get('/api/stores/fuge/media/'+ma['id']).content==photo()
 assert ca.get('/api/stores/fuge/media/'+ma['id']+'?thumbnail=true').headers['Cache-Control']=='no-store'
 assert len(ca.get('/api/stores/fuge/products/'+pa+'/media').json()['items'])==1

def test_dedupe_local_and_injection(stores):
 a,b,ca,cb=stores;pa=product(ca,'fuge');ids=[]
 for i in range(2):ids.append(ca.post('/api/stores/fuge/products/'+pa+'/media',files={'file':('../../panse.png',photo(),'image/png')}).json()['id'])
 assert ids[0]==ids[1]
 assert ca.post('/api/stores/fuge/products',json={'code':'SKU2','name':'x','specification':'y','store_id':'panse'}).status_code==422
 assert cb.get('/api/stores/panse/products').json()['items']==[]

@pytest.mark.parametrize('path',['../panse/db','/etc/passwd','C:/panse/db','media/../../db','media\\..\\db',''])
def test_paths(stores,path):
 with pytest.raises(ValueError):safe_child(stores[0].root,path)

def test_swapped_database_and_root(stores):
 a,b,ca,cb=stores
 with pytest.raises(ValueError):Store(a.root,'panse')
 with a.db() as db:db.execute("UPDATE identity SET value='panse' WHERE key='store_id'")
 with pytest.raises(ValueError):Store(a.root,'fuge')

def test_export_restore_scope(stores):
 a,b,ca,cb=stores;product(ca,'fuge');product(cb,'panse')
 ea=ca.get('/api/stores/fuge/export').json();eb=cb.get('/api/stores/panse/export').json()
 assert len(ea['body']['data']['products'])==1
 assert ca.post('/api/stores/fuge/restore/validate',json=eb).status_code==422
 eb['body']['store_id']='fuge';eb['body']['instance_id']=a.instance
 assert ca.post('/api/stores/fuge/restore/validate',json=eb).status_code==422
 assert ca.post('/api/stores/fuge/restore/validate',json=ea).json()['restored'] is False
 assert 'password' not in json.dumps(ea)

def test_parallel_windows_no_shared_current_store(stores):
 a,b,ca,cb=stores
 def run(args):
  c,owner=args
  for i in range(5):product(c,owner,str(i))
 with ThreadPoolExecutor(2) as pool:list(pool.map(run,[(ca,'fuge'),(cb,'panse')]))
 for c,owner in [(ca,'fuge'),(cb,'panse')]:
  rows=c.get('/api/stores/'+owner+'/products').json()['items'];assert len(rows)==5;assert all(r['id'].startswith(owner+'_') for r in rows)

def test_disabled_jobs_and_other_domains(stores):
 a,b,ca,cb=stores
 for module in ['orders','customers','finance','notifications','platform_accounts','jobs','restore','import']:
  assert ca.post('/api/stores/fuge/'+module,json={'store_id':'panse'}).status_code==404
 assert ca.get('/health').json()['disabled']

def test_price_audit_and_no_cross_write(stores):
 a,b,ca,cb=stores;pa=product(ca,'fuge');pb=product(cb,'panse')
 r=ca.post('/api/stores/fuge/products/'+pa+'/calculate',json=evidence());assert r.status_code==200
 out=r.json();assert out['target_final']=='700.00' and out['signup_price']=='1050.00' and out['official_deduction']=='158' and out['single_deduction']=='192.00'
 assert ca.post('/api/stores/fuge/products/'+pb+'/calculate',json=evidence()).status_code==404
 assert cb.get('/api/stores/panse/export').json()['body']['data']['quotes']==[]
 assert ca.post('/api/stores/fuge/products/'+pa+'/calculate',json=evidence(store_id='panse')).status_code==422

@pytest.mark.parametrize('changes',[{'confirmed':False},{'single_item_rate':None},{'official_rate':None},{'platform_pre_coupon_price':0},{'platform_pre_coupon_price':'NaN'},{'list_price':'1'},{'price_basis':'unknown'},{'rule_version':'old'},{'source_sku_id':''},{'observed_at':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()}])
def test_price_missing_stale_or_unconfirmed(changes):
 with pytest.raises(ValueError):calculate(evidence(**changes))

def test_low_price_exact_rounding():
 out=calculate(evidence(platform_pre_coupon_price='50',list_price='100',single_item_rate='0.75'))
 assert out['official_deduction']=='11.25' and out['verified_final']=='35.00'

def test_empty_init_does_not_overwrite(tmp_path):
 with pytest.raises(ValueError):initialize(tmp_path,'fuge','x','long-local-test-password')
 with pytest.raises(ValueError):initialize(tmp_path/'new','fuge','x','short')
 assert not (tmp_path/'new').exists()

def test_backup_full_recovery_and_foreign_rejection(stores,tmp_path):
 from app.isolated_store_recovery import backup,recover
 a,b,ca,cb=stores;pa=product(ca,'fuge');ca.post('/api/stores/fuge/products/'+pa+'/media',files={'file':('a.png',photo(),'image/png')})
 raw=backup(a)
 with pytest.raises(ValueError):recover(b.root/'store.json',raw,tmp_path/'foreign')
 assert not (tmp_path/'foreign').exists()
 with pytest.raises(ValueError):recover(a.root/'store.json',raw,a.root)
 recovered=recover(a.root/'store.json',raw,tmp_path/'recovered')
 with recovered.db() as db:
  assert db.execute('SELECT COUNT(*) FROM products').fetchone()[0]==1
  assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
  record=dict(db.execute('SELECT * FROM media').fetchone())
 assert (recovered.root/record['path']).read_bytes()==photo()

def test_symlink_not_served(stores,tmp_path):
 a,b,ca,cb=stores
 try:(a.root/'media'/'link').symlink_to(b.root/'store.json')
 except OSError:pytest.skip('Windows symlink privilege unavailable')
 with pytest.raises(ValueError):safe_child(a.root,'media/link')

def test_login_rate_limit(stores):
 a,b,ca,cb=stores
 for i in range(5):assert ca.post('/api/stores/fuge/login',json={'username':'x','password':'wrong'}).status_code==401
 assert ca.post('/api/stores/fuge/login',json={'username':'tester','password':'long-local-test-password'}).status_code==429


def test_runtime_database_switch_fails_closed(stores):
 a,b,ca,cb=stores
 with a.db() as db:db.execute("UPDATE identity SET value='panse' WHERE key='store_id'")
 with pytest.raises(ValueError):a.export()

@pytest.mark.parametrize('changes',[{'shop_and_personal_coupons_excluded':False},{'price_basis':'buyer_final'}])
def test_old_price_basis_never_accepted(changes):
 with pytest.raises(ValueError):calculate(evidence(**changes))

def test_locked_setup_and_catalog(tmp_path):
 from app.isolated_store_setup import create_setup_app
 from app.isolated_store_catalog import import_catalog,add_catalog_image
 import re
 s=initialize(tmp_path/'locked','fuge');api=TestClient(create_app(s));api.headers['X-Store-ID']='fuge'
 assert api.post('/api/stores/fuge/login',json={'username':'x','password':'long-enough-password'}).status_code==503
 c=TestClient(create_setup_app(s),base_url='http://127.0.0.1:8292')
 assert c.get('/',headers={'host':'evil.example'}).status_code==403
 nonce=re.search('name="nonce" value="([^"]+)',c.get('/').text).group(1)
 body=dict(username='owner',password='long-enough-password',repeat='long-enough-password',nonce=nonce)
 assert c.post('/',data=body,headers={'Origin':'http://evil.example'}).status_code==403
 assert c.post('/',data=body,headers={'Origin':'http://127.0.0.1:8292'}).status_code==200
 assert c.post('/',data=body,headers={'Origin':'http://127.0.0.1:8292'}).status_code==422
 token=api.post('/api/stores/fuge/login',json={'username':'owner','password':'long-enough-password'}).json()['token'];api.headers['Authorization']='Bearer '+token
 document=dict(schema='fuge-catalog-import-v1',store_id='fuge',source_type='authorized_fuge_product_handoff',entries=[dict(source_key='001',title='书柜',metadata={'material':'实木多层板'})])
 r=import_catalog(s,document);rid=r['entries'][0]['id'];assert import_catalog(s,document)['entries'][0]['created'] is False
 m=add_catalog_image(s,rid,'主图','a.png',photo());assert api.get('/api/stores/fuge/catalog-media/'+m['id']).status_code==200
 assert api.get('/api/stores/fuge/catalog').json()['items'][0]['image_count']==1
 assert api.get('/api/stores/fuge/catalog-media/panse_unknown').status_code==404
 document['store_id']='panse'
 with pytest.raises(ValueError):import_catalog(s,document)
 assert api.get('/api/stores/fuge/products').json()['items']==[]

def test_streaming_catalog_recovery_and_alias(tmp_path):
 from app.isolated_store_catalog import import_catalog,add_catalog_image
 from app.isolated_store_recovery import backup_file,recover_file
 s=initialize(tmp_path/'source','fuge')
 d=dict(schema='fuge-catalog-import-v1',store_id='fuge',source_type='authorized_fuge_product_handoff',entries=[dict(source_key='001',title='x',metadata={})])
 rid=import_catalog(s,d)['entries'][0]['id'];m=add_catalog_image(s,rid,'其他','详情单屏/a.png',photo());s=Store(s.root,'fuge')
 with s.db() as db:assert db.execute('SELECT category FROM catalog_media').fetchone()[0]=='详情页'
 f=backup_file(s);r=recover_file(s.root/'store.json',f,tmp_path/'restored')
 assert (r.root/'media'/m['sha256']).read_bytes()==photo()
 with pytest.raises(ValueError):recover_file(s.root/'store.json',f,tmp_path/'rejected',max_bytes=1)
 assert not (tmp_path/'rejected').exists()
