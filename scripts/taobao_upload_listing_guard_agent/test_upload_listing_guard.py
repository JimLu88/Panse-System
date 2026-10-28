import hashlib
import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from app.engine import upload_listing_guard as guard


@pytest.fixture(autouse=True)
def isolate_receipts(tmp_path,monkeypatch):
    monkeypatch.setattr(guard,'RECEIPTS',tmp_path/'receipts')


def response(data, ok=True):
    return SimpleNamespace(returncode=0 if ok else 2, stdout=json.dumps(
        {'ok':ok,'sha256':hashlib.sha256(data).hexdigest(),'blocked':[] if ok else ['offsale']}))


def test_payload_is_exact_checked_bytes_even_if_file_changes(tmp_path,monkeypatch):
    p=tmp_path/'old.xlsx';p.write_bytes(b'original')
    def run(*a,**kw):
        p.write_bytes(b'replaced-after-check')
        return response(b'original')
    monkeypatch.setattr(guard.subprocess,'run',run)
    assert guard.guarded_payload(p)['buffer']==b'original'


@pytest.mark.parametrize('result',[SimpleNamespace(returncode=2,stdout='{}'),
    SimpleNamespace(returncode=0,stdout='not-json'),response(b'wrong-file'),response(b'file',False)])
def test_no_fallback_when_erp_unavailable_or_denies(monkeypatch,result):
    monkeypatch.setattr(guard.subprocess,'run',lambda *a,**k:result)
    with pytest.raises(guard.UploadListingBlocked): guard.check_bytes(b'file')


def test_old_success_is_never_cached(monkeypatch):
    answers=iter([response(b'file'),response(b'file',False)])
    monkeypatch.setattr(guard.subprocess,'run',lambda *a,**k:next(answers))
    assert guard.check_bytes(b'file')['ok']
    with pytest.raises(guard.UploadListingBlocked): guard.check_bytes(b'file')


def test_wrong_expected_sha_never_calls_erp(tmp_path,monkeypatch):
    p=tmp_path/'old.xlsx';p.write_bytes(b'changed')
    def unexpected(*a,**kw): raise AssertionError('must not contact ERP')
    monkeypatch.setattr(guard.subprocess,'run',unexpected)
    with pytest.raises(guard.UploadListingBlocked): guard.guarded_payload(p,'old-hash')


def test_atomic_transfer_uses_same_gate(tmp_path,monkeypatch):
    from app.engine.campaign_page_io import workbook_payload
    p=tmp_path/'atomic.xlsx';p.write_bytes(b'file')
    monkeypatch.setattr(guard.subprocess,'run',lambda *a,**k:response(b'file',False))
    with pytest.raises(guard.UploadListingBlocked):
        workbook_payload(p,hashlib.sha256(b'file').hexdigest())


def test_all_legacy_workbook_attachment_sites_are_guarded():
    import ast
    source=Path(__file__).parents[1]/'app/engine/uploader.py'
    tree=ast.parse(source.read_text(encoding='utf-8'))
    sites=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('set_files','set_input_files'):
            if node.args and 'xlsx_path' in ast.unparse(node.args[0]):
                assert 'listing_checked_payload' in ast.unparse(node.args[0])
                sites.append(node)
    assert len(sites)==10
