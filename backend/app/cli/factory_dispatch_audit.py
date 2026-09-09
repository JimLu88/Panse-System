"""Factory table maintenance handoff: default read-only diff, explicit exact apply.

python -m app.cli.factory_dispatch_audit
python -m app.cli.factory_dispatch_audit --apply-table tblqn9PDPO0S69wZ
No other tables, messages, schema migrations or record deletions are permitted.
"""
import argparse
import json
from sqlalchemy import text
from app.database import SessionLocal
from app.services import factory_dispatch_feishu_service as service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-table', default='')
    args = parser.parse_args()
    if args.apply_table and args.apply_table != service.DEFAULT_TABLE_ID:
        parser.error('Only the exact factory table is supported')
    with SessionLocal() as db:
        if not args.apply_table and db.get_bind().dialect.name == 'postgresql':
            db.execute(text('SET TRANSACTION READ ONLY'))
        result = service.sync(db) if args.apply_table else service.preview_diff(db)
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
