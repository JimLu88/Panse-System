"""Prepare-only batch cap analysis and fixed read request. Never submits a job or upload."""
import argparse
from collections import Counter
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from campaign_generate_current_files import build_rows


def analyze(snapshot, inventory, scope):
    if not scope or len(scope)!=len(set(scope)):raise ValueError('exact_unique_scope_required')
    selected=[r for r in inventory['rows'] if r['item'] in scope]
    pairs=[(r['item'],r['sku']) for r in selected]
    if len(pairs)!=len(set(pairs)):raise ValueError('multiple_current_records_for_sku')
    if {r['item'] for r in selected}!=set(scope):raise ValueError('inventory_scope_incomplete')
    caps={(r['item'],r['sku']):r.get('min_final') for r in selected}
    activity, discounts, issues=build_rows(snapshot,selected,Decimal('.10'),'medium',{},
                                           signup_items=set(),discount_items=set(scope),platform_caps=caps)
    return dict(items=scope, source_skus=len(selected), ordinary_rows=discounts, issues=issues,
                counts=dict(Counter(i['error'] for i in issues)), source_as_of=inventory['as_of'],
                activity_rows=activity, platform_write=False, database_write=False, upload_ready=False,
                note='Amounts are proposed only. Existing offers require exact full-window readback; do not stack or replay.')


def main():
    import sys
    if sys.argv[1:2] == ['recovery-trial']:
        from campaign_recovery_trial import main as recovery_trial
        return recovery_trial(sys.argv[2:])
    if sys.argv[1:2] == ['audit-current']:
        from campaign_official_current import main as audit_current
        return audit_current(sys.argv[2:])
    if sys.argv[1:2] == ['prepare']:
        from campaign_cap_prepare import main as prepare
        return prepare(sys.argv[2:])
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('snapshot','inventory','scope','identity-receipt','output-dir'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    if a.output_dir.exists():raise ValueError('output_exists_do_not_overwrite')
    def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))
    inv,snapshot,scope_doc,source_request=map(load,(a.inventory,a.snapshot,a.scope,a.identity_receipt))
    source=Path(inv['source']['path'])
    if hashlib.sha256(source.read_bytes()).hexdigest()!=inv['source']['sha256']:raise ValueError('official_inventory_file_changed')
    scope=scope_doc['scope']
    result=analyze(snapshot,inv,scope)
    request=source_request['payload']
    payload=dict(identity=request['identity'],price_window=request['price_window'],items=scope,
                 sku_scope={i:[r['sku'] for r in inv['rows'] if r['item']==i] for i in scope},batch_read=True)
    payload['read_request_id']=hashlib.sha256(json.dumps(dict(payload=payload,inventory_sha256=hashlib.sha256(a.inventory.read_bytes()).hexdigest()),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    result['sources']=[dict(path=str(f.resolve()),sha256=hashlib.sha256(f.read_bytes()).hexdigest())
                       for f in (a.snapshot,a.inventory,a.scope,a.identity_receipt)]
    a.output_dir.mkdir(parents=True)
    for name,data in [('cap-analysis.json',result),('batch-read-request.json',dict(step='discount_item_discovery',payload=payload))]:
        with (a.output_dir/name).open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
    print(json.dumps(dict(items=len(scope),source_skus=result['source_skus'],ordinary_rows=len(result['ordinary_rows']),
                         issues=result['counts'],platform_write=False,upload_ready=False),ensure_ascii=False))


if __name__=='__main__':main()
