"""Full on-sale product export facts and unique ERP-code mapping.

No sales filter, stock-as-listing inference, price substitution, or SKU rotation.
The official export lacks a SKU enabled/disabled column; only the current
activity template determines the enabled signup SKU range.
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
