import copy
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.services import business_relationship_service as service
from app.services.business_relationship_index import source_index, dependency_review


@pytest.fixture
def index_root(tmp_path):
    app = tmp_path / 'backend' / 'app'
    for p, text in {
        'backend/app/models/order.py': 'class Order:\n    qty: int\n',
        'backend/app/services/reader.py': 'from app.models.order import Order\ndef read(): pass\n',
        'backend/app/services/deep/nested.py': 'from ..reader import read\n',
        'backend/app/cli/task.py': 'from app.services.reader import read\n',
        'scripts/campaign.py': 'from app.services.reader import read\nSECRET="never-export-this"\n',
        'frontend/src/A.tsx': 'import { B } from "./nested/B";\nexport function A() {}',
        'frontend/src/nested/B.ts': 'export type B = string;',
        'backend/app/services/broken.py': 'def !!!',
    }.items():
        path = tmp_path / p; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text, encoding='utf-8')
    return app


def test_recursive_complete_scopes_and_no_source_values(index_root):
    result = source_index(index_root)
    assert len(result['files']) == 8
    assert not result['missing_scopes']
    assert result['errors'] == [{'path': 'backend/app/services/broken.py', 'type': 'SyntaxError'}]
    assert 'never-export-this' not in json.dumps(result)
    assert all(not f['semantic_reviewed'] for f in result['files'])
    assert any(e['from'] == 'frontend/src/A.tsx' and e['to'] == 'frontend/src/nested/B.ts' for e in result['dependencies'])
    assert any(e['from'] == 'backend/app/services/deep/nested.py' and e['to'] == 'backend/app/services/reader.py' for e in result['dependencies'])


def test_recursive_reverse_dependencies_and_depth(index_root):
    index = source_index(index_root)
    r = dependency_review(['backend/app/models/order.py'], index, depth=1)
    assert r['truncated']
    r = dependency_review(['backend/app/models/order.py'], index, depth=3)
    assert not r['truncated']
    assert {f['path'] for f in r['files']} >= {'scripts/campaign.py', 'backend/app/cli/task.py'}


@pytest.mark.parametrize('paths', [[], ['../secret'], ['/etc/passwd'], ['C:/secret'], [''], ['x'*301], ['a']*101])
def test_invalid_paths_never_read_files(index_root, paths):
    with pytest.raises(ValueError): dependency_review(paths, source_index(index_root))


def test_unknown_path_explicit(index_root):
    r = dependency_review(['frontend/src/missing.ts'], source_index(index_root))
    assert r['unknown_paths'] == ['frontend/src/missing.ts'] and not r['files']


def test_missing_scopes_explicit(tmp_path):
    result = source_index(tmp_path / 'backend' / 'app')
    assert result['missing_scopes'] == ['backend/app', 'scripts', 'frontend/src']


def test_published_index_restores_only_absent_scopes(index_root, tmp_path):
    package = source_index(index_root)
    deployed = tmp_path / 'deployed' / 'backend' / 'app'
    assets = deployed / 'assets'; assets.mkdir(parents=True)
    (assets / 'business_relationship_index.json').write_text(json.dumps(package))
    result = source_index(deployed)
    assert not result['missing_scopes']
    assert result['packaged_scopes'] == ['scripts', 'frontend/src']
    assert all(f['origin'] == 'published_source_snapshot' for f in result['files'])
    assert not any(f['path'].startswith('backend/app/models') for f in result['files'])
    assert result['errors']  # A build with parse failures cannot become a green live index.


def test_bad_published_index_preserves_missing_scopes(tmp_path):
    root = tmp_path / 'backend' / 'app'; (root / 'assets').mkdir(parents=True)
    (root / 'assets' / 'business_relationship_index.json').write_text('{"schema":"wrong"}')
    result = source_index(root)
    assert result['errors'] and result['missing_scopes']


def test_cycles_no_repetition(index_root):
    index = source_index(index_root)
    index['dependencies'].append({'from': 'backend/app/models/order.py', 'to': 'backend/app/services/reader.py', 'line': 1})
    r = dependency_review(['backend/app/models/order.py'], index)
    assert len(r['files']) == len({f['path'] for f in r['files']})


def test_flow_conditions_not_invented():
    r = service.flow_review('order')
    assert r['boundary_relations']
    assert all(e in service.snapshot()['edges'] for e in r['relations'])
    with pytest.raises(ValueError): service.flow_review('bad')


def test_catalog_validation_rejects_invalid_metadata(monkeypatch):
    edges = copy.deepcopy(service.catalog.EDGES)
    edges[0]['kind'] = 'automatically_write'
    edges[0]['evidence'] = 'guessed'
    monkeypatch.setattr(service.catalog, 'EDGES', edges)
    assert any(e.startswith('invalid_kind') for e in service.validate())
    assert any(e.startswith('invalid_evidence') for e in service.validate())


def test_removed_locked_source_is_not_silent(tmp_path):
    lock = tmp_path / 'lock.json'
    lock.write_text(json.dumps({'sources': {**service.fingerprints(), 'backend/app/services/removed.py': 'old'}}))
    assert service.snapshot(lock=lock)['retired_sources'] == ['backend/app/services/removed.py']


@pytest.mark.parametrize('role,status', [('admin', 200), ('operator', 200), ('viewer', 403)])
@pytest.mark.parametrize('route', ['source-index', 'changes?paths=backend/app/models/order.py'])
def test_new_routes_readonly_and_role_guard(role, status, route, monkeypatch):
    from app.api import admin
    from app.dependencies import get_current_user
    from app.services import business_relationship_index as index
    monkeypatch.setattr(index, 'source_index', lambda root: {'files': []})
    monkeypatch.setattr(service, 'review_changes', lambda *a, **k: {'read_only': True})
    app = FastAPI(); app.include_router(admin.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role=role)
    client = TestClient(app)
    assert client.get('/api/admin/business-relationships/' + route).status_code == status
    assert client.post('/api/admin/business-relationships/' + route).status_code == 405


def test_change_review_retains_unmapped_and_protection():
    r = service.review_changes(['backend/app/services/factory_sheet.py', 'nonexistent.py'])
    assert 'nonexistent.py' in r['business_review']['unmapped_changes']
    assert r['checklist'] and not r['runtime_verified']
    assert any(e['kind'] == 'protect' for e in r['checklist'])
