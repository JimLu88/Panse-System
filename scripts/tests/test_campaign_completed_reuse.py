import json
from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
import campaign_discount_availability as mod
from campaign_entry_authority import file_sha


@pytest.mark.parametrize('fault',[None,'new_sku','amount','failed_sku','campaign','window','time_hash','unbound_path'])
def test_only_verified_existing_scope_can_continue(tmp_path,monkeypatch,fault):
    import campaign_partial_discount
    monkeypatch.setattr(mod,'CONTINUOUS_RUNS',tmp_path/'runs')
    timing=tmp_path/'runs'/('a'*64)/'time-request.json'
    timing.parent.mkdir(parents=True);timing.write_text('{}')
    proof=tmp_path/'partial.json';proof.write_text(json.dumps({'campaign':'legacy/itemApply/1'}))
    binding={'request_path':str(timing),'request_file_sha256':file_sha(timing),
             'segment':{'campaign':'legacy/itemApply/1','price_window':{'start':'s','end':'e'}}}
    rows=[{'item':'1','sku':'2','deduct':'10'},{'item':'1','sku':'3','deduct':'20'}]
    offer={'offer_id':'bundle:new','platform_offer_id':'123','start':'s','end':'e','rows':rows,
           'partial_terminal_evidence':{'path':str(proof),'sha256':file_sha(proof)},'time_binding':binding}
    checked=[]
    def verified(o,p):checked.append(True);return {('1','2')}
    monkeypatch.setattr(campaign_partial_discount,'verified_rows',verified)
    planned=[deepcopy(rows[0])];campaign='legacy/itemApply/1';end='e'
    if fault=='new_sku':planned[0]['sku']='4'
    if fault=='failed_sku':planned=[rows[1]]
    if fault=='amount':planned[0]['deduct']='11'
    if fault=='campaign':campaign='legacy/itemApply/2'
    if fault=='window':end='different'
    if fault=='time_hash':binding['request_file_sha256']='bad'
    if fault=='unbound_path':monkeypatch.setattr(mod,'CONTINUOUS_RUNS',tmp_path/'other')
    if fault=='time_hash':
        with pytest.raises(ValueError,match='time_binding_changed'):
            mod.completed_reuse_controller([offer],planned,campaign,'s',end)
    else:
        assert mod.completed_reuse_controller([offer],planned,campaign,'s',end)==('a'*64 if fault is None else None)
    if fault is None:assert checked


def test_unrelated_offer_does_not_consume_expiring_observation(tmp_path,monkeypatch):
    ref=tmp_path/'availability.json';ref.write_text(json.dumps({'scope':{'campaign':'legacy/itemApply/1','start':'s','end':'e'},'offer_ids':['old']}))
    offer={'offer_id':'new','start':'old_s','end':'old_e','availability_refs':[{'path':str(ref),'sha256':file_sha(ref)}]}
    monkeypatch.setattr(mod,'verified_scope',lambda *a,**k:pytest.fail('unrelated freshness check'))
    assert mod.inactive_for_window(offer,'legacy/itemApply/1','s','e') is False


@pytest.mark.parametrize('fault',[None,'write_step','unknown','other_error','has_files','changed_payload','owner'])
def test_local_generation_checkpoint_only_before_output(tmp_path,fault):
    from campaign_generation_checkpoint import can_rebuild
    from campaign_continuous_flow import Store
    from campaign_continuous_policy import fingerprint
    folder=tmp_path/'segments'/'segment'/'actions'/'action';folder.mkdir(parents=True)
    payload={'items':['1']};saved={'step':'generate','payload':payload}
    (folder/'request.json').write_text(json.dumps(saved));(folder/'files-input-context.json').write_text('{}')
    store=Store(tmp_path/'controller.sqlite3')
    # Real schema, local-only fixtures.
    rid=store.start('b'*64,'rule')
    store.db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?,NULL)',
        ('action',rid,'signup' if fault=='write_step' else 'generate',
         'bad' if fault=='changed_payload' else fingerprint(payload),'unknown' if fault=='unknown' else 'interrupted_read'))
    store.db.execute('INSERT INTO continuous_campaign_diagnostics VALUES(NULL,?,?,?,?,?)',
        ('action','generate','ValueError','different' if fault=='other_error' else 'offer_availability_readback_stale',0))
    if fault=='owner':store.db.execute('UPDATE continuous_campaign_runs SET owner=? WHERE id=?',('live',rid))
    if fault=='has_files':(folder/'files').mkdir()
    store.close()
    assert can_rebuild(folder)==(fault is None)
