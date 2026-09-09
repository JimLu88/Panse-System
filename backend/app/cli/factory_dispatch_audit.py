"""Factory table maintenance handoff: default read-only diff, explicit exact apply.

python -m app.cli.factory_dispatch_audit
python -m app.cli.factory_dispatch_audit --apply-table tblqn9PDPO0S69wZ
No other tables, messages, schema migrations or record deletions are permitted.
"""
import argparse
import json
import hashlib
from sqlalchemy import text, select
from app.database import SessionLocal
from app.services import factory_dispatch_feishu_service as service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-table', default='')
    parser.add_argument('--source-status', action='store_true', help='Read archive/provenance timestamps only; no import')
    args = parser.parse_args()
    if args.apply_table and args.apply_table != service.DEFAULT_TABLE_ID:
        parser.error('Only the exact factory table is supported')
    with SessionLocal() as db:
        if not args.apply_table and db.get_bind().dialect.name == 'postgresql':
            db.execute(text('SET TRANSACTION READ ONLY'))
        result = service.sync(db) if args.apply_table else service.preview_diff(db)
        if args.source_status:
            from app.models.import_file import ImportedFile
            from app.models.order import Order
            archives = db.scalars(select(ImportedFile).where(ImportedFile.kind=='taobao')
                .order_by(ImportedFile.id.desc()).limit(25)).all()
            result['source_status'] = {
                'recent_archives': [{'id': r.id, 'archived_at': r.created_at,
                    'source': r.source, 'sha256': r.file_hash,
                    'agent_status': (r.row_summary or {}).get('agent_status', 'unknown'),
                    'role': (r.row_summary or {}).get('agent_report_role'),
                    'errors_present': bool((r.row_summary or {}).get('errors'))} for r in archives],
                'orders_with_field_provenance': sum(bool(value) for value in db.scalars(select(Order.platform_field_state))),
                'archive_time_is_not_platform_capture_time': True,
                'new_download_triggered': False,
            }
        result['clear_intents'] = [{**{key:value for key,value in intent.items()
                                    if key not in ('order_no','sub_order_no')},
            'entity_sha256': hashlib.sha256((str(intent.get('order_no'))+'|'+str(intent.get('sub_order_no'))).encode()).hexdigest()}
            for intent in result.get('clear_intents', [])]
        # Deliberately no row values, credentials, customer/contact/order IDs.
        for key in ('missing_wood_cost', 'missing_factory_sheet_image', 'deferred_image_uploads'):
            result[key + '_count'] = len(result.pop(key, []) or [])
        result['warning_count'] = len(result.pop('warnings', []) or [])
        result['error_count'] = len(result.get('errors', []))
        result['errors'] = ['sync_or_readback_failed_see_private_service_log'] if result['error_count'] else []
        print(json.dumps(result, ensure_ascii=False, default=str))
        if not args.apply_table:
            db.rollback()
        return 0 if result.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
