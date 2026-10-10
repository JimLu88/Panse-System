"""Exact one additional failed SKU refresh; no ERP pricing or unrelated updates."""
import argparse,base64,hashlib,json,subprocess
from pathlib import Path
ROOT=Path('D:/AI/畔色ERP系统/outputs/campaign-new-three-20261010/rotation/correction')
ITEM='1090473184978';CODE='PPS2625016100119';OLD='6308609774680'
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()

def validate(report,prior):
 if not report.get('ok'):raise ValueError(str(report.get('error')))
 rows=report['mappings'];expected={r['sku_code']:str(r['taobao_sku_id']) for r in prior}
 if len(rows)!=10 or {r['sku_code'] for r in rows}!=set(expected):raise ValueError('exact enamel 10 scope required')
 changes=[r for r in rows if r['changed']]
 if len(changes)!=1 or changes[0]['sku_code']!=CODE or changes[0]['old_sku_id']!=OLD:raise ValueError('only failed SKU may change')
 if any(r['old_sku_id']!=expected[r['sku_code']] or r['taobao_item_id']!=ITEM for r in rows):raise ValueError('mapping drift')
 if len({r['taobao_sku_id'] for r in rows})!=10:raise ValueError('new IDs not unique')

def remote(mode,payload,expected):
 from sqlalchemy import text
 from app.database import SessionLocal
 from app.services.sku_rotation_service import preview_export_mapping_refresh,apply_export_mapping_refresh
 books=[base64.b64decode(v) for v in payload['files']];codes=[r['sku_code'] for r in payload['prior']]
 with SessionLocal() as db:
  def prices():return json.loads(json.dumps([dict(r[0]) for r in db.execute(text('SELECT to_jsonb(t) FROM pricing_sku t WHERE sku_code=ANY(:codes) ORDER BY id'),{'codes':codes})],default=str))
  before=prices()
  if before!=payload['prices']:raise ValueError('ERP price drift')
  report=preview_export_mapping_refresh(db,books,item_ids=[ITEM],sku_codes=codes);validate(report,payload['prior']);version=digest(report)
  if mode=='preview':return {'state':'preview','sha256':version,'report':report}
  if expected!=version:raise ValueError('fresh preview required')
  result=apply_export_mapping_refresh(db,books,item_ids=[ITEM],sku_codes=codes,dry_run=False)
  if prices()!=before:raise ValueError('ERP pricing changed')
  readback=preview_export_mapping_refresh(db,books,item_ids=[ITEM],sku_codes=codes)
  if not readback.get('ok') or readback['changed_rows']!=0:raise ValueError('mapping readback mismatch')
  return {'state':'rotation_mapping_verified','erp_prices_unchanged':True,'result':result,'readback':readback}

def main():
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['preview','apply']);p.add_argument('--expected');p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 terminal=json.loads((ROOT/'enamel-final-rotation/terminal.json').read_text(encoding='utf-8'))
 if terminal['state']!='published_and_exported':raise ValueError('publication and export required')
 previous=json.loads((ROOT/'rotation-mapping-applied.json').read_text(encoding='utf-8'))
 prior=[r for r in previous['readback']['mappings'] if r['taobao_item_id']==ITEM]
 codes={r['sku_code'] for r in prior};original=json.loads((ROOT/'erp-applied.json').read_text(encoding='utf-8'))['after']
 files=[]
 for f in terminal['export']['files']:
  raw=Path(f['path']).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=f['sha256']:raise ValueError('export changed')
  files.append(base64.b64encode(raw).decode())
 payload={'files':files,'prior':prior,'prices':[r for r in original['prices'] if r['sku_code'] in codes]}
 source="__name__='remote_mapping'\n"+Path(__file__).read_text(encoding='utf-8')+'\nprint(json.dumps(remote('+repr(a.mode)+','+repr(payload)+','+repr(a.expected)+'),ensure_ascii=False,default=str))'
 command=['C:/Program Files/Git/usr/bin/ssh.exe','-i',str(Path.home()/'.ssh/panse_nas'),'-o','BatchMode=yes','-o','ConnectTimeout=15','-p','2222','15068803006@DS923plus','sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -']
 with a.output.open('x',encoding='utf-8') as f:
  run=subprocess.run(command,input=source,text=True,encoding='utf-8',capture_output=True,timeout=60)
  if run.returncode:raise RuntimeError('failed or unknown, no repeat: '+run.stderr[-1800:])
  result=json.loads(run.stdout);json.dump(result,f,ensure_ascii=False,indent=2)
 print(json.dumps({'state':result['state'],'sha256':result.get('sha256')}))
if __name__=='__main__':main()
