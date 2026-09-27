"""One-shot exact ERP link sync. Default preview; never operates Taobao."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

PROOF='cdccb78b4e9b7c2dc3377fb44c49bf05d53bc0ff3012921ed8f7ba98c3d8c60e'


def execute(mode, expected=None, evidence=None):
    if mode=='apply':
        if not expected or not evidence or hashlib.sha256(Path(evidence).read_bytes()).hexdigest()!=PROOF:
            raise ValueError('exact_preview_and_user_screenshot_required')
    source=Path(__file__).with_name('campaign_link_retirement_remote.py').read_text(encoding='utf-8-sig')
    # Values are Python string literals, never shell fragments.
    source+=f'\nprint(json.dumps(run({mode!r},{expected!r},{PROOF!r}),ensure_ascii=False,default=str))\n'
    command=['C:/Program Files/Git/usr/bin/ssh.exe','-i',str(Path.home()/'.ssh/panse_nas'),
        '-o','BatchMode=yes','-o','ConnectTimeout=15','-p','2222','15068803006@DS923plus',
        'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -']
    proc=subprocess.run(command,input=source,encoding='utf-8',capture_output=True,timeout=60)
    if proc.returncode:
        raise RuntimeError('ERP link sync failed or result unknown; read preview, do not blindly retry. '+proc.stderr[-1200:])
    return json.loads(proc.stdout)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['preview','apply'])
    p.add_argument('--expected-sha256')
    p.add_argument('--evidence',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    # Reserve output before a possible write so an unavailable file cannot
    # strand a known committed operation without a local receipt destination.
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8') as stream:
        result=execute(args.mode,args.expected_sha256,args.evidence)
        json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
