import copy
import json
import sqlite3
from pathlib import Path

import pytest
from campaign_continuous_policy import fingerprint
from campaign_continuous_recovery import phase_observation
from campaign_scope_checkpoint import discount_observation_collision
from test_campaign_scope_maintenance import write


def observations():
    before={'job_id':'a'*64,'state':'finished','operation':'discount','result':{
        'state':'official_import_terminal','claim':{'claim_id':'b'*32},'success':270,'failed':101,'start':'start','end':'end'}}
    after=copy.deepcopy(before)
    after['result'].update(state='verified_partial_offer_terminal',feedback={'path':'report'},platform_write=False,
        recovery_binding={'source_job_id':before['job_id'],'original_result_sha':fingerprint(before['result'])})
    return before,after


@pytest.mark.parametrize('case',[None,'counts','claim','source_sha','phase','window'])
def test_observation_enrichment_keeps_original_and_is_idempotent(tmp_path,case):
    before,after=observations();path=tmp_path/'reconciled-discount-observation.json';write(path,before);raw=path.read_bytes()
    phase='discount'
    if case=='counts':after['result']['success']+=1
    if case=='claim':after['result']['claim']['claim_id']='other'
    if case=='source_sha':after['result']['recovery_binding']['original_result_sha']='changed'
    if case=='phase':phase='signup';write(tmp_path/'reconciled-signup-observation.json',before)
    if case=='window':after['result']['end']='changed'
    if case:
        with pytest.raises(ValueError):phase_observation(tmp_path,phase,after)
        assert not (tmp_path/'reconciled-discount-observations').exists()
    else:
        result=phase_observation(tmp_path,phase,after)
        assert result!=str(path) and json.loads(Path(result).read_text())==after
        assert phase_observation(tmp_path,phase,after)==result
    assert path.read_bytes()==raw


@pytest.mark.parametrize('case',[None,'log','result','authority','completed','budget','unknown_reason'])
def test_only_proven_pre_reconciliation_collision_can_resume(tmp_path,case):
    before,after=observations();jid=before['job_id'];rid='c'*64;folder=tmp_path/'action';folder.mkdir()
    write(folder/'reconciled-discount-observation.json',before)
    ref=write(tmp_path/'result.json',{'original':'unchanged'})
    write(tmp_path/('discount-terminal-recovery-'+jid+'.json'),dict(request_id=rid,job_id=jid,write_claims_released=False,
        previous_controller={'state':'blocked'},old_result_sha256=ref['sha256']))
    log=tmp_path/('process-discount-terminal-recovery-'+jid+'.log')
    log.write_text("in _recover_finished_phase\nreconciled-discount-observation\nin persist\nValueError: continuous_evidence_is_immutable\n")
    database=tmp_path/'authority.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE claim_transports(claim_id TEXT,transport TEXT,state TEXT,job_id TEXT)')
        db.execute('CREATE TABLE attempts(id TEXT,status TEXT)')
        db.execute('INSERT INTO claim_transports VALUES(?,?,?,?)',('b'*32,'dedicated_edge_v1','dispatched_unknown',jid))
        db.execute('INSERT INTO attempts VALUES(?,?)',('b'*32+':item','success' if case=='authority' else 'unknown'))
    controller=('unknown',json.dumps({'reason':'controller_did_not_produce_terminal_receipt','automatic_retry':False}))
    if case=='log':log.write_text('unrelated error')
    if case=='result':write(tmp_path/'result.json',{'changed':True})
    if case=='completed':write(folder/'reconciled-result.json',{})
    if case=='budget':write(tmp_path/'discount-observation-recovery.json',{})
    if case=='unknown_reason':controller=('unknown',json.dumps({'reason':'other'}))
    if case:
        with pytest.raises(ValueError):discount_observation_collision(tmp_path,rid,folder,jid,after['result'],controller,authority_db=database)
    else:
        proof,refs=discount_observation_collision(tmp_path,rid,folder,jid,after['result'],controller,authority_db=database)
        assert proof['authority_unchanged'] is True and len(refs)==4
