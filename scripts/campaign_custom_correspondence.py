"""One exact user-approved custom correspondence; preview default, no Taobao."""
import argparse
import json
from pathlib import Path
import subprocess
from campaign_custom_correspondence_policy import SOURCE_SHA, ROWS


def verify_source():
    from campaign_sku_fact_store import read_version
    _,rows=read_version(SOURCE_SHA)
    for expected in ROWS:
        found=[r for r in rows if (r['item'],r['sku'])==(expected['item'],expected['sku'])]
        if (len(found)!=1 or found[0]['merchant_code']!=expected['raw_code']
                or found[0]['attributes']!=expected['attributes'] or found[0]['issues']):
            raise ValueError('approved_source_rows_missing_or_changed')


def execute(mode, expected=None):
    if mode not in ('preview','apply'):raise ValueError('unsupported_mode')
    if mode=='apply' and not expected:raise ValueError('preview_required')
    verify_source()
    policy=Path(__file__).with_name('campaign_custom_correspondence_policy.py').read_text(encoding='utf-8-sig')
    source="import sys,types\nm=types.ModuleType('campaign_custom_correspondence_policy')\nsys.modules[m.__name__]=m\nexec("+repr(policy)+",m.__dict__)\n"
    source+=Path(__file__).with_name('campaign_custom_correspondence_remote.py').read_text(encoding='utf-8-sig')
    source+=f'\nprint(json.dumps(run({mode!r},{expected!r}),ensure_ascii=False,default=str))\n'
    command=['C:/Program Files/Git/usr/bin/ssh.exe','-i',str(Path.home()/'.ssh/panse_nas'),
        '-o','BatchMode=yes','-o','ConnectTimeout=15','-p','2222','15068803006@DS923plus',
        'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -']
    proc=subprocess.run(command,input=source,encoding='utf-8',capture_output=True,timeout=60)
    if proc.returncode:raise RuntimeError('Operation failed or unknown; preview before retry. '+proc.stderr[-1200:])
    return json.loads(proc.stdout)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',nargs='?',default='preview',choices=['preview','apply'])
    p.add_argument('--expected-sha256')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as stream:
        result=execute(a.mode,a.expected_sha256)
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
