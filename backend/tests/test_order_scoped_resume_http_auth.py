from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import dependencies as deps
from app.api.imports import router
from app.database import get_db
from app.services import order_scoped_resume_service as svc


BODY = {
    'business_date': '2026-09-19',
    'order_batch_id': 'orders-20260919-fca9a7d3462846c8aa512a3648f35916',
    'file_ids': [3025, 3027, 3033],
    'sub_order_nos': ['3316428051023004178', '3316871605005137171', '3316871605005146353'],
    'request_id': '20260919000040008000000000000001',
    'dry_run': True,
}
PATH = '/api/imports/order-sheets/resume-scoped'


@pytest.fixture
def http_case(monkeypatch):
    monkeypatch.setenv('PANSE_AUTH_ENFORCE', '1')
    users = {
        1: SimpleNamespace(is_active=True, role='operator', page_perms=None),
        2: SimpleNamespace(is_active=False, role='operator', page_perms=None),
        3: SimpleNamespace(is_active=True, role='viewer', page_perms=None),
        4: SimpleNamespace(is_active=True, role='admin', page_perms=None),
    }
    db = SimpleNamespace(get=lambda cls, uid: users.get(uid))
    keys = {'ingest_api_token': 'test-ingest', 'cs_api_key': 'test-cs',
            'campaign_prepare_service_token': 'test-campaign', 'web_agent_token': 'test-web'}
    monkeypatch.setattr(deps.settings_service, 'get', lambda db, name, **kw: keys.get(name))
    monkeypatch.setattr(deps.auth_service, 'decode_token', lambda token: {'uid': int(token)})
    calls = []
    def resume(db, **payload):
        calls.append(payload)
        return {'status': 'preview' if payload['dry_run'] else 'done'}
    monkeypatch.setattr(svc, 'resume', resume)
    app = FastAPI(dependencies=[Depends(deps.require_authenticated), Depends(deps.enforce_page_permission)])
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, calls


@pytest.mark.parametrize('dry_run', [True, False])
def test_exact_machine_scope_reaches_service_over_http(http_case, dry_run):
    client, calls = http_case
    body = dict(BODY, dry_run=dry_run)
    result = client.post(PATH, json=body, headers={'X-API-Key': 'test-cs'})
    assert result.status_code == 200
    assert calls == [body]


@pytest.mark.parametrize('key', [None, 'invalid', 'test-ingest', 'test-campaign', 'test-web'])
def test_other_machine_keys_denied(http_case, key):
    client, calls = http_case
    headers = {'X-API-Key': key} if key else {}
    assert client.post(PATH, json=BODY, headers=headers).status_code == 401
    assert calls == []


@pytest.mark.parametrize('change', [
    {'business_date': '2026-09-20'},
    {'order_batch_id': 'orders-20260919-' + 'a'*32},
    {'file_ids': [3025, 3027, 3034]},
    {'file_ids': [3025, 3025, 3033]},
    {'sub_order_nos': BODY['sub_order_nos'] + ['other']},
    {'sub_order_nos': BODY['sub_order_nos'][:2]},
    {'request_id': 'a'*32},
])
def test_scope_change_denied_before_service(http_case, change):
    client, calls = http_case
    assert client.post(PATH, json=dict(BODY, **change), headers={'X-API-Key': 'test-cs'}).status_code == 403
    assert calls == []


def test_machine_not_promoted_to_broad_push_role(http_case):
    client, calls = http_case
    assert client.post('/api/imports/order-sheets/push', json={'limit': 3},
                       headers={'X-API-Key': 'test-cs'}).status_code == 401
    assert calls == []


@pytest.mark.parametrize('uid,expected', [(1, 200), (2, 403), (3, 403), (4, 200)])
def test_existing_user_roles_preserved(http_case, uid, expected):
    client, calls = http_case
    # Disabled/viewer users must not fall back to the machine identity.
    result = client.post(PATH, json=BODY, headers={'Authorization': f'Bearer {uid}', 'X-API-Key': 'test-cs'})
    assert result.status_code == expected
    assert len(calls) == int(expected == 200)
