"""Extract exact reported failed custom identities, not every SKU in failed items."""
import argparse,json,re
from pathlib import Path
from collections import Counter
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path)
parser.add_argument('--output', type=Path, required=True)
args=parser.parse_args()
src=json.loads(args.source.read_text(encoding='utf-8-sig'))
items={}; unresolved=[]; normals=[]
def add(item,row,campaign,reason):
    key=(item,row['sku'])
    erp=row.get('erp',row)
    if not erp['custom']:
        normals.append(dict(item=item,sku=row['sku'],name=row.get('name'),campaign=campaign,reason=reason))
        return
    entry=items.setdefault(key,dict(item=item,sku=row['sku'],name=row.get('name'),erp_code=erp.get('erp_code'),current_signup=row.get('current_signup'),campaigns=[],evidence=[],custom=True,classification_verified=True,failed_current_scope=True,basis_status='not_loaded',fixed_original=None,fixed_floor=None,rotation_need='unknown',existing_reserve='unknown',missing_reserve_count=None))
    if campaign not in entry['campaigns']: entry['campaigns'].append(campaign)
    entry['evidence'].append(dict(reason, campaign=campaign))
for r in src['super_reduce']['approved_price_caps']:
    add(r['item'],r,'super_reduce',dict(type='strict_approved_cap',cap=r['platform_strict_less_than'],source='official report exact skuId'))

def parts(name):
    return sorted(t.strip() for t in re.split('[,，;；]',name or '') if t.strip())
def parse_group(g,campaign,rows):
    reason=g['reason_verbatim']
    mentioned=[]
    for m in re.finditer(r'\[([^\[\]]+?)（活动普惠券后价：([\d.]+)元，最低普惠券后价：([\d.]+)元',reason):
        mentioned.append((m[1],dict(type='coupon_cap',platform_final=m[2],cap=m[3])))
    for m in re.finditer(r'您的sku：(.*?) 在管控期标价为([\d.]+)元',reason):
        mentioned.append((m[1],dict(type='list_cap',cap=m[2])))
    for name,evidence in mentioned:
        matches=[r for r in rows if parts(r['name'])==parts(name)]
        if len(matches)!=1:
            unresolved.append(dict(item=g['item'],campaign=campaign,reported_name=name,matching_rows=len(matches),evidence=evidence))
        else:
            evidence['reported_name']=name
            add(g['item'],matches[0],campaign,evidence)
    if not mentioned:
        unresolved.append(dict(item=g['item'],campaign=campaign,error='no_complete_price_mention_extracted',reason=reason))
    if reason and not (reason.endswith('等') or reason.endswith(';') or reason.endswith('；') or reason.endswith('。')):
        unresolved.append(dict(item=g['item'],campaign=campaign,error='report_text_may_be_truncated_do_not_claim_complete'))
for g in src['autumn']['groups']:
    if g['category']=='price_decision': parse_group(g,'autumn',g['rows'])
sr=json.loads(Path(src['source_files']['super_reduce']).read_text(encoding='utf-8'))
for g in sr['groups']:
    if 'no_sales' not in g['categories'] and 'approved_price_cap' not in g['categories']:
        rows=[dict(sku=r['sku'],name=r['sku_name'],current_signup=r['submitted_price'],erp=r['erp']) for r in g['sku_rows']]
        parse_group(g,'super_reduce',rows)
result=dict(status='initial_report_identity_inventory_not_live_spare_count',custom_affected_count=len(items),custom_affected_products=len({i for i,s in items}),rows=list(items.values()),normal_mentions=normals,unresolved=unresolved,report_only=True,platform_write=False,notes=['Exclude only per-campaign official no-sales failures.','Matching report names uses full attribute tokens within same product; never used to infer reserve mappings.','Missing spare count remains unknown until fixed baseline and exact old/new reserve evidence are verified.'])
out=args.output
with out.open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
print(json.dumps({k:v for k,v in result.items() if k not in ('rows','normal_mentions')},ensure_ascii=False))
