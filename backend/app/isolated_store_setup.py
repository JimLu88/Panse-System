"""Single-use account setup. Publish only on NAS loopback via an SSH tunnel."""
import json,os,secrets
from pathlib import Path
from fastapi import FastAPI,Request,HTTPException,Form
from fastapi.responses import HTMLResponse
from app.isolated_store import Store,safe_child,password_hash,dumps

def set_admin(store,username,password):
    if not username.strip() or len(username)>100 or not 14<=len(password)<=200:raise ValueError('账号不能为空，密码至少14个字符')
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        path=safe_child(store.root,'store.json');config=json.loads(path.read_text(encoding='utf8'))
        if config.get('username') or config.get('password_hash'):raise ValueError('账号已经设置，不能重复初始化')
        salt=secrets.token_hex(16);config.update(username=username.strip(),salt=salt,password_hash=password_hash(password,salt))
        temporary=safe_child(store.root,'setup-'+secrets.token_hex(8)+'.json');temporary.write_text(dumps(config),encoding='utf8');temporary.chmod(0o600);temporary.replace(path)
        db.execute('DELETE FROM sessions');store.audit(db,'account_initialized','local-setup')
    store.config=config

def create_setup_app(store):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None);nonce=secrets.token_urlsafe(32)
    @app.middleware('http')
    async def local_only(request,call_next):
        host=request.headers.get('host','')
        if host.split(':')[0] not in ('127.0.0.1','localhost'):return HTMLResponse('仅限本机设置入口',status_code=403)
        if request.method=='POST' and request.headers.get('origin')!='http://'+host:
            return HTMLResponse('不接受外部页面提交',status_code=403)
        r=await call_next(request);r.headers['Cache-Control']='no-store';r.headers['X-Frame-Options']='DENY';r.headers['Referrer-Policy']='no-referrer';return r
    @app.get('/',response_class=HTMLResponse)
    def page():
        config=json.loads(safe_child(store.root,'store.json').read_text(encoding='utf8'))
        if config.get('username'):return '<h2>孚格账号已设置</h2><p>请回到孚格 ERP 登录。这个初始化入口现已关闭。</p>'
        return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>设置孚格独立账号</title><style>body{font:18px system-ui;max-width:600px;margin:70px auto;background:#f5f2ec}input,button{display:block;padding:12px;margin:16px 0;width:90%}button{background:#244b3b;color:white;border:0}</style><h1>设置孚格独立账号</h1><p>仅设置孚格，不改变畔色账号。密码不发送到对话。</p><form method="post"><input type="hidden" name="nonce" value="'''+nonce+'''"><label>新账号<input name="username" required maxlength="100" autocomplete="username"></label><label>密码（至少14位）<input type="password" name="password" required minlength="14" maxlength="200" autocomplete="new-password"></label><label>再输入一次密码<input type="password" name="repeat" required minlength="14" maxlength="200" autocomplete="new-password"></label><button>保存独立账号</button></form></html>'''
    @app.post('/',response_class=HTMLResponse)
    def save(username:str=Form(...),password:str=Form(...),repeat:str=Form(...),nonce_value:str=Form(...,alias='nonce')):
        if not secrets.compare_digest(nonce_value,nonce) or password!=repeat:return HTMLResponse('两次密码不一致或页面已失效，请返回重新填写。',status_code=422)
        try:set_admin(store,username,password)
        except ValueError as e:return HTMLResponse(str(e),status_code=422)
        return '<h2>孚格独立账号已设置成功</h2><p>现在可以关闭本页，打开孚格 ERP 登录。</p>'
    return app

def environment_app():
    if os.environ.get('ISOLATED_STORE_ID')!='fuge':raise RuntimeError('explicit_fuge_required')
    return create_setup_app(Store(os.environ['ISOLATED_STORE_ROOT'],'fuge'))
