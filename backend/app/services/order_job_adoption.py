"""One-source adoption, explicit business date, no export or automatic delivery."""
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

QUOTA_RECOVERY_LINEAGE = (
    'job2-f0f7d0df859ca8013388081d5a8abf35',
    '3523d9cc0f77263db7ce17bd0194fdcaa5ce059fe52e7cd11061c4e5909a2847',
    '45f0488f0049499487651dd1c5aa201f',
    '937f58d3c06611c13a738ae3fe7d08110f455996ee95e812f65daf7b58ee3d5b',
)


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
            or receipt.get('receipt_kind') not in ('partial_job_adoption_collect_only', 'shipping_quota_failure_recovery_v1')):
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
    if receipt['receipt_kind'] == 'shipping_quota_failure_recovery_v1':
        validate_quota_recovery(root, receipt, source)
    else:
        claim = json.loads((root/'order-recovery'/(source_job_id+'.claim.json')).read_bytes())
        if receipt.get('platform_export_triggered') is not False or claim != {'source_sha256': receipt['source_sha256'], 'order_batch_id': batch, 'order_attempt_id': attempt}:
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


def validate_quota_recovery(root, receipt, source):
    # A separate reviewed recovery, never reinterpret an old collect receipt.
    job, source_sha, failed, failed_sha = QUOTA_RECOVERY_LINEAGE
    if (receipt['source_job_id'] != job or receipt['source_sha256'] != source_sha
            or receipt.get('failed_attempt') != failed or receipt.get('failed_receipt_sha256') != failed_sha
            or receipt.get('platform_export_triggered') is not True):
        raise ValueError('quota_recovery_lineage_invalid')
    raw = (root/'order-runs'/(failed+'.json')).read_bytes()
    old = json.loads(raw)
    claim = json.loads((root/'order-recovery'/(job+'.claim.json')).read_bytes())
    if (hashlib.sha256(raw).hexdigest() != failed_sha or old.get('status') != 'error'
            or old.get('artifacts') != source['artifacts']
            or claim != {'source_sha256': source_sha, 'order_batch_id': old['order_batch_id'], 'order_attempt_id': failed}):
        raise ValueError('prior_failure_changed')
    new_claim = json.loads((root/'order-recovery'/(job+'.quota-recovery.claim.json')).read_bytes())
    if new_claim != {'source_sha256': source_sha, 'order_batch_id': receipt['order_batch_id'],
                     'order_attempt_id': receipt['order_attempt_id'], 'failed_attempt': failed,
                     'failed_receipt_sha256': failed_sha}:
        raise ValueError('quota_recovery_claim_mismatch')
    failure = receipt.get('failure_evidence') or {}
    if (failure.get('export_id') != '27216774510' or failure.get('report') != '发货报表'
            or failure.get('code') != 'decrypt_quota_exceeded'
            or failure.get('applied_at') != '2026-10-03 00:03:53'
            or receipt['order_business_date'] != '2026-10-02'):
        raise ValueError('quota_failure_evidence_invalid')
    cn = timezone(timedelta(hours=8))
    quota, identity = receipt.get('quota_evidence') or {}, receipt.get('export_identity') or {}
    try:
        checked = datetime.fromisoformat(quota['checked_at'])
        trigger = datetime.fromisoformat(receipt['shipping_triggered_at']).replace(tzinfo=cn)
        applied = datetime.fromisoformat(identity['applied_at']).replace(tzinfo=cn)
        valid_time = (checked.tzinfo is not None and checked.astimezone(cn).date() == trigger.date()
                      and 0 <= (trigger-checked).total_seconds() <= 600
                      and 0 <= (applied-trigger).total_seconds() <= 120)
    except (ValueError, KeyError, TypeError):
        valid_time = False
    response = receipt.get('submit_response') or {}
    if (not valid_time or quota.get('verified') is not True or quota.get('source') != 'live_quota_console'
            or identity.get('report') != '发货报表' or not re.fullmatch(r'\d{1,30}', identity.get('export_id', ''))
            or identity['export_id'] == failure['export_id']
            or response.get('business_status') not in ('accepted', 'unknown')
            or not isinstance(response.get('http_status'), int) or not 200 <= response['http_status'] < 400
            or not re.fullmatch(r'[a-f0-9]{64}', response.get('body_sha256', ''))
            or (response.get('export_id') and response['export_id'] != identity['export_id'])):
        raise ValueError('quota_or_new_export_evidence_invalid')


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
            'order_business_date': business_date, 'checked_at': (receipt.get('quota_evidence') or {}).get('checked_at', receipt['started_at']),
            'source_job_id': source_job_id})
        db.commit()
    # Explicit-only business call. Existing importer retains file and password gates.
    # Never resolve the latest global receipt again: another day may have started
    # after this claim. Only the already validated exact manifest can be imported.
    roles = {Path(a['path']).name: a['role'] for a in receipt['artifacts']}
    imported = ingest.run_ingest(db, only_paths=[str(ingest.OUTPUT_DIR/a['path']) for a in receipt['artifacts']],
                                 artifact_roles=roles, order_batch_id=receipt['order_batch_id'])
    result['recovery'] = {key: imported.get(key) for key in ['scanned', 'imported', 'pending', 'errors']}
    result['applied'] = not bool(imported.get('errors'))
    result['delivery_triggered'] = False
    ingest._save_json(db, key, {'identity': identity, 'status': 'completed' if result['applied'] else 'blocked'})
    db.commit()
    return result
