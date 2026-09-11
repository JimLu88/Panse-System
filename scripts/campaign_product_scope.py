"""Full on-sale product export facts and unique ERP-code mapping.

No sales filter, stock-as-listing inference, price substitution, or SKU rotation.
The official export lacks a SKU enabled/disabled column. A pinned activity
template is not current enabled-state evidence; never infer that from stock.
"""
from collections import defaultdict
import hashlib

from campaign_official_template import read_rows


def parse_export(raw):
    rows = read_rows(raw,'发布模板')
    candidates=[(n,c) for n,c in rows.items() if '商品Id' in c.values() and 'skuId' in c.values() and '价格(元)' in c.values()]
    if len(candidates)!=1:
        raise ValueError('official_product_export_schema_changed')
    header_n,header=candidates[0]
    def column(name):
        matches=[c for c,v in header.items() if v==name]
        if len(matches)!=1:raise ValueError('export_header_not_unique:'+name)
        return matches[0]
    item_col,sku_col=column('商品Id'),column('skuId')
    # The two 商家编码 columns have different meanings. Bind the SKU code to
    # the SKU block, never use the product's code for every variant.
    codes=[c for c,v in header.items() if v=='商家编码' and len(c)==1 and ord(c)>ord(sku_col)]
    if len(codes)!=1:raise ValueError('sku_merchant_code_column_ambiguous')
    columns={'item':item_col,'sku':sku_col,'sku_code':codes[0],
             'price':column('价格(元)'),'stock':column('库存(件)'), 'attributes':column('销售属性')}
    result,current=[],None
    seen=set()
    for n,cells in sorted(rows.items()):
        if n<=header_n:continue
        values={k:cells.get(c,'').strip() for k,c in columns.items()}
        if not any(values.values()):continue
        if values['item']:
            current=values['item']
        if not str(current or '').isdigit() or not values['sku'].isdigit():
            raise ValueError('export_item_or_sku_identity_invalid')
        values['item']=current
        pair=current,values['sku']
        if pair in seen:raise ValueError('duplicate_export_pair')
        seen.add(pair)
        result.append(dict(values,sheet='发布模板',row=n))
    return result


def complete_scope(files, *, observed_item_ids, observed_page_count, observed_total, page_evidence):
    ids=list(map(str,observed_item_ids))
    if (not page_evidence or type(observed_total) is not int or observed_total<0
            or type(observed_page_count) is not int or observed_page_count<1
            or len(set(ids))!=len(ids) or len(ids)!=observed_total
            or not all(i.isdigit() for i in ids)):
        raise ValueError('full_on_sale_page_scope_not_proven')
    merged,provenance={},[]
    for raw,expected_sha in files:
        actual=hashlib.sha256(raw).hexdigest()
        if actual!=expected_sha:raise ValueError('official_product_export_changed')
        provenance.append(actual)
        for row in parse_export(raw):
            key=row['item'],row['sku']
            fact={k:v for k,v in row.items() if k not in ('sheet','row')}
            if key in merged and merged[key]['facts']!=fact:
                raise ValueError('conflicting_page_export_pair')
            entry=merged.setdefault(key,{'facts':fact,'sources':[]})
            entry['sources'].append({'sha256':actual,'sheet':row['sheet'],'row':row['row']})
    if {i for i,s in merged}!=set(ids):
        raise ValueError('download_does_not_cover_current_on_sale_products')
    return {'complete':True,'observed_item_count':observed_total,'page_count':observed_page_count,
            'platform_rows':[{'item':i,'on_sale':True} for i in sorted(ids)],
            'sku_facts':list(merged.values()),'source_sha256':provenance,'page_evidence':page_evidence,
            'sku_enabled_state_inferred_from_stock':False}


def unique_mappings(scope, erp_rows):
    if scope.get('complete') is not True:raise ValueError('complete_export_required')
    by_code=defaultdict(list)
    for row in erp_rows:by_code[row['code']].append(row)
    matches,unknown=[],[]
    for entry in scope['sku_facts']:
        fact=entry['facts']; candidates=by_code[fact['sku_code']]
        compatible=[r for r in candidates if fact['item'] in {
            str(r.get('item')),str(r.get('product_item_id')),*map(str,r.get('product_alt_item_ids') or [])}]
        if len(candidates)==1 and len(compatible)==1:
            matches.append({'item':fact['item'],'sku':fact['sku'],'erp_code':compatible[0]['code'],
                            'sources':entry['sources']})
        else:
            unknown.append({'item':fact['item'],'sku':fact['sku'],'sku_code':fact['sku_code'],
                            'reason':'exact_code_and_product_mapping_missing_or_ambiguous'})
    return {'matches':matches,'unknown':unknown,'price_changes':False,'database_write':False}


def template_scope_issues(scope, template_rows, items):
    """Compare the already downloaded facts in memory; never fetch/preflight.

    A pinned template is a package/layout source, not a current SKU registry.
    Stock zero does not mean disabled. Differences therefore remain explicit
    per-product input issues until an exact enabled-SKU fact resolves them.
    This check does not claim to prove enabled state when the export lacks it.
    """
    if scope.get('complete') is not True or not scope.get('page_evidence'):
        raise ValueError('complete_product_scope_evidence_required')
    wanted=set(map(str,items));current=defaultdict(set);template=defaultdict(set)
    for entry in scope.get('sku_facts',[]):
        fact=entry['facts'];item,sku=str(fact['item']),str(fact['sku'])
        if item in wanted:
            if sku in current[item]:raise ValueError('duplicate_current_export_sku')
            current[item].add(sku)
    for row in template_rows:
        item,sku=str(row['item']),str(row['sku'])
        if item in wanted:
            if sku in template[item]:raise ValueError('duplicate_template_sku')
            template[item].add(sku)
    issues=[]
    for item in sorted(wanted):
        if not current[item]:
            issues.append(dict(item=item,sku='',error='selected_product_missing_from_current_export'))
            continue
        for sku in sorted(current[item]-template[item]):
            issues.append(dict(item=item,sku=sku,error='current_sku_missing_from_fixed_template',
                               enabled_state='unknown',requires_rotation=False))
        for sku in sorted(template[item]-current[item]):
            issues.append(dict(item=item,sku=sku,error='fixed_template_sku_missing_from_current_export',
                               enabled_state='unknown',requires_rotation=False))
    return issues


def from_edge_job(job, *, expected_request_id, expected_shop, roots):
    """Bound export receipt -> verified scope, never trust HTTP/download alone."""
    import json
    from pathlib import Path
    from campaign_continuous_policy import fingerprint
    if (job.get('operation')!='product_export' or job.get('state')!='finished'
            or job.get('job_id')!=fingerprint(['product_export',expected_shop,expected_request_id])):
        raise ValueError('exact_product_export_job_not_finished')
    result=job.get('result') or {}
    if (result.get('state')!='downloaded' or result.get('snapshot_request_id')!=expected_request_id
            or result.get('shop_name')!=expected_shop):
        raise ValueError('product_export_scope_request_mismatch')
    allowed=[Path(p).resolve(strict=True) for p in roots]
    def resolve(value, suffix):
        path=Path(value).resolve(strict=True)
        if not path.is_file() or path.suffix.lower()!=suffix or not any(path.is_relative_to(root) for root in allowed):
            raise ValueError('product_export_evidence_outside_configured_roots')
        return path
    evidence_path=resolve(result['evidence_path'],'.json')
    evidence=json.loads(evidence_path.read_text(encoding='utf-8'))
    keys=('state','files','observed_item_ids','observed_total','page_count','snapshot_request_id','shop_name')
    if any(result.get(k)!=evidence.get(k) for k in keys):
        raise ValueError('product_export_observation_changed')
    if (type(result['page_count']) is not int or result['page_count']<1
            or len(result['files'])!=result['page_count']):
        raise ValueError('product_export_file_page_count_mismatch')
    files=[];seen=set();record_ids=set()
    for index,entry in enumerate(result['files'],start=1):
        scope,record=entry['scope'],entry['record']
        ids=scope['item_ids'];record_id=str(record['id'])
        if (scope.get('on_sale') is not True or scope['page']!=index
                or scope['page_count']!=result['page_count'] or scope['total']!=result['observed_total']
                or len(ids)!=len(set(ids)) or seen.intersection(ids)
                or not record_id.isdigit() or record_id in record_ids or record['rowCount']!=len(ids)):
            raise ValueError('product_export_page_or_record_scope_mismatch')
        raw=resolve(entry['path'],'.xlsx').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:
            raise ValueError('official_product_export_changed')
        if {r['item'] for r in parse_export(raw)}!=set(ids):
            raise ValueError('official_export_file_does_not_match_selected_page')
        seen.update(ids);record_ids.add(record_id);files.append((raw,entry['sha256']))
    if seen!=set(result['observed_item_ids']):
        raise ValueError('product_export_combined_page_scope_mismatch')
    return complete_scope(files,observed_item_ids=result['observed_item_ids'],
        observed_page_count=result['page_count'],observed_total=result['observed_total'],
        page_evidence={'path':str(evidence_path),'sha256':hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                       'job_id':job['job_id'],'snapshot_request_id':expected_request_id})
