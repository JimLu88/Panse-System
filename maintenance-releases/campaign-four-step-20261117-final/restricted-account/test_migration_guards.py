import json
import pytest
import install_restricted_runtime as m

def fixture(tmp_path,monkeypatch):
    sid='S-1-5-21-1-2-3-1009'
    (tmp_path/'preparation-receipt.json').write_text(json.dumps({'sid':sid}))
    monkeypatch.setattr(m,'STAGE',tmp_path)
    monkeypatch.setattr(m,'MUTATING',False)
    monkeypatch.setattr(m.ctypes.windll.shell32,'IsUserAnAdmin',lambda:True)
    monkeypatch.setattr(m.win32security,'LookupAccountName',lambda *a:(m.win32security.ConvertStringSidToSid(sid),'test',1))
    monkeypatch.setattr(m.win32net,'NetUserGetInfo',lambda *a:{'flags':0})
    monkeypatch.setattr(m,'disabled',lambda:pytest.fail('must not change enabled account'))
    monkeypatch.setattr(m.sys,'argv',['installer','--apply'])

def test_active_identity_is_not_disabled_or_reset(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='active_account_requires'):m.main()
    assert m.MUTATING is False

def test_completed_migration_is_not_replayed(tmp_path,monkeypatch):
    fixture(tmp_path,monkeypatch)
    (tmp_path/'migration-receipt.json').write_text('{}')
    m.main();assert m.MUTATING is False

def test_dry_run_never_enters_administrator_or_account_mutation(monkeypatch):
    monkeypatch.setattr(m.sys,'argv',['installer'])
    monkeypatch.setattr(m.ctypes.windll.shell32,'IsUserAnAdmin',lambda:pytest.fail('dry-run must not reach admin check'))
    m.main();assert m.MUTATING is False
