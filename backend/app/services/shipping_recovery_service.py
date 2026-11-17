"""Durable exact-file password target. Independent recovery never rewrites a pull."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from app.models.import_file import ImportedFile
from app.models.settings import SystemSetting
from app.services import import_storage

PREFIX = 'shipping_target:'
ACTIVE = 'shipping_target_active'


def _row(db, key, lock=False):
    q = select(SystemSetting).where(SystemSetting.key == key)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    return db.scalar(q)


def _save(db, key, value):
    row = _row(db, key)
    if row is None:
        row = SystemSetting(key=key, is_secret=False)
        db.add(row)
    row.value_plain = json.dumps(value, ensure_ascii=False)
    db.flush()


def _file(manifest):
    from app.services.agent_ingest_service import OUTPUT_DIR
    relative = Path(manifest['path'])
    root = OUTPUT_DIR.resolve()
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root) or not path.is_file():
        raise ValueError('shipping_target_path_invalid')
    if path.name != 'ExportOrderList' + manifest['export_id'] + '.xlsx':
        raise ValueError('shipping_target_export_id_mismatch')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest['sha256']:
        raise ValueError('shipping_target_hash_mismatch')
    return path, raw


def register(db, manifest):
    """Registration must succeed before the platform password request is sent."""
    allowed = {'path', 'sha256', 'export_id', 'business_date', 'generated_at',
               'order_batch_id', 'mode', 'source_file_ids', 'sub_order_nos'}
    if set(manifest) - allowed:
        raise ValueError('shipping_target_unknown_fields')
    manifest = {**manifest, 'source_file_ids': sorted(manifest.get('source_file_ids') or []),
                'sub_order_nos': sorted(manifest.get('sub_order_nos') or [])}
    if (not re.fullmatch(r'[0-9a-f]{64}', manifest.get('sha256', ''))
            or not re.fullmatch(r'\d{1,30}', manifest.get('export_id', ''))
            or manifest.get('mode') not in ('original', 'independent')):
        raise ValueError('shipping_target_identity_invalid')
    day = date.fromisoformat(manifest['business_date'])
    generated = datetime.fromisoformat(manifest['generated_at'])
    if generated.tzinfo is None or generated.astimezone(ZoneInfo('Asia/Shanghai')).date() not in (day, day + timedelta(days=1)):
        raise ValueError('shipping_target_date_invalid')
    if not re.fullmatch('orders-' + day.strftime('%Y%m%d') + r'-[0-9a-f]{32}', manifest.get('order_batch_id', '')):
        raise ValueError('shipping_target_batch_invalid')
    path, raw = _file(manifest)
    from app.services import taobao_order_import as importer
    if importer.detect_report_role(path.name, raw) != 'shipping':
        raise ValueError('shipping_target_role_invalid')
    sources = []
    if manifest['mode'] == 'independent':
        subs = manifest['sub_order_nos']
        if not 1 <= len(subs) <= 50 or len(set(subs)) != len(subs) or any(not re.fullmatch(r'\d{10,32}', x) for x in subs):
            raise ValueError('shipping_target_child_scope_required')
        if len(manifest['source_file_ids']) != 2 or len(set(manifest['source_file_ids'])) != 2:
            raise ValueError('shipping_target_two_sources_required')
        for fid in manifest['source_file_ids']:
            rec = db.get(ImportedFile, fid)
            st = (rec.row_summary or {}) if rec else {}
            if (not rec or st.get('agent_status') != 'imported' or st.get('errors')
                    or st.get('automation_batch_id') != manifest['order_batch_id']):
                raise ValueError('shipping_target_source_batch_mismatch')
            if hashlib.sha256(import_storage.read(rec.stored_path)).hexdigest() != rec.file_hash:
                raise ValueError('shipping_target_source_hash_mismatch')
            sources.append({'file_id': fid, 'sha256': rec.file_hash, 'role': st.get('agent_report_role')})
        if {s['role'] for s in sources} != {'orders', 'sales_detail'}:
            raise ValueError('shipping_target_source_roles_invalid')
    elif manifest['source_file_ids'] or manifest['sub_order_nos']:
        raise ValueError('original_target_cannot_override_child_scope')
    key = PREFIX + manifest['sha256']
    previous = _row(db, key)
    if previous:
        saved = json.loads(previous.value_plain)
        if saved['manifest'] != manifest:
            raise ValueError('shipping_target_registration_conflict')
        return {'ok': True, 'target_id': key, 'state': saved['state'], 'password_request_allowed': False}
    active = _row(db, ACTIVE, lock=True)
    if active:
        current = _row(db, json.loads(active.value_plain))
        if current and json.loads(current.value_plain)['state'] not in ('done', 'partial', 'imported'):
            raise ValueError('shipping_target_unresolved_do_not_replace')
    saved = {'manifest': manifest, 'sources': sources, 'state': 'registered',
             'registered_at': datetime.now(timezone.utc).isoformat()}
    _save(db, key, saved)
    _save(db, ACTIVE, key)
    db.commit()
    return {'ok': True, 'target_id': key, 'state': 'registered', 'password_request_allowed': True}


def apply_password(db, pwd):
    """None means no explicit target; never discover a target from old files."""
    active = _row(db, ACTIVE, lock=True)
    if not active:
        return None
    key = json.loads(active.value_plain)
    row = _row(db, key, lock=True)
    if not row:
        raise ValueError('shipping_target_record_missing')
    record = json.loads(row.value_plain)
    m = record['manifest']
    base = {'target_id': key, 'independent': m['mode'] == 'independent',
            'scope_artifacts': [Path(m['path']).name], 'tried': 0, 'imported': 0,
            'files': [], 'pending_files': [], 'business_date': m['business_date']}
    if record['state'] not in ('registered', 'password_mismatch'):
        return {**base, **record.get('result', {}), 'duplicate': True, 'status': record['state']}
    path, raw = _file(m)
    from app.services import taobao_order_import as importer
    try:
        importer.maybe_decrypt(raw, pwd)
    except ValueError:
        result = {**base, 'tried': 1, 'status': 'password_mismatch',
                  'pending_files': [path.name], 'failure_reason': f'{path.name} 口令不匹配或文件异常'}
        record.update(state='password_mismatch', result=result)
        _save(db, key, record)
        db.commit()
        return result
    # Commit before import/delivery. An interrupted processing claim requires
    # inspection, never blind replay. No plaintext password is recorded here.
    record['state'] = 'processing'
    _save(db, key, record)
    db.commit()
    prior = db.scalar(select(ImportedFile).where(ImportedFile.file_hash == m['sha256']).order_by(ImportedFile.id.desc()))
    if prior and (prior.row_summary or {}).get('agent_status') == 'imported':
        archive = prior
        updated = 0
    else:
        # Pass encrypted bytes so the shipping role survives layout detection.
        rep = importer.import_taobao_orders(db, path.name, raw, password=pwd,
                    source_observed_at=datetime.fromisoformat(m['generated_at']), source_kind='agent_download')
        if rep.errors:
            result = {**base, 'tried': 1, 'status': 'import_failed', 'failure_reason': '口令已验证；发货资料入库失败'}
            record.update(state='import_failed', result=result)
            _save(db, key, record)
            db.commit()
            return result
        from dataclasses import asdict
        summary = {**asdict(rep), 'agent_status': 'imported', 'agent_report_role': 'shipping',
                   'shipping_target_id': key, 'order_business_date': m['business_date'],
                   'generated_at': m['generated_at']}
        if m['mode'] == 'original':
            summary['automation_batch_id'] = m['order_batch_id']
        archive = import_storage.archive(db, content=raw, original_name=path.name, kind='taobao',
                                         source='api', row_summary=summary).file
        updated = rep.updated
        db.commit()
    result = {**base, 'tried': 1, 'imported': 1, 'updated': updated, 'status': 'imported',
              'files': [{'file': path.name, 'status': 'imported', 'file_id': archive.id}]}
    if m['mode'] == 'independent':
        from app.services.order_scoped_resume_service import resume
        try:
            recovery = resume(db, business_date=m['business_date'], order_batch_id=m['order_batch_id'],
                              file_ids=m['source_file_ids'] + [archive.id], sub_order_nos=m['sub_order_nos'],
                              request_id=m['sha256'][:32], dry_run=False, shipping_target_id=key)
        except Exception as exc:
            db.rollback()
            recovery = {'status': 'needs_review', 'error_type': type(exc).__name__,
                        'held': [{'reason': 'source_evidence_or_delivery_needs_review'}],
                        'original_pull_terminal_unchanged': True}
        result['recovery'] = recovery
        result['status'] = recovery['status']
    record.update(state=result['status'], result=result)
    _save(db, key, record)
    db.commit()
    return result
