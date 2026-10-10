"""Exact physical-code correction after verified Taobao save; not SKU rotation.

Only existing mapping columns change. No SKU creation, price, order, inventory,
offer, baseline or global permission writes. Preview is mandatory before apply.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path('D:/AI/畔色ERP系统/outputs/campaign-new-three-20261010')
ITEMS = {'1090473184978', '1089439705938', '1089444961055'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def build_payload():
    plan = json.loads((ROOT/'rotation/automated-plan.json').read_text(encoding='utf-8'))
    terminal = json.loads((ROOT/'rotation/correction/terminal.json').read_text(encoding='utf-8'))
    if terminal['state'] != 'all_three_codes_corrected':
        raise ValueError('Taobao merchant codes not verified')
    snapshot = json.loads((ROOT/'snapshot.json').read_text(encoding='utf-8'))
    baseline = [r for r in snapshot['all_erp_rows'] if r.get('item') in ITEMS]
    changes = plan['mapping_conflicts']
    if len(baseline) != 24 or len(changes) != 14:
        raise ValueError('exact scope changed')
    new_by_code = {r['proposed_code_by_physical_variant']: r['sku'] for r in changes}
    for item in ITEMS:
        folder = ROOT/'rotation/correction'/item
        receipt = json.loads((folder/'terminal.json').read_text(encoding='utf-8'))
        actual = json.loads((folder/'after.json').read_text(encoding='utf-8'))
        expected = json.loads((folder/'expected.json').read_text(encoding='utf-8'))
        if receipt['state'] != 'published_verified' or actual != expected:
            # DOM runtime metadata can differ; compare the same business fields
            # as the publication program, not elapsed capture metadata.
            def values(rows):
                return [[[(f.get('value'),f.get('type')) for f in c.get('fields',[])] for c in r['cells']] for r in rows]
            if receipt['state'] != 'published_verified' or values(actual) != values(expected):
                raise ValueError('published values mismatch')
    return {'baseline': baseline, 'new_by_code': new_by_code,
            'platform_receipt_sha256': digest(terminal)}


def protected(state):
    result = json.loads(json.dumps(state, default=str))
    for r in result['promos']:
        r.pop('taobao_sku_id', None)
        r.pop('updated_at', None)
    for r in result['listings']:
        r.pop('sku_code', None)
        r.pop('updated_at', None)
    return result


def validate(state, payload, applied=False):
    baseline = {r['code']:r for r in payload['baseline']}
    promos = {r['sku_code']:r for r in state['promos']}
    prices = {r['sku_code']:r for r in state['prices']}
    if len(baseline)!=24 or set(promos)!=set(baseline) or set(prices)!=set(baseline):
        raise ValueError('exact ERP scope drift')
    from decimal import Decimal
    for code, row in baseline.items():
        promo=promos[code]
        expected=payload['new_by_code'].get(code,row['sku']) if applied else row['sku']
        if str(promo['taobao_item_id'])!=row['item'] or str(promo['taobao_sku_id'])!=expected or (promo.get('alt_taobao_sku_ids') or [])!=row['alt']:
            raise ValueError('ERP mapping drift:'+code)
        for value, frozen in ((prices[code]['daily_price'],row['daily']),
                              (promo.get('mid_buyer_price'),row['medium_target']),
                              (promo.get('big_buyer_price'),row['big_target'])):
            if (None if value is None else Decimal(str(value))) != (None if frozen is None else Decimal(str(frozen))):
                raise ValueError('ERP price drift:'+code)
        if code in payload['new_by_code'] and (promo.get('coupon_floor_price') is not None or promo.get('enrolled_floor_price') is not None):
            raise ValueError('physical historical floor needs explicit reassociation:'+code)
    old_owner={r['sku']:r['code'] for r in baseline.values()}
    new_owner={payload['new_by_code'].get(r['code'],r['sku']):r['code'] for r in baseline.values()}
    if len(new_owner)!=24 or set(new_owner)!=set(old_owner):
        raise ValueError('not a closed existing SKU permutation')
    for listing in state['listings']:
        sid=str(listing['taobao_sku_id'])
        if sid not in old_owner: continue
        owner=(new_owner if applied else old_owner)[sid]
        if listing.get('sku_code')!=owner or str(listing['taobao_item_id'])!=baseline[owner]['item']:
            raise ValueError('listing mapping drift:'+sid)


def remote(mode, payload, expected):
    from sqlalchemy import text
    from app.database import SessionLocal
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE'))
        codes=list(r['code'] for r in payload['baseline'])
        ids=list(ITEMS)
        def inspect():
            def rows(table, column, values):
                return [dict(r[0]) for r in db.execute(text('SELECT to_jsonb(t) FROM '+table+' t WHERE '+column+' = ANY(:values) ORDER BY id FOR UPDATE'),{'values':values})]
            return {'prices':rows('pricing_sku','sku_code',codes),
                    'promos':rows('pricing_sku_promo','sku_code',codes),
                    'listings':rows('taobao_listings','taobao_item_id',ids)}
        before=inspect()
        validate(before,payload)
        if mode=='preview': return {'state':'preview','sha256':digest(before),'before':before}
        if mode!='apply' or not expected or expected!=digest(before):
            raise ValueError('fresh preview required')
        by_sid={r['sku']:r for r in payload['baseline']}
        for code,sid in payload['new_by_code'].items():
            db.execute(text('UPDATE pricing_sku_promo SET taobao_sku_id=:sid WHERE sku_code=:code'),{'sid':sid,'code':code})
            db.execute(text('UPDATE taobao_listings SET sku_code=:code WHERE taobao_item_id=:item AND taobao_sku_id=:sid'),{'code':code,'item':by_sid[sid]['item'],'sid':sid})
        after=inspect()
        validate(after,payload,applied=True)
        if protected(before)!=protected(after): raise ValueError('protected fields changed')
        db.commit()
        readback=inspect()
        validate(readback,payload,applied=True)
        if protected(before)!=protected(readback): raise ValueError('commit readback mismatch')
        return {'state':'mapping_corrected_verified','changed':14,'before':before,'after':readback,
                'erp_prices_unchanged':True,'orders_written':False,'rotation_enabled_changed':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['preview','apply'])
    p.add_argument('--expected')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    payload=build_payload()
    source=Path(__file__).read_text(encoding='utf-8')
    source+='\nprint(json.dumps(remote('+repr(args.mode)+','+repr(payload)+','+repr(args.expected)+'),ensure_ascii=False,default=str))\n'
    source="__name__='remote_mapping_correction'\n"+source
    command=['C:/Program Files/Git/usr/bin/ssh.exe','-i',str(Path.home()/'.ssh/panse_nas'),'-o','BatchMode=yes','-o','ConnectTimeout=15','-p','2222','15068803006@DS923plus','sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as f:
        run=subprocess.run(command,input=source,text=True,encoding='utf-8',capture_output=True,timeout=60)
        if run.returncode: raise RuntimeError('failed or unknown; never auto-replay: '+run.stderr[-1500:])
        result=json.loads(run.stdout)
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k not in ('before','after')},ensure_ascii=False))


if __name__=='__main__': main()
