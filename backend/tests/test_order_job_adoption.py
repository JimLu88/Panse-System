import hashlib
import json
import pytest
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
    def recover(db, on): calls.append(on); return {'recovered': True}
    monkeypatch.setattr(ai, 'recover_order_receipt', recover)
    db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name='sqlite')), commit=lambda: None)
    assert adoption.adopt(db, **args)['applied']
    assert adoption.adopt(db, **args)['already_claimed']
    assert len(calls) == 1
    assert states['web_agent_order_receipt_evidence']['order_business_date'] == '2026-10-02'
    assert states[ai.KEY_ORDER_QUOTA_RESULT]['order_batch_id'] == receipt['order_batch_id']
