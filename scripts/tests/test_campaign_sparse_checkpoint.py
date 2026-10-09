import sys
import json
import hashlib
import sqlite3
import importlib.util
from pathlib import Path
import pytest

sys.path.insert(0, 'D:/AI/畔色ERP系统/ERP程序/scripts')
spec=importlib.util.spec_from_file_location('sparse_checkpoint_candidate',Path(__file__).parents[1]/'campaign_generation_checkpoint.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

@pytest.mark.parametrize('blocker',['none','claim','result','files','changed_input','owned'])
def test_preoutput_only(tmp_path,blocker):
    root=tmp_path/'run'; folder=root/'segments'/'segment'/'actions'/'action';folder.mkdir(parents=True)
    payload={'items':['12345678901']}
    (folder/'request.json').write_text(json.dumps({'step':'generate','payload':payload}))
    context={}
    for name in ('template','snapshot','time_request'):
        p=root/name;p.write_bytes(b'original')
        context[name]={'path':str(p),'sha256':hashlib.sha256(b'original').hexdigest()}
    (folder/'files-input-context.json').write_text(json.dumps(context))
    with sqlite3.connect(root/'controller.sqlite3') as db:
        db.executescript('CREATE TABLE continuous_campaign_runs(id,owner);CREATE TABLE continuous_campaign_actions(id,run_id,step,status,payload_sha);CREATE TABLE continuous_campaign_diagnostics(event_id,action_id,code);')
        db.execute('INSERT INTO continuous_campaign_runs VALUES(?,?)',('run','owner' if blocker=='owned' else None))
        db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?)',('action','run','generate','interrupted_read',c.fingerprint(payload)))
        db.execute('INSERT INTO continuous_campaign_diagnostics VALUES(?,?,?)',(1,'action','official_text_cell_missing'))
    if blocker in ('claim','result'):(folder/(blocker+'.json')).write_text('{}')
    if blocker=='files':(folder/'files').mkdir()
    if blocker=='changed_input':(root/'template').write_bytes(b'changed')
    assert c.can_rebuild(folder) is (blocker=='none')
