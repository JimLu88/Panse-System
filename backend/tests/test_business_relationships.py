import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.services import business_relationship_service as s


def test_catalog_has_all_domains_and_valid_links():
    assert len(s.catalog.DOMAINS) == 18
    assert not s.validate()
    assert len(s.catalog.NODES) >= 65 and len(s.catalog.EDGES) >= 75


def test_sources_and_anchors_resolve():
    data=s.snapshot()
    assert not [(n['id'],ref) for n in data['nodes']+data['edges'] for ref in n['sources']
                if ref['state'] in ('missing','anchor_missing')]
    assert not data['runtime_verified'] and data['read_only']


def test_full_schema_inventory_is_not_claimed_as_semantic_coverage():
    inv=s.inventory()
    assert inv['model_count'] >= 100 and inv['field_count'] > 1000
    assert inv['unmapped_files'] and not inv['errors']
    assert any(m['model']=='OrderDetail' and 'qty' in m['fields'] for m in inv['models'])
    assert '不代表全部字段' in inv['note']


@pytest.mark.parametrize('direction',['upstream','downstream'])
def test_impact_cycle_safe_and_bounded(direction):
    data=s.snapshot()
    data['edges'].append({**data['edges'][0],'id':'cycle','from':'factory.sheet','to':'quantity.physical'})
    report=s.impact(['quantity.physical'],direction=direction,depth=12,data=data)
    assert len(report['nodes'])==len({n['id'] for n in report['nodes']})
    assert len(report['nodes'])<=len(data['nodes'])


def test_protected_and_uncertain_downstream_still_listed_for_review():
    report=s.impact(['quantity.physical'],depth=6)
    assert {'factory.sheet','sync.factory','finance.actual','finance.profit','stock.lock'} <= {n['id'] for n in report['nodes']}
    assert any(e['to']=='finance.actual' and e['kind']=='protect' for e in report['edges'])
    assert any(e['to']=='stock.lock' and e['evidence']=='gap' for e in report['edges'])
    assert '禁止' in report['notice']


def test_depth_limit_is_explicit():
    report=s.impact(['quantity.physical'],depth=1)
    assert report['truncated']
    assert max(n['distance'] for n in report['nodes'])==1


@pytest.mark.parametrize('kw',[{'node_ids':['unknown']},{'node_ids':[]},{'node_ids':['order.qty'],'depth':0},{'node_ids':['order.qty'],'depth':13},{'node_ids':['order.qty'],'direction':'bad'}])
def test_invalid_scope_rejected(kw):
    with pytest.raises(ValueError): s.impact(**kw)


def test_changed_edge_only_source_is_included():
    report=s.change_report(['backend/app/models/sku_identity.py','frontend/unknown.tsx'])
    assert report['impact']
    assert 'frontend/unknown.tsx' in report['unmapped_changes']
    assert 'product.sku' in report['impact']['roots']


def test_missing_lock_never_matches(tmp_path):
    data=s.snapshot(lock=tmp_path/'absent')
    assert all(state!='matching' for state in data['source_states'].values())


def test_stale_lock_reports_changed_without_overwriting(tmp_path):
    path=tmp_path/'lock.json'
    baseline={'sources':{name:'wrong' for name in s.referenced_sources()}}
    path.write_text(json.dumps(baseline))
    before=path.read_bytes()
    data=s.snapshot(lock=path)
    assert set(data['source_states'].values())=={'changed'}
    assert path.read_bytes()==before


def test_no_arbitrary_file_source():
    with pytest.raises(ValueError):s._path(Path('/tmp/app'),{'path':'backend/app/../../password'})


@pytest.mark.parametrize('role,expected',[('admin',200),('operator',200),('viewer',403)])
def test_read_only_api_role_guard(role,expected,monkeypatch):
    from app.api import admin
    from app.dependencies import get_current_user
    app=FastAPI();app.include_router(admin.router)
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(role=role)
    monkeypatch.setattr(s,'snapshot',lambda:{'read_only':True,'runtime_verified':False})
    client=TestClient(app)
    assert client.get('/api/admin/business-relationships').status_code==expected
    assert client.post('/api/admin/business-relationships').status_code==405


def test_api_uses_existing_admin_page_boundary():
    from app import page_permissions as p
    assert p.perm_for_path('/api/admin/business-relationships')==p.ADMIN_ONLY


def test_service_has_no_business_execution_imports_or_db_operations():
    source=Path(s.__file__).read_text('utf-8')
    assert 'from app.services import business_relationship_catalog' in source
    for forbidden in ('SessionLocal(', 'get_db(', 'db.commit(', 'sync_if_enabled(', 'send_image(', 'requests.', 'subprocess.'):
        assert forbidden not in source


def test_any_model_field_scanner_returns_candidates_not_proof():
    result=s.field_references('OrderDetail','qty')
    assert result['total']>0 and not result['errors']
    assert any('order_cost_service.py' in r['path'] for r in result['candidates'])
    assert '不是已验证' in result['notice']


@pytest.mark.parametrize('model,field',[('../secret','qty'),('OrderDetail','not_a_field')])
def test_unknown_field_cannot_read_arbitrary_files(model,field):
    with pytest.raises(ValueError):s.field_references(model,field)
