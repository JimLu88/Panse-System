"""Carry an already verified exact code alias into a new complete export.

Reuses the existing export, never fetches pages, invents aliases, changes prices
or retries enrollment. Old snapshot-specific receipts remain immutable.
"""
from copy import deepcopy
from pathlib import Path
import json

from campaign_entry_authority import file_sha, load
from campaign_catalog_repair import mapped_rows


def persist(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if load(path) != document:
            raise ValueError('catalog_refresh_evidence_conflict')
    else:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
    return str(path.resolve())


def refresh(authority, snapshot, scope, output):
    if scope.get('complete') is not True:
        raise ValueError('catalog_refresh_full_scope_required')
    facts = {(r['facts']['item'], r['facts']['sku']): r['facts'] for r in scope['sku_facts']}
    if len(facts) != len(scope['sku_facts']):
        raise ValueError('catalog_refresh_duplicate_fact')
    candidates = {}
    for source in authority.sources():
        doc = source['document']
        if source['kind'] != 'catalog' or doc.get('schema') != 'campaign_scoped_catalog_repair_v1':
            continue
        for row in doc.get('restored', []):
            if row.get('repair_kind') != 'verified_exact_trailing_delimiter':
                continue
            pair = row['item'], row['sku']
            fact = facts.get(pair)
            if not fact or fact['sku_code'] != row['official_sku_code']:
                continue
            if row['official_sku_code'] != row['erp_code'] + '|':
                raise ValueError('catalog_refresh_not_exact_prior_alias')
            current = [r for r in snapshot['all_erp_rows'] if r['code'] == row['erp_code']
                       and row['item'] in {str(r.get('item')), str(r.get('product_item_id')),
                                          *map(str, r.get('product_alt_item_ids') or [])}]
            if len(current) != 1:
                raise ValueError('catalog_refresh_current_mapping_not_unique')
            if pair in candidates and candidates[pair][0] != row:
                raise ValueError('catalog_refresh_prior_alias_conflict')
            candidates.setdefault(pair, (row, source, doc))
    if not candidates:
        return snapshot, None
    original_rows = deepcopy(snapshot['all_erp_rows'])
    selected = list(candidates.values())
    registry = selected[0][2]['registry_path']
    references = {s['path']: s['sha256'] for s in selected[0][2]['sources']}
    if references.get(registry) != file_sha(registry):
        raise ValueError('catalog_refresh_registry_changed')
    scope_path = persist(Path(output)/'verified-scope.json', dict(scope=scope))
    refs = {scope_path: file_sha(scope_path), registry: file_sha(registry)}
    for _, source, _ in selected:
        refs[source['path']] = source['sha256']
    doc = dict(schema='campaign_scoped_catalog_repair_v1',
               snapshot_captured_at=snapshot['captured_at'],
               price_version=snapshot['resolved_price_version_sha256'],
               registry_path=registry, official_scope_path=scope_path,
               sources=[dict(path=p, sha256=h) for p,h in sorted(refs.items())],
               retired=[], restored=[r for r,_,_ in selected],
               scope='Previously verified exact trailing delimiter aliases only; new complete export; no price or listing changes.')
    path = persist(Path(output)/'catalog-alias-refresh.json', doc)
    bound = dict(snapshot, catalog_repair_sources=[dict(path=path,sha256=file_sha(path))])
    checked = mapped_rows(bound, snapshot['all_erp_rows'])
    if snapshot['all_erp_rows'] != original_rows:
        raise ValueError('catalog_refresh_mutated_price_snapshot')
    authority.register_source(path, 'catalog', file_sha(path))
    resolved = authority.resolve_snapshot(snapshot)
    if (resolved['all_erp_rows'] != original_rows
            or resolved['resolved_price_version_sha256'] != snapshot['resolved_price_version_sha256']):
        raise ValueError('catalog_refresh_changed_price_version')
    return resolved, dict(path=path, sha256=file_sha(path), mapped_pairs=sorted(candidates),
                         platform_write=False, price_changes=False)


def main():
    import argparse
    from campaign_entry_authority import Authority
    from campaign_product_scope import from_edge_job
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', required=True)
    p.add_argument('--scope', required=True)
    p.add_argument('--output', required=True)
    args=p.parse_args()
    scope=load(args.scope);proof=scope['page_evidence']
    if file_sha(proof['path']) != proof['sha256']:
        raise ValueError('catalog_refresh_export_receipt_changed')
    result=load(proof['path'])
    verified=from_edge_job(dict(operation='product_export',state='finished',job_id=proof['job_id'],
                                 result=dict(result,evidence_path=proof['path'])),
        expected_request_id=proof['snapshot_request_id'],expected_shop=result['shop_name'],
        roots=[Path(proof['path']).parent])
    if verified != scope:
        raise ValueError('catalog_refresh_scope_changed')
    a=Authority()
    try:
        snapshot=a.resolve_snapshot(load(args.snapshot))
        resolved,receipt=refresh(a,snapshot,verified,args.output)
        print(json.dumps(dict(receipt=receipt,price_version_unchanged=resolved['resolved_price_version_sha256']==snapshot['resolved_price_version_sha256'],
                              platform_write=False),ensure_ascii=False))
    finally:a.close()


if __name__=='__main__':main()
