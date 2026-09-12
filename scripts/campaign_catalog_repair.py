"""Exact catalog facts: retired is not unmapped, and zero stock is not retired.

Old runs may receive a byte-pinned, snapshot-scoped supplement. No current price,
SKU identity, listing switch, or platform claim is changed by this module.
"""
import json
from copy import deepcopy


def documents(snapshot):
    from campaign_entry_authority import load,file_sha
    for ref in snapshot.get('catalog_repair_sources',[]):
        if file_sha(ref['path'])!=ref['sha256']:raise ValueError('catalog_repair_receipt_changed')
        doc=load(ref['path'])
        if (doc.get('schema')!='campaign_scoped_catalog_repair_v1'
                or doc.get('snapshot_captured_at')!=snapshot['captured_at']
                or doc.get('price_version')!=snapshot['resolved_price_version_sha256']):
            raise ValueError('catalog_repair_wrong_snapshot')
        for source in doc['sources']:
            if file_sha(source['path'])!=source['sha256']:raise ValueError('catalog_repair_source_changed')
        paths={source['path'] for source in doc['sources']}
        if doc['registry_path'] not in paths or doc['official_scope_path'] not in paths:
            raise ValueError('catalog_sources_not_bound')
        yield doc


def excluded_pairs(snapshot,identities):
    from campaign_entry_authority import load
    retired=set(snapshot.get('registered_delisted_sku_ids',[])); explicit=set()
    for doc in documents(snapshot):
        registry=load(doc['registry_path'])
        rows=[r for r in registry if r['code']=='delisted']
        if len(rows)!=1:raise ValueError('catalog_retirement_registry_missing')
        registered=set(json.loads(rows[0]['value_plain']))
        for row in doc['retired']:
            pair=row['item'],row['sku']
            if row['sku'] not in registered:raise ValueError('catalog_retirement_not_registered')
            explicit.add(pair)
    return {(r['item'],r['sku']) for r in identities if r['sku'] in retired or (r['item'],r['sku']) in explicit}


def mapped_rows(snapshot,rows):
    result=deepcopy(rows)
    for doc in documents(snapshot):
        from campaign_entry_authority import load
        scope=load(doc['official_scope_path'])['scope']
        if scope.get('complete') is not True:raise ValueError('catalog_complete_official_scope_required')
        facts={(e['facts']['item'],e['facts']['sku']):e['facts'] for e in scope['sku_facts']}
        for m in doc.get('restored',[]):
            fact=facts.get((m['item'],m['sku']))
            if (not fact or fact['sku_code']!=m['official_sku_code']
                    or m['official_sku_code']!=m['erp_code']+'|'
                    or m.get('repair_kind')!='verified_exact_trailing_delimiter'):
                raise ValueError('catalog_exact_mapping_proof_missing')
            candidates=[r for r in result if r['code']==m['erp_code'] and m['item'] in {
                str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}]
            if len(candidates)!=1:raise ValueError('catalog_repair_erp_identity_not_unique')
            existing=[r for r in result if m['sku'] in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
            if any(r['code']!=m['erp_code'] for r in existing):raise ValueError('catalog_repair_identity_conflict')
            candidates[0]['alt']=list(dict.fromkeys([*(candidates[0].get('alt') or []),m['sku']]))
    return result
