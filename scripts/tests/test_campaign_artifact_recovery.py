"""Real Store -> transport -> generator -> claim -> receipt, offline only.

Only the platform and the existing external discount snapshot are simulated.
Every database, template, export, and receipt lives under pytest tmp_path.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_artifact_recovery import ensure_generated
from campaign_continuous_flow import Store, run, Blocked
from campaign_continuous_policy import RULE_SHA, ENTRY, fingerprint
from campaign_continuous_transport import CampaignTransport, persist
from campaign_entry_authority import Authority, file_sha, load
from campaign_generate_current_files import _generate
from campaign_price_snapshot import build_snapshot
from campaign_submission_gate import consume_claim
from test_campaign_current_rate import rate_fixture

ITEM='917179577721'
START='2026-10-01 00:00:00'
END='2026-10-07 23:59:59'


@pytest.fixture
def case(tmp_path):
    manifest=persist(tmp_path/'sources.json',{'sources':[]})
    authority=Authority(tmp_path/'authority.sqlite',manifest)
    rows=[dict(item=ITEM,sku=str(6241018727157+n),alt=[],code='NORMAL'+str(n),
        product_code='PRODUCT',daily='100.00',medium_target='75.00',big_target='70.00',
        custom=False) for n in range(4)]
    snapshot=Path(persist(tmp_path/'snapshot.json',build_snapshot(rows)))
    template=tmp_path/'template.xlsx';template.write_bytes(rate_fixture('12%'))
    offer=dict(offer_id='12345',start=START,end=END,items=[dict(item=ITEM,status='success')],
        rows=[dict(item=ITEM,sku=r['sku'],deduct='18.00') for r in rows])
    authority.discount_offers=lambda:[deepcopy(offer)]
    args=SimpleNamespace(output_dir=tmp_path/'files',snapshot=snapshot,activity_template=template,
        start=START,end=END,campaign_key='1/2/3',official_rate='.12',target='big',
        signup_items=ITEM,discount_items=ITEM,custom_basis_receipt=[],custom_corrections=None,
        continuous_rule_sha=RULE_SHA)
    yield SimpleNamespace(a=authority,args=args,rows=rows,offer=offer,root=tmp_path)
    authority.close()


def test_reuses_real_bundle_bytes_without_second_generation(case):
    first=ensure_generated(case.args,case.a)
    assert first['issues']==[] and first['discount_rows']==[]
    before=[(p,p.read_bytes(),p.stat().st_mtime_ns) for p in case.args.output_dir.iterdir()]
    forbidden=Mock(side_effect=AssertionError('generation repeated'))
    assert ensure_generated(case.args,case.a,generator=forbidden)==first
    assert [(p,p.read_bytes(),p.stat().st_mtime_ns) for p,_,_ in before]==before
    assert case.a.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==0


def test_legacy_complete_artifact_adopted_not_regenerated(case):
    first=_generate(case.args,case.a)
    assert ensure_generated(case.args,case.a,adopt_only=True)==first
    assert (case.root/'files-adoption.json').is_file()


@pytest.mark.parametrize('field,value',[
    ('start','2026-10-02 00:00:00'),('end','2026-10-08 23:59:59'),
    ('campaign_key','1/2/4'),('official_rate','.15'),('target','medium'),
    ('signup_items','2'),('discount_items','2'),('continuous_rule_sha','changed')])
def test_changed_business_context_not_reused(case,field,value):
    ensure_generated(case.args,case.a)
    setattr(case.args,field,value)
    with pytest.raises(ValueError,match='context_changed'):ensure_generated(case.args,case.a)
    assert case.a.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==0


@pytest.mark.parametrize('state',['unknown','success'])
@pytest.mark.parametrize('damage',['missing_file','changed_file','missing_receipt'])
def test_local_damage_never_releases_effect_claim(case,state,damage):
    result=ensure_generated(case.args,case.a)
    bid=result['entry_bundle_id'];cid=case.a.claim(bid,'signup')
    # Synthetic ledger states only; not evidence of a real platform result.
    if state=='success':case.a.db.execute('UPDATE attempts SET status=?',('success',))
    before=list(map(tuple,case.a.db.execute('SELECT * FROM attempts')))
    path=Path(result['files'][0]['path'])
    if damage=='missing_file':path.unlink()
    elif damage=='changed_file':path.write_bytes(b'broken')
    else:(case.args.output_dir/'receipt.json').unlink()
    with pytest.raises((ValueError,FileNotFoundError)):ensure_generated(case.args,case.a)
    assert list(map(tuple,case.a.db.execute('SELECT * FROM attempts')))==before
    with pytest.raises(ValueError,match='must_not_replay'):case.a.claim(bid,'signup')


def test_changed_offer_blocks_cached_generation(case):
    ensure_generated(case.args,case.a)
    case.offer['rows'][0]['deduct']='20.01'
    with pytest.raises(ValueError,match='reuse_invalid'):ensure_generated(case.args,case.a)


class OfflineTransport(CampaignTransport):
    def __init__(self,case,*,crash=False,unknown=False,fail=False):
        super().__init__(None,case.a,root=case.root/'execution',
            request={'target':'big'},artifact_roots=[case.root])
        self.case=case;self.crash=crash;self.unknown=unknown;self.fail=fail;self.operations=[]
        persist(self.root/'resolved-snapshot.json',load(case.args.snapshot))

    def step_scope(self,aid,payload,folder):
        version=self.authority.resolve_snapshot(load(self.case.args.snapshot))['resolved_price_version_sha256']
        proof=persist(folder/'prior.json',{'items':{}})
        return dict(erp_sellable=[ITEM],platform_rows=[dict(item=ITEM,on_sale=True)],
            complete=True,observed_item_count=1,prior_outcomes={},prior_outcomes_evidence=proof,
            price_version=version)

    def step_template(self,aid,payload,folder):
        return dict(path=str(self.case.args.activity_template),sha256=file_sha(self.case.args.activity_template),
            registered_items=[])

    def step_generate(self,*args):
        result=super().step_generate(*args)
        if self.crash:
            self.crash=False
            raise OSError('simulated_generation_checkpoint_gap')
        return result

    def job(self,step,payload,folder):
        """The only fake boundary: serialize and bind a synthetic platform reply."""
        payload=json.loads(json.dumps(payload));self.operations.append(step)
        if step=='discount_readback':
            aid=payload['read_request_id'];jid=fingerprint([step,'test-shop',aid])
            raw=dict(state='readback',shop_name='test-shop',read_request_id=aid,platform_write=False,
                price_window=payload['price_window'],rows=[dict(item=ITEM,
                window=dict(offer_id='12345',**payload['price_window']),
                values={r['sku']:r['deduct'] for r in self.case.offer['rows']})])
        elif step=='signup':
            jid=fingerprint(['offline_signup',payload['claim_id']])
            claim=consume_claim(self.authority,payload['claim_id'],jid)
            if self.unknown:raise TimeoutError('simulated_post_submit_timeout')
            raw=dict(state='terminal_processing_failed' if self.fail else 'terminal',batch='991',claim=claim,
                record={'历史记录ID':'991','执行操作':'商品批量导入','数据来源':'活动报名.xlsx',
                        '执行状态':'处理失败' if self.fail else '成功','执行结果':'模拟文件错误'},
                counts=dict(total=1,success=0 if self.fail else 1,failed=1 if self.fail else 0,pending=0))
        else:raise AssertionError('unexpected or repeated external operation: '+step)
        source=persist(folder/(step+'-offline-response.json'),raw)
        return json.loads(json.dumps(dict(operation=step,state='finished',job_id=jid,
            result=dict(raw,evidence_path=source))))


PAGE=dict(entry=ENTRY,url='https://myseller.taobao.com/activity?id=1',shop_id='test-shop',
    campaign_id='1',phase_id='2',sign_record_id='3',title='offline fixture',phase_title='fixture',
    start=START,end=END,official_rate='.12',page_evidence='synthetic offline page')


@pytest.mark.parametrize('custom',[False,True])
def test_full_controller_restarts_from_saved_artifact_to_official_terminal(case,custom):
    if custom:
        case.args.snapshot.write_text(json.dumps(build_snapshot([dict(r,custom=True) for r in case.rows])),encoding='utf-8')
        case.a.discount_offers=lambda:[]
    transport=OfflineTransport(case,crash=True)
    store=Store(case.root/'flow.sqlite')
    runargs=dict(expected_shop='test-shop',observed_links=[PAGE['url']])
    first=run(store,transport,PAGE,**runargs)
    assert first['status']=='blocked' and not transport.operations
    assert first['blocker']['reason']=='simulated_generation_checkpoint_gap'
    store.close()
    store=Store(case.root/'flow.sqlite')
    try:
        result=run(store,transport,PAGE,**runargs)
        assert result['all_signed_up'] is True, result
        assert transport.operations==(['signup'] if custom else ['discount_readback','signup'])
        assert case.a.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0]==1
        assert case.a.db.execute('SELECT status FROM attempts').fetchone()[0]=='success'
        assert store.db.execute('SELECT COUNT(*) FROM continuous_campaign_recoveries').fetchone()[0]==1
        before=list(transport.operations)
        assert run(store,transport,PAGE,**runargs)['all_signed_up'] is True
        assert transport.operations==before
    finally:store.close()


def test_post_submit_unknown_restart_never_calls_platform_again(case):
    transport=OfflineTransport(case,unknown=True);store=Store(case.root/'flow.sqlite')
    try:
        first=run(store,transport,PAGE,expected_shop='test-shop',observed_links=[PAGE['url']])
        assert first['status']=='blocked' and first['blocker']['step']=='signup'
        before=list(transport.operations)
        second=run(store,transport,PAGE,expected_shop='test-shop',observed_links=[PAGE['url']])
        assert second['status']=='blocked' and transport.operations==before
        assert case.a.db.execute('SELECT status FROM attempts').fetchone()[0]=='unknown'
    finally:store.close()


def test_terminal_processing_failure_collected_not_retried(case):
    transport=OfflineTransport(case,fail=True);store=Store(case.root/'flow.sqlite')
    try:
        result=run(store,transport,PAGE,expected_shop='test-shop',observed_links=[PAGE['url']])
        assert result['status']=='complete' and result['all_signed_up'] is False, result
        assert ITEM in result['exceptions']
        assert transport.operations==['discount_readback','signup']
    finally:store.close()


def test_release_guard_blocks_mixed_code_without_changing_business_identity(tmp_path):
    from campaign_execution_release import ReleaseGuard
    source=tmp_path/'scripts/campaign_example.py';source.parent.mkdir();source.write_text('VERSION=1')
    guard=ReleaseGuard(tmp_path);guard.verify()
    before=guard.release_id
    source.write_text('VERSION=2')
    with pytest.raises(ValueError,match='release_changed'):guard.verify()
    assert ReleaseGuard(tmp_path).release_id!=before
    assert guard.evidence()['business_identity_changed'] is False


def test_finished_original_signup_receipt_reconciles_execution_layout_without_replay(case):
    from campaign_submission_gate import verify_claim
    from campaign_continuous_recovery import recover_finished_signup
    from campaign_owned_execution import checkpoint
    transport=OfflineTransport(case,unknown=True);store=Store(case.root/'controller.sqlite3')
    try:
        first=run(store,transport,PAGE,expected_shop='test-shop',observed_links=[PAGE['url']])
        assert first['blocker']['step']=='signup'
        action=store.db.execute("SELECT id FROM continuous_campaign_actions WHERE step='signup'").fetchone()[0]
        folder=case.root/'execution/actions'/action
        cid=load(folder/'claim.json')['claim_id'];jid=fingerprint(['offline_signup',cid])
        # Production job ID is canonical signup+claim (the offline emulator
        # above uses a separate identity); keep this fixture's ledger coherent.
        original_jid=fingerprint(['signup',cid])
        case.a.db.execute('UPDATE claim_transports SET job_id=? WHERE claim_id=?',(original_jid,cid))
        persist(folder/'signup-job.json',dict(step='signup',job_id=original_jid))
        claim=verify_claim(case.a,cid,dispatched_job=original_jid)
        raw=dict(state='terminal',batch='992',claim=claim,
            record={'历史记录ID':'992','执行操作':'商品批量导入','数据来源':'活动报名.xlsx','执行状态':'成功'},
            counts=dict(total=1,success=1,failed=0,pending=0))
        source=persist(folder/'finished-original-response.json',raw)
        job=dict(operation='signup',state='finished',job_id=original_jid,result=dict(raw,evidence_path=source))
        edge=Mock();edge.status.return_value=job
        assert checkpoint(case.root)['action_id']==action
        result=recover_finished_signup(case.root,case.a,edge)
        assert result['platform_write'] is False
        before=list(transport.operations)
        final=run(store,transport,PAGE,expected_shop='test-shop',observed_links=[PAGE['url']])
        assert final['all_signed_up'] and transport.operations==before
        edge.submit.assert_not_called()
    finally:store.close()
