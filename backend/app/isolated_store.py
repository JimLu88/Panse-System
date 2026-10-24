"""Separate store runtime. Deliberately does not import app.main/database/settings.

One process, one immutable owner, one dedicated volume. No Panse API forwarding,
background scheduler, environment credential fallback, external sending or migration.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from app.isolated_store_pricing import calculate

DISABLED = ['orders','customers','inventory','procurement','logistics','finance','notifications','platform_accounts','background_jobs','restore_online']

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',',':'))

def password_hash(password, salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()

def safe_child(root, relative):
    if not relative or '\\' in relative or ':' in relative or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('invalid_path')
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('symlink_denied')
    resolved = current.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('outside_store')
    return resolved

class Store:
    def __init__(self, root, owner):
        self.root = Path(root)
        if not self.root.is_absolute() or self.root.is_symlink() or not re.fullmatch('[a-z][a-z0-9_-]{1,30}',owner):
            raise ValueError('explicit_store_and_absolute_root_required')
        self.root = self.root.resolve()
        self.owner = owner
        self.config = json.loads(safe_child(self.root,'store.json').read_text(encoding='utf8'))
        if self.config.get('store_id') != owner:
            raise ValueError('store_root_owner_mismatch')
        self.instance = self.config['instance_id']
        self.db_path = safe_child(self.root,'store.sqlite')
        if not self.db_path.is_file():
            raise ValueError('uninitialized_store')
        with self.db() as db:
            meta = dict(db.execute('select key,value from identity'))
        if meta != {'store_id':owner,'instance_id':self.instance}:
            raise ValueError('database_owner_mismatch')
        for folder in ['media','thumbs','exports','backups','logs']:
            if not safe_child(self.root,folder).is_dir():
                raise ValueError('missing_store_directory')
    @contextmanager
    def db(self):
        db = sqlite3.connect(str(self.db_path),timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            identity = dict(db.execute('SELECT key,value FROM identity'))
            if identity != {'store_id': self.owner, 'instance_id': self.instance}:
                raise ValueError('database_owner_mismatch')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    def new_id(self):
        return self.owner+'_'+uuid.uuid4().hex
    def resource(self,db,table,rid):
        if table not in ('products','media','quotes') or not rid.startswith(self.owner+'_'):
            raise HTTPException(404,'resource_not_found')
        row = db.execute(f'SELECT * FROM {table} WHERE id=?',(rid,)).fetchone()
        if row is None:
            raise HTTPException(404,'resource_not_found')
        return dict(row)
    def audit(self,db,action,rid):
        db.execute('INSERT INTO audit(created_at,store_id,action,resource_id) VALUES(?,?,?,?)',(time.time(),self.owner,action,rid))
    def export(self):
        with self.db() as db:
            data={t:[dict(r) for r in db.execute('SELECT * FROM '+t)] for t in ['products','media','quotes','audit']}
        body={'schema':'isolated-store-export-v1','store_id':self.owner,'instance_id':self.instance,'exported_at':datetime.now(timezone.utc).isoformat(),'data':data}
        signature=hmac.new(bytes.fromhex(self.config['backup_key']),dumps(body).encode(),hashlib.sha256).hexdigest()
        return {'body':body,'signature':signature}
    def verify_export(self,document):
        body=document.get('body',{})
        if body.get('store_id')!=self.owner or body.get('instance_id')!=self.instance or body.get('schema')!='isolated-store-export-v1':
            raise ValueError('foreign_backup_rejected')
        expected=hmac.new(bytes.fromhex(self.config['backup_key']),dumps(body).encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected,document.get('signature','')):
            raise ValueError('backup_signature_mismatch')
        return body

def initialize(root, owner, username, password):
    root=Path(root)
    if not root.is_absolute() or root.exists() or not re.fullmatch('[a-z][a-z0-9_-]{1,30}',owner):
        raise ValueError('new_absolute_directory_required')
    if len(password)<14 or not username.strip():
        raise ValueError('independent_username_and_14_character_password_required')
    root.mkdir(parents=True)
    salt=secrets.token_hex(16);instance=uuid.uuid4().hex
    config={'store_id':owner,'instance_id':instance,'username':username,'salt':salt,'password_hash':password_hash(password,salt),'backup_key':secrets.token_hex(32)}
    (root/'store.json').write_text(dumps(config),encoding='utf8');os.chmod(root/'store.json',0o600)
    for folder in ['media','thumbs','exports','backups','logs']:(root/folder).mkdir()
    with sqlite3.connect(root/'store.sqlite') as db:
        db.executescript('''
        CREATE TABLE identity(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE products(id TEXT PRIMARY KEY,code TEXT UNIQUE NOT NULL,name TEXT NOT NULL,specification TEXT NOT NULL);
        CREATE TABLE media(id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),sha256 TEXT NOT NULL,path TEXT NOT NULL,UNIQUE(product_id,sha256));
        CREATE TABLE quotes(id TEXT PRIMARY KEY,product_id TEXT NOT NULL REFERENCES products(id),document TEXT NOT NULL);
        CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,expires REAL NOT NULL);
        CREATE TABLE audit(id INTEGER PRIMARY KEY,created_at REAL NOT NULL,store_id TEXT NOT NULL,action TEXT NOT NULL,resource_id TEXT NOT NULL);
        ''')
        db.executemany('INSERT INTO identity VALUES(?,?)',[('store_id',owner),('instance_id',instance)])
    return Store(root,owner)

class ProductInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    code:str=Field(min_length=1,max_length=80,pattern=r'^[A-Za-z0-9_-]+$')
    name:str=Field(min_length=1,max_length=200)
    specification:str=Field(min_length=1,max_length=1000)

class LoginInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:str=Field(max_length=100)
    password:str=Field(max_length=200)

def create_app(store):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    prefix='/api/stores/'+store.owner
    failures={}
    @app.middleware('http')
    async def identity(request,call_next):
        path=request.url.path
        if path.startswith('/api/'):
            if not path.startswith(prefix+'/') or request.headers.get('x-store-id')!=store.owner:
                return Response(dumps({'detail':'explicit_store_identity_required'}),status_code=403,media_type='application/json')
            origin=request.headers.get('origin')
            if origin and origin!=str(request.base_url).rstrip('/'):
                return Response(status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Store-ID']=store.owner
        response.headers['Referrer-Policy']='no-referrer'
        return response
    def authenticate(request:Request):
        auth=request.headers.get('authorization','')
        if not auth.startswith('Bearer '):raise HTTPException(401,'store_login_required')
        token=auth[7:]
        digest=hashlib.sha256(token.encode()).hexdigest()
        with store.db() as db:
            row=db.execute('SELECT expires FROM sessions WHERE token_hash=?',(digest,)).fetchone()
        if row is None or row['expires']<time.time():raise HTTPException(401,'store_login_required')
    @app.get('/health')
    def health():return {'ok':True,'store_id':store.owner,'enabled':['products','gallery','price_calculation'],'disabled':DISABLED}
    @app.post(prefix+'/login')
    def login(body:LoginInput,request:Request):
        ip=request.client.host if request.client else 'unknown';now=time.time()
        failures[ip]=[t for t in failures.get(ip,[]) if now-t<300]
        if len(failures[ip])>=5:raise HTTPException(429,'login_rate_limited')
        valid=hmac.compare_digest(password_hash(body.password,store.config['salt']),store.config['password_hash'])
        if not valid or body.username!=store.config['username']:
            failures[ip].append(now);raise HTTPException(401,'invalid_store_login')
        token=secrets.token_urlsafe(48)
        with store.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?',(now,))
            db.execute('INSERT INTO sessions VALUES(?,?)',(hashlib.sha256(token.encode()).hexdigest(),now+3600))
        return {'store_id':store.owner,'token':token,'expires_in':3600}
    @app.get(prefix+'/products',dependencies=[Depends(authenticate)])
    def products(q:str=''):
        with store.db() as db:
            rows=db.execute('SELECT * FROM products WHERE name LIKE ? OR code LIKE ? ORDER BY code',('%'+q[:100]+'%','%'+q[:100]+'%')).fetchall()
        return {'store_id':store.owner,'items':[dict(r) for r in rows]}
    @app.post(prefix+'/products',dependencies=[Depends(authenticate)])
    def add_product(body:ProductInput):
        rid=store.new_id()
        try:
            with store.db() as db:
                db.execute('INSERT INTO products VALUES(?,?,?,?)',(rid,body.code,body.name,body.specification));store.audit(db,'create_product',rid)
        except sqlite3.IntegrityError:raise HTTPException(409,'product_code_exists')
        return {'id':rid,'store_id':store.owner,**body.model_dump()}
    @app.get(prefix+'/products/{rid}',dependencies=[Depends(authenticate)])
    def product(rid:str):
        with store.db() as db:return store.resource(db,'products',rid)
    @app.post(prefix+'/products/{rid}/media',dependencies=[Depends(authenticate)])
    async def upload(rid:str,file:UploadFile=File(...)):
        with store.db() as db:store.resource(db,'products',rid)
        raw=await file.read(20*1024*1024+1)
        if len(raw)>20*1024*1024:raise HTTPException(413,'image_too_large')
        try:
            im=Image.open(io.BytesIO(raw));im.verify()
            if im.format not in ('JPEG','PNG','WEBP'):raise ValueError()
        except Exception:raise HTTPException(422,'invalid_image')
        sha=hashlib.sha256(raw).hexdigest()
        # Files are addressed within this store only; no global hash cache.
        relative='media/'+sha
        target=safe_child(store.root,relative)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest()!=sha:raise HTTPException(409,'stored_file_corrupt')
        else:
            with target.open('xb') as stream:stream.write(raw)
        with store.db() as db:
            row=db.execute('SELECT id FROM media WHERE product_id=? AND sha256=?',(rid,sha)).fetchone()
            mid=row['id'] if row else store.new_id()
            if not row:
                db.execute('INSERT INTO media VALUES(?,?,?,?)',(mid,rid,sha,relative));store.audit(db,'upload_image',mid)
        return {'id':mid,'sha256':sha,'store_id':store.owner}
    @app.get(prefix+'/products/{rid}/media',dependencies=[Depends(authenticate)])
    def gallery(rid:str):
        with store.db() as db:
            store.resource(db,'products',rid)
            return {'items':[dict(r) for r in db.execute('SELECT id,sha256 FROM media WHERE product_id=?',(rid,))]}
    @app.get(prefix+'/media/{rid}',dependencies=[Depends(authenticate)])
    def image_file(rid:str,thumbnail:bool=False):
        with store.db() as db:record=store.resource(db,'media',rid)
        try:raw=safe_child(store.root,record['path']).read_bytes()
        except (ValueError,OSError):raise HTTPException(404,'image_missing')
        if hashlib.sha256(raw).hexdigest()!=record['sha256']:raise HTTPException(409,'image_hash_mismatch')
        im=Image.open(io.BytesIO(raw))
        if thumbnail:
            im.thumbnail((320,320));out=io.BytesIO();im.convert('RGB').save(out,format='JPEG');raw=out.getvalue();mime='image/jpeg'
        else:mime=Image.MIME[im.format]
        return Response(raw,media_type=mime)
    @app.post(prefix+'/products/{rid}/calculate',dependencies=[Depends(authenticate)])
    def quote(rid:str,payload:dict):
        if any(k in payload for k in ('store_id','tenant','product_id','path')):raise HTTPException(422,'identity_override_denied')
        try:document=calculate(payload)
        except (ValueError,ArithmeticError):raise HTTPException(422,'invalid_or_unconfirmed_price_evidence')
        qid=store.new_id()
        with store.db() as db:
            store.resource(db,'products',rid)
            db.execute('INSERT INTO quotes VALUES(?,?,?)',(qid,rid,dumps(document)));store.audit(db,'calculate_only',qid)
        return {'id':qid,'store_id':store.owner,**document}
    @app.get(prefix+'/export',dependencies=[Depends(authenticate)])
    def export():return store.export()
    @app.post(prefix+'/restore/validate',dependencies=[Depends(authenticate)])
    def restore_validate(payload:dict):
        try:store.verify_export(payload)
        except ValueError:raise HTTPException(422,'foreign_or_modified_backup_rejected')
        return {'valid':True,'restored':False,'reason':'offline_recovery_only'}
    @app.get('/stores/'+store.owner,response_class=HTMLResponse)
    def ui():
        source=Path(__file__).with_name('isolated_store.html').read_text(encoding='utf8')
        return source.replace('__STORE_ID__',store.owner)
    return app

def environment_app():
    owner=os.environ.get('ISOLATED_STORE_ID')
    root=os.environ.get('ISOLATED_STORE_ROOT')
    if owner!='fuge' or not root:raise RuntimeError('explicit_fuge_runtime_configuration_required')
    return create_app(Store(root,owner))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['initialize','backup']);parser.add_argument('--root',required=True);parser.add_argument('--store',required=True);parser.add_argument('--username')
    args=parser.parse_args()
    if args.action=='initialize':
        import getpass
        password=getpass.getpass('New independent store password (14+ characters): ')
        if password!=getpass.getpass('Repeat password: '):raise SystemExit('Passwords differ')
        initialize(args.root,args.store,args.username or '',password);print('Empty independent store initialized')
    else:
        store=Store(args.root,args.store);document=store.export();target=safe_child(store.root,'backups/'+uuid.uuid4().hex+'.json');target.write_text(dumps(document),encoding='utf8');print('Metadata backup saved; media requires separate same-store backup')
