"""FUGE source catalogue, deliberately distinct from confirmed sellable SKU records."""
import hashlib,io,json,sqlite3,uuid
from pathlib import Path
from fastapi import Depends,HTTPException
from fastapi.responses import Response
from PIL import Image

SCHEMA='fuge-catalog-import-v1'
def ensure_schema(store):
    with store.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS catalog(id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, title TEXT NOT NULL, metadata TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft');
        CREATE TABLE IF NOT EXISTS catalog_media(id TEXT PRIMARY KEY, catalog_id TEXT NOT NULL REFERENCES catalog(id), category TEXT NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL, original_name TEXT NOT NULL, UNIQUE(catalog_id,category,sha256));
        CREATE TABLE IF NOT EXISTS source_packages(sha256 TEXT PRIMARY KEY, imported_at TEXT NOT NULL, entry_count INTEGER NOT NULL, document TEXT NOT NULL);
        ''')

def import_catalog(store, document):
    from app.isolated_store import dumps
    from datetime import datetime,timezone
    if document.get('schema')!=SCHEMA or document.get('store_id')!=store.owner or document.get('source_type')!='authorized_fuge_product_handoff':raise ValueError('foreign_or_unapproved_import')
    entries=document.get('entries')
    if not isinstance(entries,list) or not 1<=len(entries)<=2000:raise ValueError('invalid_entry_count')
    encoded=dumps(document);sha=hashlib.sha256(encoded.encode()).hexdigest();result=[]
    with store.db() as db:
        for entry in entries:
            key=entry.get('source_key');title=entry.get('title');metadata=entry.get('metadata');status=entry.get('status','draft')
            if not isinstance(key,str) or not key or len(key)>100 or not isinstance(title,str) or not title or not isinstance(metadata,dict) or status!='draft':raise ValueError('invalid_catalog_entry')
            # No imported competitor/history/candidate row becomes a sellable SKU or active price.
            if entry.get('sellable_skus') or entry.get('active_price') is not None:raise ValueError('explicit_sku_and_price_review_required')
            old=db.execute('SELECT * FROM catalog WHERE source_key=?',(key,)).fetchone()
            raw=dumps(metadata)
            if old:
                if old['metadata']!=raw or old['title']!=title:raise ValueError('existing_catalog_changed_requires_review')
                rid=old['id']
            else:
                rid=store.new_id();db.execute('INSERT INTO catalog VALUES(?,?,?,?,?)',(rid,key,title,raw,status));store.audit(db,'import_catalog_draft',rid)
            result.append({'source_key':key,'id':rid,'created':not bool(old)})
        db.execute('INSERT OR IGNORE INTO source_packages VALUES(?,?,?,?)',(sha,datetime.now(timezone.utc).isoformat(),len(entries),encoded))
    return {'sha256':sha,'entries':result,'sellable_skus_created':0,'active_prices_written':0}

def add_catalog_image(store,catalog_id,category,name,raw):
    from app.isolated_store import safe_child
    if category not in ('主图','SKU图','详情页','评价图','其他'):raise ValueError('invalid_image_category')
    if len(raw)>30*1024*1024:raise ValueError('image_too_large')
    with store.db() as db:
        if not db.execute('SELECT 1 FROM catalog WHERE id=?',(catalog_id,)).fetchone():raise ValueError('catalog_not_found')
    im=Image.open(io.BytesIO(raw));im.verify()
    if im.format not in ('JPEG','PNG','WEBP','GIF'):raise ValueError('unsupported_image')
    sha=hashlib.sha256(raw).hexdigest();relative='media/'+sha;target=safe_child(store.root,relative)
    if not target.exists():
        with target.open('xb') as f:f.write(raw)
    if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise ValueError('stored_image_mismatch')
    with store.db() as db:
        old=db.execute('SELECT id FROM catalog_media WHERE catalog_id=? AND category=? AND sha256=?',(catalog_id,category,sha)).fetchone()
        mid=old['id'] if old else store.new_id()
        if not old:
            db.execute('INSERT INTO catalog_media VALUES(?,?,?,?,?,?)',(mid,catalog_id,category,sha,relative,name));store.audit(db,'copy_catalog_image',mid)
    return {'id':mid,'sha256':sha,'created':not bool(old),'bytes':len(raw)}

def register_routes(app,store,prefix,authenticate):
    @app.get(prefix+'/catalog',dependencies=[Depends(authenticate)])
    def catalogue(q:str=''):
        with store.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM catalog WHERE title LIKE ? OR source_key LIKE ? ORDER BY source_key',('%'+q[:100]+'%','%'+q[:100]+'%'))]
            for row in rows:
                row['metadata']=json.loads(row['metadata']);row['image_count']=db.execute('SELECT COUNT(*) FROM catalog_media WHERE catalog_id=?',(row['id'],)).fetchone()[0]
        return {'store_id':store.owner,'items':rows}
    @app.get(prefix+'/catalog/{rid}/media',dependencies=[Depends(authenticate)])
    def media(rid:str):
        with store.db() as db:
            if not db.execute('SELECT 1 FROM catalog WHERE id=?',(rid,)).fetchone():raise HTTPException(404,'catalog_not_found')
            return {'items':[dict(r) for r in db.execute('SELECT id,category,original_name,sha256 FROM catalog_media WHERE catalog_id=? ORDER BY category,original_name',(rid,))]}
    @app.get(prefix+'/catalog-media/{rid}',dependencies=[Depends(authenticate)])
    def image(rid:str,thumbnail:bool=True):
        from app.isolated_store import safe_child
        with store.db() as db:record=db.execute('SELECT * FROM catalog_media WHERE id=?',(rid,)).fetchone()
        if record is None:raise HTTPException(404,'image_not_found')
        raw=safe_child(store.root,record['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise HTTPException(409,'image_hash_mismatch')
        im=Image.open(io.BytesIO(raw))
        if thumbnail:
            im.thumbnail((400,400));out=io.BytesIO();im.convert('RGB').save(out,format='JPEG');return Response(out.getvalue(),media_type='image/jpeg')
        return Response(raw,media_type=Image.MIME[im.format])

