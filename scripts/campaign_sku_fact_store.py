"""Versioned local Taobao export evidence. Never writes ERP or platform data.

Raw merchant codes (including blanks/duplicates) are evidence, not permission
to replace ERP identities, prices, listing state or successful campaign claims.
"""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

from campaign_official_template import read_rows

DEFAULT_ROOT = Path('D:/AI/畔色ERP系统/活动准备/商品SKU事实')
SCHEMA = 'panse_sku_fact_export_v1'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('timestamp_timezone_required')
    return parsed.isoformat()


def root_path(root=None):
    return Path(root or os.environ.get('PANSE_SKU_FACT_ROOT') or DEFAULT_ROOT)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def parse(raw):
    """Read physical XML rows, not the vendor's frequently incorrect A1 dimension."""
    cells = read_rows(raw, '发布模板')
    headers = [(n, c) for n, c in cells.items() if '商品Id' in c.values() and 'skuId' in c.values()]
    if len(headers) != 1:
        raise ValueError('export_header_not_unique')
    n, header = headers[0]
    def column(label):
        found = [c for c, v in header.items() if v == label]
        if len(found) != 1:
            raise ValueError('export_column_not_unique:' + label)
        return found[0]
    def number(col):
        value = 0
        for char in col:
            value = value * 26 + ord(char) - 64
        return value
    sku_col = column('skuId')
    code_cols = [c for c, v in header.items() if v == '商家编码' and number(c) > number(sku_col)]
    if len(code_cols) != 1:
        raise ValueError('sku_code_column_not_unique')
    columns = {'item': column('商品Id'), 'sku': sku_col, 'merchant_code': code_cols[0],
               'attributes': column('销售属性'), 'title': column('宝贝标题'),
               'platform_price': column('价格(元)'), 'stock': column('库存(件)')}
    rows = []
    current_item = ''
    for row_number, row in sorted(cells.items()):
        if row_number <= n or not any(row.values()):
            continue
        record = {key: row.get(col, '') for key, col in columns.items()}
        record['raw_item'] = record['item']
        if record['item']:
            current_item = record['item'].strip()
        record['item'] = current_item
        record['sku'] = record['sku'].strip()
        record.update(sheet='发布模板', row=row_number)
        record['issues'] = []
        if not current_item.isdigit():
            record['issues'].append('invalid_item_id')
        if not record['sku']:
            record['issues'].append('product_row_without_sku_id')
        elif not record['sku'].isdigit():
            record['issues'].append('invalid_sku_id')
        if not record['merchant_code'].strip():
            record['issues'].append('merchant_code_blank')
        rows.append(record)
    if not rows:
        raise ValueError('empty_product_export')
    pairs = Counter((r['item'], r['sku']) for r in rows if r['sku'])
    codes = Counter((r['item'], r['merchant_code'].strip()) for r in rows if r['merchant_code'].strip())
    for row in rows:
        if row['sku'] and pairs[row['item'], row['sku']] > 1:
            row['issues'].append('duplicate_item_sku')
        if row['merchant_code'].strip() and codes[row['item'], row['merchant_code'].strip()] > 1:
            row['issues'].append('repeated_merchant_code_in_product')
    return rows


def register(source, exported_at, *, root=None, export_time_basis='user_confirmed_filename'):
    source = Path(source).resolve(strict=True)
    exported_at = timestamp(exported_at)
    raw = source.read_bytes()
    source_sha = sha(raw)
    rows = parse(raw)
    root = root_path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    db = sqlite3.connect(root / 'index.sqlite3', timeout=10)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS versions (sha TEXT PRIMARY KEY, exported_at TEXT NOT NULL, exported_epoch REAL NOT NULL, updated_at TEXT NOT NULL, manifest_sha TEXT NOT NULL)')
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT exported_at FROM versions WHERE sha=?', (source_sha,)).fetchone()
        if existing:
            if existing[0] != exported_at:
                raise ValueError('same_file_export_time_conflict')
            db.rollback()
            return read_version(source_sha, root=root)[0]
        folder = root / 'versions' / source_sha
        folder.mkdir(parents=True, exist_ok=True)
        saved = folder / 'source.xlsx'
        if saved.exists() and saved.read_bytes() != raw:
            raise ValueError('archived_source_changed')
        if not saved.exists():
            with saved.open('xb') as stream:
                stream.write(raw)
        metadata = dict(schema=SCHEMA, source_sha256=source_sha, original_filename=source.name,
                        original_path=str(source), exported_at=exported_at, export_time_basis=export_time_basis,
                        source_modified_at=datetime.fromtimestamp(source.stat().st_mtime, timezone.utc).isoformat(),
                        received_at=now, updated_at=now,
                        counts=dict(products=len({r['item'] for r in rows if r['item'].isdigit()}),
                                    data_rows=len(rows), sku_rows=sum(r['sku'].isdigit() for r in rows),
                                    rows_without_sku_id=sum(not r['sku'] for r in rows),
                                    rows_with_blank_code=sum(not r['merchant_code'].strip() for r in rows)),
                        coverage=dict(platform_page_count_verified=False, on_sale_scope_verified=False,
                                      sku_enabled_state_known=False),
                        platform_write=False, database_business_write=False)
        manifest = folder / 'manifest.json'
        if manifest.exists():
            # Recover only our unchanged pre-index archive after a process interruption.
            old = load(manifest)
            if any(old.get(k) != metadata[k] for k in ('schema', 'source_sha256', 'exported_at', 'counts', 'coverage')):
                raise ValueError('unindexed_manifest_conflict')
            metadata = old
        else:
            with manifest.open('x', encoding='utf-8') as stream:
                json.dump(metadata, stream, ensure_ascii=False, indent=2)
        db.execute('INSERT INTO versions VALUES (?,?,?,?,?)',
                   (source_sha, exported_at, datetime.fromisoformat(exported_at).timestamp(),
                    metadata['updated_at'], sha(manifest.read_bytes())))
        db.commit()
        return metadata
    finally:
        db.close()


def read_version(version=None, *, root=None):
    root = root_path(root).resolve()
    index = root / 'index.sqlite3'
    if not index.exists():
        if version:
            raise ValueError('sku_fact_registry_missing')
        return None, []
    db = sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)
    try:
        if version is None:
            latest = db.execute('SELECT sha,exported_epoch FROM versions ORDER BY exported_epoch DESC LIMIT 2').fetchall()
            if not latest:
                return None, []
            if len(latest) > 1 and latest[0][1] == latest[1][1]:
                raise ValueError('latest_export_timestamp_ambiguous')
            version = latest[0][0]
        if not re.fullmatch('[0-9a-f]{64}', version):
            raise ValueError('invalid_export_version')
        registered = db.execute('SELECT manifest_sha,exported_at,exported_epoch,updated_at FROM versions WHERE sha=?', (version,)).fetchone()
        if registered is None:
            raise ValueError('export_version_not_registered')
        folder = root / 'versions' / version
        manifest = folder / 'manifest.json'
        if sha(manifest.read_bytes()) != registered[0]:
            raise ValueError('sku_fact_manifest_changed')
        metadata = load(manifest)
        if (metadata['exported_at'] != registered[1]
                or datetime.fromisoformat(metadata['exported_at']).timestamp() != registered[2]
                or metadata['updated_at'] != registered[3]):
            raise ValueError('sku_fact_index_metadata_changed')
        raw = (folder / 'source.xlsx').read_bytes()
        if sha(raw) != version or metadata.get('source_sha256') != version or metadata.get('schema') != SCHEMA:
            raise ValueError('sku_fact_source_changed')
        return metadata, parse(raw)
    finally:
        db.close()


def bind_latest(snapshot, *, root=None):
    """Pin facts into a NEW local snapshot, leaving prices and old snapshots intact."""
    result = deepcopy(snapshot)
    if result.get('sku_fact_source'):
        read_version(result['sku_fact_source']['sha256'], root=result['sku_fact_source']['root'])
        return result
    metadata, _ = read_version(root=root)
    if metadata:
        result['sku_fact_source'] = dict(root=str(root_path(root).resolve()), sha256=metadata['source_sha256'],
                                       exported_at=metadata['exported_at'], updated_at=metadata['updated_at'])
    return result


def semantic_issue(fact, erp):
    """Reject known material/intent conflicts; names never create an identity."""
    if not erp.get('custom'):
        return None
    def signature(text):
        materials = {m for m in ('樱桃木','黑胡桃木','白橡木','白蜡木','榉木','松木','岩板') if m in text}
        intent = ('micro' if '微定制' in text else 'size' if '尺寸' in text else
                  'material' if materials or '材质' in text else '')
        return materials, intent
    a, ai = signature(fact['attributes'])
    b, bi = signature(erp.get('sku_name') or '')
    if a and b and not a.intersection(b) or ai and bi and ai != bi:
        return 'merchant_code_semantic_conflict'
    if not ai or not bi:
        return 'custom_code_meaning_unverified'
    return None


class FactResolver:
    def __init__(self, snapshot):
        self.rows = snapshot['all_erp_rows']
        self.facts = defaultdict(list)
        self.by_code = defaultdict(list)
        self.reference = snapshot.get('sku_fact_source')
        for row in self.rows:
            self.by_code[row['code']].append(row)
        if self.reference:
            _, rows = read_version(self.reference['sha256'], root=self.reference['root'])
            for row in rows:
                if row['sku']:
                    self.facts[row['item'], row['sku']].append(row)

    def resolve(self, item, sku, existing):
        facts = self.facts.get((item, sku), [])
        if not facts:
            return existing, None, None
        if len(facts) != 1 or 'duplicate_item_sku' in facts[0]['issues']:
            return [], 'duplicate_export_pair', None
        fact = facts[0]
        evidence = dict(source=self.reference, sheet=fact['sheet'], row=fact['row'],
                        merchant_code=fact['merchant_code'], attributes=fact['attributes'])
        code = fact['merchant_code'].strip()
        if not code:
            # Never erase a valid ERP binding; absence cannot establish a new one.
            return existing, None if existing else 'merchant_code_blank', evidence
        if existing and any(r['code'] != code for r in existing):
            return [], 'current_export_conflicts_with_erp_binding', evidence
        candidates = self.by_code.get(code, [])
        if len(candidates) != 1:
            return [], 'merchant_code_missing_or_ambiguous_in_erp', evidence
        candidate = candidates[0]
        items = {str(candidate.get('item')), str(candidate.get('product_item_id')),
                 *map(str, candidate.get('product_alt_item_ids') or [])}
        if item not in items:
            return [], 'merchant_code_product_alias_not_proven', evidence
        issue = semantic_issue(fact, candidate)
        if issue:
            return [], issue, evidence
        return [candidate], None, evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    add = commands.add_parser('register')
    add.add_argument('--source', type=Path, required=True)
    add.add_argument('--exported-at', required=True)
    query = commands.add_parser('query')
    query.add_argument('--version')
    query.add_argument('--item')
    query.add_argument('--sku')
    bind = commands.add_parser('bind')
    bind.add_argument('--snapshot', type=Path, required=True)
    bind.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'register':
        result = register(args.source, args.exported_at, root=args.root)
    elif args.command == 'query':
        metadata, rows = read_version(args.version, root=args.root)
        result = dict(metadata=metadata, rows=[r for r in rows if
                      (not args.item or r['item'] == args.item) and (not args.sku or r['sku'] == args.sku)])
        if not args.item and not args.sku:
            result.pop('rows')
    else:
        result = bind_latest(load(args.snapshot), root=args.root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        result = dict(output=str(args.output), sku_fact_source=result.get('sku_fact_source'),
                      price_version=result['resolved_price_version_sha256'], platform_write=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
