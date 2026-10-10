"""Project verified exact-three corrections without rewriting historical offers."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path

ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-new-three-20261010/rotation/correction/discount-correction')
ITEMS={'1090473184978','1089439705938','1089444961055'}
WINDOWS={'super':('150271245168','2026-10-13 00:00:00','2026-10-20 19:59:59'),
 'double11':('150177186781','2026-10-20 20:00:00','2026-11-13 23:59:59')}

def apply_entries(offers,entries):
    result=deepcopy(offers);seen=set()
    for entry in entries:
        name,item=entry['campaign_name'],entry['item'];key=(name,item)
        if key in seen or item not in ITEMS or name not in WINDOWS:raise ValueError('discount overlay exact scope required')
        seen.add(key);oid,start,end=WINDOWS[name]
        if entry['state']!='verified_saved' or tuple(entry[k] for k in ('offer_id','start','end'))!=(oid,start,end):raise ValueError('discount correction not verified')
        after=entry['after'];wanted=entry['wanted']
        if tuple(after['window'].get(k) for k in ('offer_id','start','end'))!=(oid,start,end) or after['item']!=item:raise ValueError('discount correction window mismatch')
        if {s:Decimal(v) for s,v in after['values'].items()}!={s:Decimal(v) for s,v in wanted.items()}:raise ValueError('discount correction amount mismatch')
        if len(wanted)!=(9 if item=='1090473184978' else 6):raise ValueError('discount correction ordinary scope changed')
        matches=[o for o in result if o.get('platform_offer_id',o['offer_id'])==oid and (o['start'],o['end'])==(start,end)]
        if len(matches)!=1:raise ValueError('discount parent not unique')
        offer=matches[0]
        if offer.get('partial_terminal_evidence') or [r['status'] for r in offer['items'] if r['item']==item]!=['success']:raise ValueError('discount parent state changed')
        for sku,value in wanted.items():
            existing=[r for r in offer['rows'] if (r['item'],r['sku'])==(item,sku)]
            if len(existing)>1:raise ValueError('duplicate historical discount row')
            if existing:existing[0]['deduct']=value
            else:offer['rows'].append(dict(item=item,sku=sku,deduct=value))
            if entry.get('_evidence'):
                next(r for r in offer['rows'] if (r['item'],r['sku'])==(item,sku))['amendment_receipt']=entry['_evidence']
    return result

def overlay(offers):
    entries=[]
    for name in WINDOWS:
        for item in sorted(ITEMS):
            folder=ROOT/name/item;terminal=folder/'terminal.json'
            if terminal.exists():
                raw=terminal.read_bytes();entry=json.loads(raw)
                entry['_evidence']={'path':str(terminal),'sha256':hashlib.sha256(raw).hexdigest(),'kind':'exact_three_verified_correction'}
                entries.append(entry)
            elif list(folder.glob('*intent.json')):
                # Unknown correction cannot silently reuse old historical amounts.
                offers=deepcopy(offers)
                oid,start,end=WINDOWS[name]
                for offer in offers:
                    if offer.get('platform_offer_id',offer['offer_id'])==oid and (offer['start'],offer['end'])==(start,end):
                        for status in offer['items']:
                            if status['item']==item:status['status']='unknown'
    return apply_entries(offers,entries) if entries else offers
