import hashlib
import json
import pytest


@pytest.mark.parametrize('fault', ['', 'old_day', 'old_id', 'response_rejected', 'claim', 'old_receipt'])
def test_quota_recovery_binds_old_failure_new_claim_and_same_day(monkeypatch, manifest, fault):
    from app.services import order_job_adoption as a
    root,path,receipt,args=manifest
    job=receipt['source_job_id']; failed='f'*32
    old=dict(receipt,status='error',order_attempt_id=failed,artifacts=receipt['artifacts'][:2])
    old_path=root/'order-runs'/(failed+'.json'); old_path.write_text(json.dumps(old))
    failed_sha=hashlib.sha256(old_path.read_bytes()).hexdigest()
    monkeypatch.setattr(a,'QUOTA_RECOVERY_LINEAGE',(job,receipt['source_sha256'],failed,failed_sha))
    folder=root/'order-recovery'
    (folder/(job+'.claim.json')).write_text(json.dumps(dict(source_sha256=receipt['source_sha256'],order_batch_id=old['order_batch_id'],order_attempt_id=failed)))
    claim=dict(source_sha256=receipt['source_sha256'],order_batch_id=receipt['order_batch_id'],order_attempt_id=receipt['order_attempt_id'],failed_attempt=failed,failed_receipt_sha256=failed_sha)
    claim_path=folder/(job+'.quota-recovery.claim.json');claim_path.write_text(json.dumps(claim))
    receipt.update(receipt_kind='shipping_quota_failure_recovery_v1',platform_export_triggered=True,
        failed_attempt=failed,failed_receipt_sha256=failed_sha,
        failure_evidence=dict(export_id='27216774510',report='发货报表',code='decrypt_quota_exceeded',applied_at='2026-10-03 00:03:53'),
        quota_evidence=dict(verified=True,source='live_quota_console',checked_at='2026-10-03T02:00:00+08:00'),
        shipping_triggered_at='2026-10-03T02:00:02',
        export_identity=dict(export_id='999',report='发货报表',applied_at='2026-10-03T02:00:03'),
        submit_response=dict(business_status='unknown',http_status=200,body_sha256='a'*64))
    if fault=='old_day':receipt['quota_evidence']['checked_at']='2026-10-02T23:59:59+08:00'
    if fault=='old_id':receipt['export_identity']['export_id']='27216774510'
    if fault=='response_rejected':receipt['submit_response']['business_status']='rejected'
    if fault=='claim':claim_path.write_text('{}')
    if fault=='old_receipt':old_path.write_text('{}')
    path.write_text(json.dumps(receipt));args['receipt_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    if fault:
        with pytest.raises((ValueError, KeyError)): a.validate(root,**args)
    else: assert a.validate(root,**args)['receipt_kind']=='shipping_quota_failure_recovery_v1'
from app.services.order_job_adoption import validate


@pytest.fixture
def manifest(tmp_path):
    job, attempt, batch = 'job2-'+'a'*32, 'b'*32, 'orders-20261002-'+'c'*32
    folder = tmp_path/'2026-10-02'/'taobao'; folder.mkdir(parents=True)
    artifacts = []
    for role, report in [('orders', '订单报表'), ('sales_detail', '宝贝销售明细报表'), ('shipping', '发货报表')]:
        file = folder/(role+'.xlsx'); file.write_bytes(role.encode())
        artifacts.append(dict(role=role, report=report, path=file.relative_to(tmp_path).as_posix(), sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
    recovery = tmp_path/'order-recovery'; recovery.mkdir()
    source = dict(source_job_id=job, business_date='2026-10-02', quota_verified=True, artifacts=artifacts[:2])
    source_raw = json.dumps(source).encode(); (recovery/(job+'.source.json')).write_bytes(source_raw)
    source_sha = hashlib.sha256(source_raw).hexdigest()
    (recovery/(job+'.claim.json')).write_text(json.dumps(dict(source_sha256=source_sha, order_batch_id=batch, order_attempt_id=attempt)))
    receipt = dict(version=1, status='done', order_attempt_id=attempt, order_batch_id=batch, order_business_date='2026-10-02',
                   source_job_id=job, source_sha256=source_sha, quota_verified=True, platform_export_triggered=False,
                   receipt_kind='partial_job_adoption_collect_only', artifacts=artifacts)
    path = tmp_path/'order-runs'/(attempt+'.json'); path.parent.mkdir()
    return tmp_path, path, receipt, dict(attempt=attempt, source_job_id=job, business_date='2026-10-02')


@pytest.mark.parametrize('fault', ['', 'quota', 'partial', 'role', 'same_file', 'source', 'wrong_date', 'path'])
def test_manifest_gate(manifest, fault):
    root, path, receipt, args = manifest
    if fault == 'quota': receipt['quota_verified'] = False
    if fault == 'partial': receipt['status'] = 'error'
    if fault == 'role': receipt['artifacts'][2]['role'] = 'orders'
    if fault == 'same_file': receipt['artifacts'][2] = dict(receipt['artifacts'][0], role='shipping', report='发货报表')
    if fault == 'source': receipt['source_sha256'] = '0'*64
    if fault == 'wrong_date': args['business_date'] = '2026-10-03'
    if fault == 'path': receipt['artifacts'][2]['path'] = '../outside'
    path.write_text(json.dumps(receipt), encoding='utf-8')
    args['receipt_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    if fault:
        with pytest.raises(ValueError): validate(root, **args)
    else:
        assert validate(root, **args) == receipt
        path.write_text('{}')
        with pytest.raises(ValueError): validate(root, **args)


def test_apply_claim_is_single_use_and_previous_attempt_is_required(monkeypatch, manifest):
    from app.services import order_job_adoption as adoption
    from app.services import agent_ingest_service as ai
    from types import SimpleNamespace
    root, path, receipt, args = manifest
    receipt['started_at'] = '2026-10-03T00:30:00+08:00'
    path.write_text(json.dumps(receipt), encoding='utf-8')
    args['receipt_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    args.update(previous_attempt='d'*32, apply=True)
    states, calls = {}, []
    monkeypatch.setattr(ai, 'OUTPUT_DIR', root)
    monkeypatch.setattr(ai, '_load_json', lambda db, key: states.get(key, {}))
    monkeypatch.setattr(ai, '_save_json', lambda db, key, val: states.update({key: val}))
    monkeypatch.setattr(ai, 'latest_order_pull_evidence', lambda db, on: {'order_attempt_id': 'd'*32})
    def recover(db, **kw): calls.append(kw); return {'errors': 0, 'pending': 1}
    monkeypatch.setattr(ai, 'run_ingest', recover)
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='sqlite')), commit=lambda: None)
    assert adoption.adopt(db, **args)['applied']
    assert adoption.adopt(db, **args)['already_claimed']
    assert len(calls) == 1
    assert len(calls[0]['only_paths']) == 3
    assert calls[0]['order_batch_id'] == receipt['order_batch_id']
    assert states['web_agent_order_receipt_evidence']['order_business_date'] == '2026-10-02'
    assert states[ai.KEY_ORDER_QUOTA_RESULT]['order_batch_id'] == receipt['order_batch_id']
