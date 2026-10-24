import json
import bcrypt
import pytest
from fastapi.testclient import TestClient
from app.isolated_store import initialize, create_app
from app.isolated_store_account import import_owner, verify_password


def owner():
    return dict(source='panse-owner-user-authorized', role='admin', active=True,
                must_change_password=False, username='owner',
                password_hash=bcrypt.hashpw(b'existing-pass', bcrypt.gensalt(rounds=4)).decode())


def test_reuse_login_and_no_cross_store_tokens(tmp_path):
    a=initialize(tmp_path/'a','fuge');b=initialize(tmp_path/'b','panse')
    original=json.loads((a.root/'store.json').read_text())
    payload=owner()
    receipt=import_owner(a,payload);import_owner(b,payload)
    assert 'password_hash' not in receipt
    config=json.loads((a.root/'store.json').read_text())
    assert config['backup_key']==original['backup_key']
    ca=TestClient(create_app(a),headers={'X-Store-ID':'fuge'})
    cb=TestClient(create_app(b),headers={'X-Store-ID':'panse'})
    assert ca.get('/health').json()['account_ready']
    assert ca.post('/api/stores/fuge/login',json={'username':'owner','password':'wrong'}).status_code==401
    response=ca.post('/api/stores/fuge/login',json={'username':'owner','password':'existing-pass'})
    assert response.status_code==200
    token=response.json()['token']
    assert ca.get('/api/stores/fuge/products',headers={'Authorization':'Bearer '+token}).status_code==200
    assert cb.get('/api/stores/panse/products',headers={'Authorization':'Bearer '+token}).status_code==401
    assert 'password_hash' not in json.dumps(a.export())
    with pytest.raises(ValueError,match='already_configured'):import_owner(a,payload)


@pytest.mark.parametrize('changes',[{'active':False},{'role':'viewer'},{'must_change_password':True},{'source':'unknown'},{'password_hash':'bad'},{'username':''}])
def test_invalid_owner_cannot_initialize(tmp_path,changes):
    store=initialize(tmp_path/'a','fuge');p=owner();p.update(changes)
    with pytest.raises(ValueError):import_owner(store,p)
    assert json.loads((store.root/'store.json').read_text())['username'] is None


def test_unknown_scheme_and_long_password_fail_closed():
    assert not verify_password('x',{'password_scheme':'unknown','password_hash':'x'})
    assert not verify_password('x'*200,{'password_scheme':'bcrypt','password_hash':owner()['password_hash']})
