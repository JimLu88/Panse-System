"""Recognize only the 36 already proven cloud-sofa legacy identities."""
import hashlib
import json
from pathlib import Path
import re
from decimal import Decimal

HISTORY_SHA='e438ddd61e17e902e534d0322085d3a1ec91c2280130f456ff4966fe59bc4a64'


def load_bound(ref):
    raw=Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ref['sha256']:
        raise ValueError('legacy_spec_source_changed')
    return json.loads(raw)


def history(doc):
    refs=[r for r in doc['sources'] if r['sha256']==HISTORY_SHA]
    if len(refs)!=1:return None
    old=load_bound(refs[0])
    if old.get('schema')!='campaign_scoped_catalog_repair_v1':return None
    sources={r['path']:load_bound(r) for r in old['sources']}
    scope=sources.get(old['official_scope_path'],{}).get('scope',{})
    if scope.get('complete') is not True:return None
    facts={(e['facts']['item'],e['facts']['sku']):e['facts'] for e in scope['sku_facts']}
    return old,facts


def matches(mapping,fact,row,proof):
    if proof is None or row.get('custom') is not False:return False
    old,facts=proof
    if mapping not in old.get('restored',[]):return False
    pair=(mapping['item'],mapping['sku']);prior=facts.get(pair,{})
    if (row.get('product_code')!='PPS23160010401' or row.get('code')!=mapping['erp_code']
            or str(row.get('item'))!=pair[0] or str(row.get('sku'))!=pair[1]
            or fact.get('sku_code')!=mapping['official_sku_code']
            or mapping['erp_code']!='PPS'+mapping['official_sku_code']
            or any(prior.get(k)!=fact.get(k) for k in ('item','sku','sku_code','attributes'))):
        return False
    # This reviewed product's ERP labels express centimetres. No generic
    # name matcher, prefix-based lookup, new binding, or price mutation.
    official=re.fullmatch(r'适用人数:([^;]*?)(\d+(?:\.\d+)?)米;颜色分类:([^;]+);',fact.get('attributes',''))
    erp=re.fullmatch(r'2023款沙发-(\d+) ([^-]+)-(.+)',row.get('sku_name',''))
    if not official or not erp:return False
    if Decimal(official[2])*100!=Decimal(erp[1]) or official[3]!=erp[3]:return False
    return (official[1]==erp[2] or
            (official[1],erp[2],Decimal(erp[1]))==('双人位','双人半位',Decimal(200)))
