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
        if doc.get('recorded_state'):
            from campaign_recorded_sku_state import verified_disabled
            explicit.update(verified_disabled(doc))
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
            kind=m.get('repair_kind')
            suffix=(kind=='verified_exact_trailing_delimiter' and m['official_sku_code']==m['erp_code']+'|')
            legacy=(kind=='verified_bound_legacy_prefix' and m['official_sku_code'].isdigit()
                    and m['erp_code']=='PPS'+m['official_sku_code'])
            if not fact or fact['sku_code']!=m['official_sku_code'] or not (suffix or legacy):
                raise ValueError('catalog_exact_mapping_proof_missing')
            candidates=[r for r in result if r['code']==m['erp_code'] and m['item'] in {
                str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}]
            if len(candidates)!=1:raise ValueError('catalog_repair_erp_identity_not_unique')
            existing=[r for r in result if m['sku'] in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
            if any(r['code']!=m['erp_code'] for r in existing):raise ValueError('catalog_repair_identity_conflict')
            if legacy:
                # Existing exact physical binding + exact specification. This
                # only recognizes a proven legacy spelling, never maps an
                # unknown SKU by adding a guessed prefix or changing prices.
                if (len(existing)!=1 or existing[0]['code']!=m['erp_code']
                        or not fact.get('attributes')
                        or fact['attributes'].strip()!=existing[0].get('sku_name','').strip()
                        or existing[0].get('custom') is not False):
                    raise ValueError('catalog_legacy_physical_or_spec_not_proven')
                continue
            candidates[0]['alt']=list(dict.fromkeys([*(candidates[0].get('alt') or []),m['sku']]))
    return result


def remaining_mapping_summary(snapshot,exceptions):
    """Projection only: preserve historical controller exceptions verbatim."""
    rows=mapped_rows(snapshot,snapshot['all_erp_rows']);products=[]
    for item,issues in exceptions.items():
        original=[d['sku'] for d in issues if d.get('reason')=='erp_mapping_missing_or_not_unique' and d.get('sku')]
        if not original:continue
        pairs=[dict(item=item,sku=s) for s in original];removed=excluded_pairs(snapshot,pairs)
        unresolved=[];resolved=[]
        for sku in dict.fromkeys(original):
            matches=[r for r in rows if item in {str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}
                and sku in {str(r.get('sku')),*map(str,r.get('alt') or [])}]
            if (item,sku) in removed or len(matches)==1:resolved.append(sku)
            else:unresolved.append(sku)
        products.append(dict(item=item,historical_sku_count=len(original),resolved_skus=resolved,
                             remaining_skus=unresolved,remaining_sku_count=len(unresolved)))
    return dict(products=products,historical_sku_count=sum(p['historical_sku_count'] for p in products),
                remaining_sku_count=sum(p['remaining_sku_count'] for p in products),controller_modified=False)
