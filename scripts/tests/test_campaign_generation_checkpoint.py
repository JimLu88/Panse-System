"""Only proven local generation failures can reuse an original action."""
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_generation_checkpoint as checkpoint
from campaign_continuous_policy import fingerprint
import campaign_failure_remediation


def make_action(tmp_path,code,*,context=False,overlay=False,owner=None):
    segment=tmp_path/'runs'/'request'/'segments'/'segment'
    folder=segment/'actions'/('a'*64)
    folder.mkdir(parents=True)
    payload={'items':['100'],'round':3}
    (folder/'request.json').write_text(json.dumps({'step':'generate','payload':payload}),encoding='utf-8')
    if context:(folder/'files-input-context.json').write_text('{}',encoding='utf-8')
    if overlay:(folder/'official-mapping-overlay.json').write_text('{}',encoding='utf-8')
    (segment/'resolved-snapshot.json').write_text('{}',encoding='utf-8')
    with sqlite3.connect(segment.parent.parent/'controller.sqlite3') as db:
        db.execute('CREATE TABLE continuous_campaign_runs(id TEXT,owner TEXT)')
        db.execute('CREATE TABLE continuous_campaign_actions(id TEXT,run_id TEXT,step TEXT,'
                   'status TEXT,payload_sha TEXT)')
        db.execute('CREATE TABLE continuous_campaign_diagnostics(event_id INTEGER PRIMARY KEY,'
                   'action_id TEXT,code TEXT)')
        db.execute('INSERT INTO continuous_campaign_runs VALUES(?,?)',('run',owner))
        db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?)',
                   (folder.name,'run','generate','interrupted_read',fingerprint(payload)))
        db.execute('INSERT INTO continuous_campaign_diagnostics(action_id,code) VALUES(?,?)',
                   (folder.name,code))
    return folder


def test_document_level_alias_proof_is_required_before_pre_context_retry(tmp_path,monkeypatch):
    folder=make_action(tmp_path,'backup_alias_provenance_missing',overlay=True)
    checked=[]
    monkeypatch.setattr(campaign_failure_remediation,'verified_code_aliases',
                        lambda snapshot:checked.append(snapshot))
    assert checkpoint.can_rebuild(folder) is True and checked==[{}]
    (folder/'files').mkdir()
    assert checkpoint.can_rebuild(folder) is False


@pytest.mark.parametrize('code,context,overlay',[
    ('backup_alias_provenance_missing',False,False),
    ('backup_alias_provenance_missing',True,True),
    ('other_failure',False,True),
    ('offer_availability_readback_stale',False,False),
])
def test_other_or_incomplete_local_checkpoint_rejected(tmp_path,code,context,overlay):
    folder=make_action(tmp_path,code,context=context,overlay=overlay)
    assert checkpoint.can_rebuild(folder) is False


def test_owned_checkpoint_requires_controller_owned_mode(tmp_path,monkeypatch):
    folder=make_action(tmp_path,'backup_alias_provenance_missing',overlay=True,owner='active')
    monkeypatch.setattr(campaign_failure_remediation,'verified_code_aliases',lambda snapshot:None)
    assert checkpoint.can_rebuild(folder) is False
    assert checkpoint.can_rebuild(folder,allow_owned=True) is True
