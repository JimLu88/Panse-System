"""One-source adoption, explicit business date, no export or automatic delivery."""
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path


def validate(root, *, attempt, receipt_sha256, source_job_id, business_date):
    root = Path(root).resolve()
    day = date.fromisoformat(business_date)
    if not re.fullmatch(r'[a-f0-9]{32}', attempt) or not re.fullmatch(r'job\d+-[a-f0-9]{32}', source_job_id):
        raise ValueError('invalid_identity')
    path = root/'order-runs'/(attempt+'.json')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != receipt_sha256:
        raise ValueError('receipt_hash_mismatch')
    receipt = json.loads(raw)
    if (receipt.get('version') != 1 or receipt.get('status') != 'done'
            or receipt.get('order_attempt_id') != attempt
            or receipt.get('order_business_date') != business_date
            or receipt.get('source_job_id') != source_job_id
            or receipt.get('quota_verified') is not True
            or receipt.get('platform_export_triggered') is not False
            or receipt.get('receipt_kind') != 'partial_job_adoption_collect_only'):
        raise ValueError('receipt_identity_status_or_quota_invalid')
    batch = receipt.get('order_batch_id', '')
    if not re.fullmatch('orders-'+day.strftime('%Y%m%d')+r'-[a-f0-9]{32}', batch):
        raise ValueError('invalid_batch')
    source_bytes = (root/'order-recovery'/(source_job_id+'.source.json')).read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != receipt.get('source_sha256'):
        raise ValueError('source_hash_mismatch')
    source = json.loads(source_bytes)
    if source.get('business_date') != business_date or source.get('source_job_id') != source_job_id or source.get('quota_verified') is not True:
        raise ValueError('source_identity_invalid')
    claim = json.loads((root/'order-recovery'/(source_job_id+'.claim.json')).read_bytes())
    if claim != {'source_sha256': receipt['source_sha256'], 'order_batch_id': batch, 'order_attempt_id': attempt}:
        raise ValueError('claim_mismatch')
    artifacts = receipt.get('artifacts') or []
    expected = {'orders': '订单报表', 'sales_detail': '宝贝销售明细报表', 'shipping': '发货报表'}
    if len(artifacts) != 3 or {a.get('role') for a in artifacts} != set(expected):
        raise ValueError('roles_incomplete')
    paths, hashes = set(), set()
    for item in artifacts:
        file = (root/item['path']).resolve()
        rel = file.relative_to(root)
        if len(rel.parts) != 3 or rel.parts[:2] != (business_date, 'taobao') or item.get('report') != expected[item['role']]:
            raise ValueError('artifact_scope_or_role_invalid')
        if hashlib.sha256(file.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('artifact_hash_mismatch')
        paths.add(item['path']); hashes.add(item['sha256'])
    if len(paths) != 3 or len(hashes) != 3 or [a for a in artifacts if a['role'] != 'shipping'] != source.get('artifacts'):
        raise ValueError('source_artifacts_changed')
    return receipt


def adopt(db, *, attempt, receipt_sha256, source_job_id, business_date, previous_attempt, apply=False):
    from app.services import agent_ingest_service as ingest
    from sqlalchemy import text
    receipt = validate(ingest.OUTPUT_DIR, attempt=attempt, receipt_sha256=receipt_sha256,
                       source_job_id=source_job_id, business_date=business_date)
    result = {'validated': True, 'applied': False, 'order_batch_id': receipt['order_batch_id'],
              'business_date': business_date, 'source_job_id': source_job_id}
    if not apply:
        return result
    # Cross-process transaction lock; no claim or partial evidence overwrite.
    if db.get_bind().dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': 40720261003})
    state = ingest._load_json(db, ingest.KEY_ORCH_STATE)
    if state.get('running'):
        raise ValueError('orchestration_running')
    key = 'order_job_adoption_' + source_job_id
    old = ingest._load_json(db, key)
    identity = {'order_batch_id': receipt['order_batch_id'], 'order_attempt_id': attempt,
                'receipt_sha256': receipt_sha256, 'business_date': business_date}
    if old and old.get('identity') != identity:
        raise ValueError('source_already_adopted_differently')
    if old:
        return {**result, 'already_claimed': True, 'prior_status': old.get('status'),
                'applied': old.get('status') == 'completed', 'delivery_triggered': False}
    existing = ingest._load_json(db, 'web_agent_order_receipt_evidence')
    current = ingest.latest_order_pull_evidence(db, on=date.fromisoformat(business_date))
    if current.get('order_attempt_id') != previous_attempt or previous_attempt == attempt:
        raise ValueError('previous_attempt_changed')
    if existing.get('order_business_date') and existing['order_business_date'] > business_date:
        raise ValueError('newer_business_day_evidence_exists')
    if not old:
        roles = {Path(a['path']).name: a['role'] for a in receipt['artifacts']}
        # Distinct adoption provenance; never overwrite the original job1 receipt.
        evidence = {'started_at': datetime.now().isoformat(timespec='microseconds'),
                    'order_business_date': business_date, 'order_batch_id': receipt['order_batch_id'],
                    'order_attempt_id': attempt, 'manual_recovery': True,
                    'source_job_id': source_job_id, 'receipt_source': 'order-runs/'+attempt+'.json',
                    'tasks': [{'task': 'taobao_orders', 'status': 'done',
                               'order_batch_id': receipt['order_batch_id'],
                               'artifacts': list(roles), 'artifact_roles': roles}]}
        ingest._save_json(db, key, {'identity': identity, 'status': 'claimed'})
        ingest._save_json(db, 'web_agent_order_receipt_evidence', evidence)
        ingest._save_json(db, ingest.KEY_ORDER_QUOTA_RESULT, {
            'verified': True, 'state': 'verified_unlimited', 'order_batch_id': receipt['order_batch_id'],
            'order_business_date': business_date, 'checked_at': receipt['started_at'],
            'source_job_id': source_job_id})
        db.commit()
    # Explicit-only business call. Existing importer retains file and password gates.
    result['recovery'] = ingest.recover_order_receipt(db, on=date.fromisoformat(business_date))
    result['applied'] = bool(result['recovery'].get('recovered'))
    result['delivery_triggered'] = False
    ingest._save_json(db, key, {'identity': identity, 'status': 'completed' if result['applied'] else 'blocked'})
    db.commit()
    return result
