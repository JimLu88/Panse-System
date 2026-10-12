import asyncio
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from campaign_continuous_flow import Store
from campaign_continuous_policy import fingerprint


def test_resume_local_report_only(tmp_path,monkeypatch):
    rule='frozen';request={'rule_sha':rule};rid=fingerprint(request)
    root=tmp_path/'runs'/rid;folder=root/'segments/s/actions/a';folder.mkdir(parents=True)
    (tmp_path/'requests').mkdir();(tmp_path/'requests'/f'{rid}.json').write_text(json.dumps(request))
    p=dict(batch='b',failed_phase='discount',items=['c'])
    (folder/'request.json').write_text(json.dumps(dict(step='report',payload=p)))
    segment=folder.parent.parent
    for name,value in [('terminals/discount-b.json',dict(batch='b',bundle_id='x')),
                       ('reports/b.json',dict(batch='b',bundle_id='x',errors=[dict(item='a')]))]:
        f=segment/name;f.parent.mkdir(exist_ok=True);f.write_text(json.dumps(value))
    state=dict(blocker=dict(step='report',reason='continuous_evidence_is_immutable'))
    ledger=Store(root/'controller.sqlite3')
    ledger.db.execute('INSERT INTO continuous_campaign_runs VALUES(?,?,?,?,?)',('r','i',rule,json.dumps(state),None))
    ledger.db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?,?)',('a','r','report',fingerprint(p),'interrupted_read',None))
    ledger.db.execute('INSERT INTO continuous_campaign_diagnostics(action_id,step,kind,code,elapsed_ms) VALUES(?,?,?,?,?)',('a','report','ValueError','continuous_evidence_is_immutable',1))
    ledger.close()
    worker_db=sqlite3.connect(':memory:',isolation_level=None)
    worker_db.execute('CREATE TABLE continuous_jobs(id,state,result)')
    worker_db.execute('INSERT INTO continuous_jobs VALUES(?,?,?)',(rid,'blocked',None))
    module=ModuleType('test_report_runtime.campaign_continuous_worker')
    module.ERP_ROOT=tmp_path;module.RULE_SHA=rule;module.fingerprint=fingerprint;module.source_hashes=lambda:{'version':'test'}
    monkeypatch.setitem(sys.modules,module.__name__,module)
    source=Path(r'D:/AI/畔色ERP系统/工作树/Web-Agent-report-scope-20261012/app/engine/campaign_report_scope_recovery.py')
    spec=importlib.util.spec_from_file_location('test_report_runtime.campaign_report_scope_recovery',source)
    recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)
    calls=[]
    async def execute(rid,log_name):calls.append((rid,log_name))
    worker=SimpleNamespace(root=tmp_path,tasks={},db=worker_db,execute=execute)
    worker.status=lambda rid:dict(state=worker_db.execute('SELECT state FROM continuous_jobs WHERE id=?',(rid,)).fetchone()[0])
    async def run():
        assert recovery.resume(worker,rid)['state']=='running'
        await worker.tasks[rid]
    asyncio.run(run())
    assert len(calls)==1
    db=sqlite3.connect(root/'controller.sqlite3')
    assert db.execute('SELECT count(*) FROM continuous_campaign_actions').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM continuous_campaign_diagnostics').fetchone()[0]==1
    assert len(list((root/'report-scope-recovery').glob('*.json')))==1
    assert (segment/'reports/b.json').exists()
