"""One read-only ERP pricing snapshot; no prepare chain or platform operations."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SQL = """BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '20s';
SELECT row_to_json(q) FROM (
 SELECT s.product_code, s.sku_code AS code, s.product_name, s.sku AS sku_name,
 p.taobao_item_id::text AS item, p.taobao_sku_id::text AS sku,
 p.alt_taobao_sku_ids AS alt, s.daily_price::text AS daily,
 p.mid_buyer_price::text AS medium_target, p.big_buyer_price::text AS big_target,
 s.is_custom_placeholder AS custom, g.listing_status,
 g.taobao_id AS product_item_id, g.alt_taobao_ids AS product_alt_item_ids,
 s.updated_at::text AS price_updated_at, p.updated_at::text AS mapping_updated_at
 FROM pricing_sku s LEFT JOIN pricing_sku_promo p ON p.sku_code=s.sku_code
 LEFT JOIN products g ON g.code=s.product_code ORDER BY s.product_code,s.sku_code
) q;
COMMIT;
"""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load_rows():
    command = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-db-1 psql -X -v ON_ERROR_STOP=1 -U panse -d panse_erp -At'
    run = subprocess.run(['C:/Program Files/Git/usr/bin/ssh.exe', '-i', str(Path.home()/'.ssh/panse_nas'), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-p', '2222', '15068803006@DS923plus', command], input=SQL, capture_output=True, text=True, encoding='utf-8', timeout=45, check=True)
    rows = [json.loads(line) for line in run.stdout.splitlines() if line.startswith('{')]
    if not rows or len({r['code'] for r in rows}) != len(rows):
        raise ValueError('empty_or_duplicate_erp_snapshot')
    return rows


def apply_rotation_receipt(rows, receipt):
    """A provenance-bearing file overlay, never a database mapping mutation."""
    result = deepcopy(rows)
    if receipt.get('official_success') is not True:
        raise ValueError('rotation_receipt_not_confirmed')
    mapping = receipt.get('new_sku_mapping') or {}
    if not mapping or len(set(mapping.values())) != len(mapping):
        raise ValueError('invalid_rotation_mapping')
    for old, new in mapping.items():
        candidates = [r for r in result if old in [str(r.get('sku')), *map(str, r.get('alt') or [])] or new in [str(r.get('sku')), *map(str, r.get('alt') or [])]]
        if len(candidates) != 1 or candidates[0]['item'] not in receipt['item_ids']:
            raise ValueError('rotation_identity_not_unique:' + old)
        row = candidates[0]
        row.setdefault('rotation_source_ids', []).append({'old': old, 'new': new, 'batch': receipt['batch_id']})
        if str(row.get('sku')) == old:
            row['sku'] = new
        row['alt'] = list(dict.fromkeys(new if str(x) == old else str(x) for x in row.get('alt') or []))
        row['alt'] = [x for x in row['alt'] if x != row.get('sku')]
    return result


def build_snapshot(rows, receipt=None):
    resolved = apply_rotation_receipt(rows, receipt) if receipt is not None else deepcopy(rows)
    active = [r for r in resolved if r.get('listing_status') == '在售']
    ids = set()
    for r in active:
        for value in [r.get('item'), r.get('product_item_id'), *(r.get('product_alt_item_ids') or [])]:
            if str(value or '').isdigit():
                ids.add(str(value))
    return {
        'schema': 'campaign_erp_price_snapshot_v1', 'captured_at': datetime.now(timezone.utc).isoformat(),
        'source': 'ERP pricing_sku + pricing_sku_promo + products, one repeatable-read read-only transaction',
        'erp_price_version_sha256': digest(rows), 'resolved_price_version_sha256': digest(resolved),
        'rotation_receipt_sha256': digest(receipt) if receipt else None,
        'database_write': False, 'platform_write': False, 'no_sales_filter_applied': False,
        'all_erp_rows': resolved, 'current_sellable_item_ids': sorted(ids),
        'unknown_listing_status_codes': [r['code'] for r in resolved if not r.get('listing_status')],
        'unmapped_sellable_codes': [r['code'] for r in active if not r.get('item') or not r.get('sku')],
        'notes': 'ERP listing status is a local source, not a fresh platform scan. All raw rows and unknown states are retained. Use the current official template for actual enabled SKU range; never use historical no-sales to filter. Daily/medium/big are stored values, not recomputed. Custom original floors must come from separate confirmed provenance, not current daily.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rotation-receipt', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('snapshot_output_already_exists_no_overwrite')
    receipt = json.loads(args.rotation_receipt.read_text(encoding='utf-8-sig')) if args.rotation_receipt else None
    snapshot = build_snapshot(load_rows(), receipt)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(snapshot, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k:v for k,v in snapshot.items() if k not in ('all_erp_rows', 'notes')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
