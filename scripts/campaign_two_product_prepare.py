"""One explicitly authorized, local-only recovery supplement. No transport code.

Keeps existing activity prices, adds four exact blank SKU prices, and emits
only two missing discounts. Shared recovery reservation prevents regeneration.
Uses the established byte-preserving official-template adapter (custom XML).
"""
import argparse
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import re

from campaign_discount_template import load_fixed_discount_template
from campaign_fixed_template_projection import project
from campaign_official_template import fill_selected_rows, fill_single_discount_rows
from campaign_recovery_trial import MASTER, MASTER_SHA, LEDGER, CAMPAIGN, reserve, write_new

ROOT = Path('D:/AI/畔色ERP系统')
SOURCE = ROOT/'outputs/01a067c6-7e83-7483-9a21-84b44ed7299b'
PINS = {
 'rotation-20260928/verified-scope.json':'2a1deea7a402be522d3af686fb088d919beac97a4739780bb22ac258631846fb',
 'consolidated-20260929/erp-snapshot.json':'3cd63f67f0e48bc2ede0736af5acbbaf79b2a791dfb5a4c15399321452944b82',
 'national-closeout-20260929/four-status-receipt.json':'84733e511505c73a988c2c84f73235c8cafb1d0724d3c4ba0706c74b1fed7cb6',
 'national-closeout-20260929/completed-readbacks.json':'8cd590aeaf69a78083ac70ad78fd7d4a436b8aeb60d6007577fdbdf6e5e06576',
}
ITEMS = {'793202812082':12,'722275846168':6}
NEW = {'6137591788516':'1500.00','6137591788517':'2000.00',
       '6299575587331':'3375.00','6301271146899':'3472.50'}
DEDUCT = {'6299575587331':'934.96','6301271146899':'959.40'}
END = '2026-10-07 19:59:59'
TZ = timezone(timedelta(hours=8))

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def sources():
    result={}
    for name,digest in PINS.items():
        raw=(SOURCE/name).read_bytes()
        if sha(raw)!=digest: raise ValueError('source_changed:'+name)
        result[name]=json.loads(raw.decode('utf-8-sig'))
    return result

def validate(data, start, now=None):
    now=now or datetime.now(TZ)
    begin=datetime.strptime(start,'%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ)
    end=datetime.strptime(END,'%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ)
    if not now < begin < end: raise ValueError('future_unexpired_window_required')
    scope,erp,official,discounts=(data[n] for n in PINS)
    if official['job']['state']!='finished' or official['platform_write'] is not False:
        raise ValueError('official_terminal_required')
    rows=official['job']['result']['rows']
    if {r['item'] for r in rows}!=set(ITEMS) or len(rows)!=2:
        raise ValueError('exact_two_product_scope_required')
    active={r['item']:r for r in discounts['active_offers'] if r['item'] in ITEMS}
    if set(active)!=set(ITEMS): raise ValueError('discount_evidence_missing')
    facts={(r['facts']['item'],r['facts']['sku']):r for r in scope['sku_facts']}
    all_rows=[]; extra=[]; records={}; seen=set(); blanks=set(); protected=0
    for item in rows:
        iid=item['item']; live=[r for r in item['list_rows'] if '异常' in r['cells']]
        if len(live)!=1 or '出售中' not in live[0]['cells'][0]:
            raise ValueError('exact_on_sale_abnormal_record_required')
        mids=re.findall(r'营销ID\s*(\d+)',live[0]['cells'][0])
        if len(mids)!=1: raise ValueError('marketing_identity_missing')
        records[iid]=mids[0]
        offer=active[iid]
        if (offer['offer_id']!='147717819883' or offer['status']!='进行中'
            or offer.get('sku_amounts_verified') is not True or offer['end']!=END
            or offer['start']>start): raise ValueError('protected_discount_changed')
        inventory=item['editor_inventory']['rows']
        if len(inventory)!=ITEMS[iid]: raise ValueError('incomplete_sku_scope')
        for row in inventory:
            sid=row['sku_id'];pair=(iid,sid)
            if pair in seen or pair not in facts: raise ValueError('sku_duplicate_or_not_exported')
            seen.add(pair)
            matches=[r for r in erp['all_erp_rows'] if r['item']==iid and sid in [r.get('sku'),*(r.get('alt') or [])]]
            if len(matches)!=1: raise ValueError('erp_identity_not_unique')
            fact=matches[0]; old=row['inputs'][0]['value']
            if old:
                price=Decimal(old)
            else:
                blanks.add(sid)
                if sid not in NEW or Decimal(fact['daily'])!=Decimal(NEW[sid]):
                    raise ValueError('blank_sku_daily_or_identity_changed')
                price=Decimal(fact['daily'])
            if not price.is_finite() or price<=0 or price!=price.quantize(Decimal('.01')):
                raise ValueError('invalid_price')
            cut=(price*Decimal('.10')).to_integral_value(rounding=ROUND_CEILING)
            old_d=Decimal(offer['values'].get(sid,'0'));new_d=Decimal(DEDUCT.get(sid,'0'))
            if sid in offer['values']: protected+=1
            if new_d:
                if old_d or sid not in offer['not_enrolled'] or old or fact['custom']:
                    raise ValueError('discount_replay_or_unproved_missing')
                extra.append({'item':iid,'sku':sid,'deduct':format(new_d,'.2f')})
            final=price-cut-old_d-new_d
            if not fact['custom'] and abs(final-Decimal(fact['medium_target']))>2:
                raise ValueError('original_target_tolerance_exceeded')
            all_rows.append({'item':iid,'sku':sid,'activity_price':format(price,'.2f'),
                'preserved':bool(old),'erp_code':fact['code'],'custom':fact['custom'],
                'existing_discount':str(old_d),'new_discount':str(new_d),'final':str(final)})
    if blanks!=set(NEW) or protected!=10 or len(extra)!=2 or len(all_rows)!=18:
        raise ValueError('exact_14_preserved_4_new_10_protected_2_discount_required')
    selected=dict(scope,sku_facts=[facts[pair] for pair in sorted(seen)])
    return all_rows,extra,records,selected

def prepare(output, start, ledger=LEDGER):
    output=Path(output)
    if output.exists(): raise ValueError('output_exists_no_overwrite')
    data=sources()
    rows,discounts,records,scope=validate(data,start)
    raw=MASTER.read_bytes()
    if sha(raw)!=MASTER_SHA: raise ValueError('official_master_changed')
    signup=fill_selected_rows(project(raw,scope,ITEMS),rows,official_rate=Decimal('.10'))
    discount=fill_single_discount_rows(load_fixed_discount_template(),discounts)
    request={'schema':'two_product_recovery_supplement_20261002_v1','campaign':CAMPAIGN,
       'marketing_records':records,'scope':rows,'source_pins':PINS,'window':{'start':start,'end':END},
       'user_authorization':'03 relayed explicit user: exclude cabinet, continue remaining two products; user uploads only',
       'platform_write':False,'automatic_retry':False,'discount_rows':discounts}
    root,initial=reserve(request,sha(json.dumps(request,sort_keys=True).encode()),rows,ledger)
    output.mkdir(parents=True)
    files=[]
    for name,content in [('单品立减-剩余两件合并.xlsx',discount),('超级立减-剩余两件合并.xlsx',signup)]:
        path=output/name
        with path.open('xb') as stream: stream.write(content)
        files.append({'path':str(path.resolve()),'sha256':sha(content)})
    result=dict(initial,state='prepared_not_submitted',files=files,window=request['window'],
        sku_count=18,discount_rows=discounts,protected_discount_count=10,preserved_price_count=14,
        completed_business=False,platform_write=False,automatic_upload=False,
        trial_dir=str(root),source_as_of='2026-09-29',
        note='Upload discount once in stated window, then activity once. No platform outcome is claimed; preserve terminal evidence and do not replay unknown.')
    write_new(root/'prepared.json',result)
    write_new(output/'receipt.json',result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--start',required=True)
    args=p.parse_args();print(json.dumps(prepare(args.output,args.start),ensure_ascii=False,indent=2))
