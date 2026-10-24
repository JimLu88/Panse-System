"""One-shot authorized FUGE handoff import; no Panse DB or platform access."""
import argparse,hashlib,json,sys
from pathlib import Path
from app.isolated_store import Store,initialize,dumps
from app.isolated_store_catalog import import_catalog,add_catalog_image

def run(handoff,root):
 h=Path(handoff);read=lambda n:json.loads((h/n).read_text(encoding='utf-8-sig'))
 manifest=read('manifest.json')
 for f in manifest['files']:
  p=h/Path(f['path']).name
  if hashlib.sha256(p.read_bytes()).hexdigest()!=f['sha256']:raise ValueError('handoff_hash_changed:'+p.name)
 store=Store(root,'fuge') if Path(root).exists() else initialize(Path(root),'fuge')
 products=read('products.json');confirmed=read('confirmed-own-skus.json');candidates=read('candidate-sku-images.json');history=read('historical-competitor-skus.json')
 entries=[]
 for p in products:
  code=p['product_code'];m=dict(p)
  m['confirmed_skus']=[s for s in confirmed if s['product_code']==code]
  m['candidate_dimensions']=[{'variant':s['candidate_variant'],'dimensions_cm':s['candidate_dimensions_cm'],'status':'candidate_only'} for s in candidates if s['product_code']==code]
  m['historical_reference']=[s for s in history if s.get('product_code')==code]
  m['pricing_policy']=read('pricing-policy.json')
  entries.append(dict(source_key=code,title=p['title'],metadata=m,status='draft'))
 receipt=import_catalog(store,dict(schema='fuge-catalog-import-v1',store_id='fuge',source_type='authorized_fuge_product_handoff',entries=entries))
 ids={r['source_key']:r['id'] for r in receipt['entries']}
 with store.db() as db:
  for s in confirmed:
   if not s.get('sellable_confirmed'):raise ValueError('unconfirmed_sku')
   code=s['local_draft_sku_code'];name=next(p['title'] for p in products if p['product_code']==s['product_code']);spec=dumps(s['specification'])
   old=db.execute('SELECT * FROM products WHERE code=?',(code,)).fetchone()
   if old and (old['name']!=name or old['specification']!=spec):raise ValueError('existing_sku_changed')
   if not old:
    rid=store.new_id();db.execute('INSERT INTO products VALUES(?,?,?,?)',(rid,code,name,spec));store.audit(db,'import_confirmed_sku_draft_code',rid)
 total=0
 for i,a in enumerate(read('gallery-assets.json')):
  p=Path(a['path']);raw=p.read_bytes()
  if len(raw)!=a['bytes'] or hashlib.sha256(raw).hexdigest()!=a['sha256']:raise ValueError('source_image_changed:'+str(p))
  category=a['relative_path'].split('/')[0]
  if category=='详情单屏':category='详情页'
  if category not in ('主图','SKU图','详情页','评价图'):category='其他'
  add_catalog_image(store,ids[a['product_code']],category,a['relative_path'],raw);total+=len(raw)
  if (i+1)%100==0:print('copied',i+1,flush=True)
 result={'store_id':'fuge','catalog_count':len(products),'confirmed_skus':len(confirmed),'candidate_dimensions':len(candidates),'image_sources':i+1,'source_bytes':total,'active_price_count':0,'source_preserved':True}
 (h.parent/'erp-maintenance'/'import-receipt.json').write_text(dumps(result),encoding='utf8');print(dumps(result),flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--handoff',required=True);p.add_argument('--root',required=True);a=p.parse_args();run(a.handoff,a.root)
