import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models.settings import SystemSetting
from app.services import feedback_notification_service as service


@pytest.fixture
def setup(monkeypatch):
    engine=create_engine('sqlite:///:memory:')
    SystemSetting.__table__.create(engine)
    db=Session(engine)
    monkeypatch.delenv('PANSE_DISABLE_NOTIFY',raising=False)
    config={'route_mode':'feishu_split','alert_chat_id':'ALERT_ONLY'}
    monkeypatch.setattr(service.notify_service,'get_config',lambda db:config)
    calls=[]
    def send(db,group,text):
        calls.append(group)
        return {'message_id':'receipt-1'}
    monkeypatch.setattr(service.feishu_client,'send_text',send)
    yield SimpleNamespace(db=db,config=config,calls=calls)
    db.close()


def submit(state,text='【店铺口碑巡检】测试'):
    return service.send_digest(state.db,hashlib.sha256(text.encode()).hexdigest(),text)


def test_success_and_duplicate(setup):
    assert submit(setup)['message_id']=='receipt-1'
    assert submit(setup)['duplicate']
    assert setup.calls==['ALERT_ONLY']


def test_no_wechat_or_factory_fallback(setup):
    setup.config['route_mode']='legacy'
    assert submit(setup)['reason']=='feishu_alert_not_configured'
    assert not setup.calls


def test_timeout_not_replayed(setup,monkeypatch):
    def fail(*args):
        setup.calls.append('once')
        raise TimeoutError('private detail')
    monkeypatch.setattr(service.feishu_client,'send_text',fail)
    result=submit(setup)
    assert result['status']=='unknown' and result['reason']=='TimeoutError'
    assert submit(setup)['duplicate']
    assert setup.calls==['once']


def test_no_receipt_is_unknown(setup,monkeypatch):
    monkeypatch.setattr(service.feishu_client,'send_text',lambda *args:{})
    assert submit(setup)['delivered'] is False


def test_disabled_does_not_send(setup,monkeypatch):
    monkeypatch.setenv('PANSE_DISABLE_NOTIFY','1')
    assert not submit(setup)['delivered']
    assert not setup.calls


@pytest.mark.parametrize('text',['other title','【店铺口碑巡检】'+'长'*10000],ids=['wrong-title','oversized'])
def test_invalid_body(setup,text):
    with pytest.raises(ValueError): submit(setup,text)


def test_hash_mismatch(setup):
    with pytest.raises(ValueError):
        service.send_digest(setup.db,'wrong','【店铺口碑巡检】测试')
