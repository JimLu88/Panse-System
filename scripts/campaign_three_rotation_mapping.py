"""Refresh only the approved three-product mappings from official export bytes."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-new-three-20261010/rotation/correction')
ITEMS=['1090473184978','1089439705938','1089444961055']

def sha(data):return hashlib.sha256(data).hexdigest()
def digest(data):return sha(json.dumps(data,sort_keys=True,default=str).encode())

def inputs():
    terminal=json.loads((ROOT/'replacement-publication/terminal.json').read_text(encoding='utf-8'))
    if terminal['state']!='all_three_replacements_published_and_exported':raise ValueError('verified publication and export required')
    prior=json.loads((ROOT/'erp-applied.json').read_text(encoding='utf-8'))['after']
    files=[]
    for entry in terminal['export']['files']:
        raw=Path(entry['path']).read_bytes()
        if sha(raw)!=entry['sha256']:raise ValueError('official export changed')
        files.append(base64.b64encode(raw).decode())
    return {'files':files,'prior':prior}

def validate(report,prior):
    if report.get('ok') is not True:raise ValueError(str(report.get('error')))
    rows=report['mappings'];codes={r['sku_code'] for r in prior['promos']}
    if len(rows)!=24 or {r['sku_code'] for r in rows}!=codes:raise ValueError('exact 24 code scope required')
    changed=[r for r in rows if r['changed']]
    counts={i:sum(r['taobao_item_id']==i for r in changed) for i in ITEMS}
    if counts!={ITEMS[0]:1,ITEMS[1]:7,ITEMS[2]:7}:raise ValueError('exact 15 replacements required')
    old={r['sku_code']:str(r['taobao_sku_id']) for r in prior['promos']}
    if any(r['old_sku_id']!=old[r['sku_code']] for r in rows):raise ValueError('mapping drift')
    if len({r['taobao_sku_id'] for r in rows})!=24:raise ValueError('duplicate physical ID')

def remote(mode,payload,expected):
    from sqlalchemy import text
    from app.database import SessionLocal
    from app.services.sku_rotation_service import preview_export_mapping_refresh,apply_export_mapping_refresh
    codes=[r['sku_code'] for r in payload['prior']['promos']]
    books=[base64.b64decode(r) for r in payload['files']]
    with SessionLocal() as db:
        def prices():
            return json.loads(json.dumps([dict(r[0]) for r in db.execute(text('SELECT to_jsonb(t) FROM pricing_sku t WHERE sku_code=ANY(:codes) ORDER BY id'),{'codes':codes})],default=str))
        before=prices()
        if before!=payload['prior']['prices']:raise ValueError('ERP pricing drift')
        report=preview_export_mapping_refresh(db,books,item_ids=ITEMS,sku_codes=codes)
        validate(report,payload['prior'])
        version=digest(report)
        if mode=='preview':return {'state':'preview','sha256':version,'report':report}
        if expected!=version:raise ValueError('fresh exact preview required')
        result=apply_export_mapping_refresh(db,books,item_ids=ITEMS,sku_codes=codes,dry_run=False)
        if prices()!=before:raise ValueError('ERP prices changed unexpectedly after commit')
        readback=preview_export_mapping_refresh(db,books,item_ids=ITEMS,sku_codes=codes)
        if not readback.get('ok') or readback['changed_rows']!=0:raise ValueError('mapping commit readback mismatch')
        return {'state':'rotation_mapping_verified','erp_prices_unchanged':True,'result':result,'readback':readback}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['preview','apply']);parser.add_argument('--expected');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();payload=inputs()
    source="__name__='mapping_remote'\n"+Path(__file__).read_text(encoding='utf-8')+'\nprint(json.dumps(remote('+repr(args.mode)+','+repr(payload)+','+repr(args.expected)+'),ensure_ascii=False,default=str))'
    command=['C:/Program Files/Git/usr/bin/ssh.exe','-i',str(Path.home()/'.ssh/panse_nas'),'-o','BatchMode=yes','-o','ConnectTimeout=15','-p','2222','15068803006@DS923plus','sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -']
    with args.output.open('x',encoding='utf-8') as output:
        run=subprocess.run(command,input=source,text=True,encoding='utf-8',capture_output=True,timeout=60)
        if run.returncode:raise RuntimeError('failed or unknown; no auto retry: '+run.stderr[-1500:])
        result=json.loads(run.stdout);json.dump(result,output,ensure_ascii=False,indent=2)
    print(json.dumps({'state':result['state'],'sha256':result.get('sha256')},ensure_ascii=False))

if __name__=='__main__':main()
