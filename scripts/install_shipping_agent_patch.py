"""Hash-bound narrow Web-Agent update; leaves unrelated dirty work untouched."""
import argparse
import hashlib
import json
import os
from pathlib import Path


def sha(data):return hashlib.sha256(data).hexdigest()


def install(root, manifest, *, apply=False, backup=None):
    root=Path(root).resolve(); pending=[];states=[]
    for entry in manifest['files']:
        path=(root/entry['path']).resolve()
        if not path.is_relative_to(root):raise ValueError('outside_runtime')
        raw=path.read_bytes() if path.exists() else None
        current=sha(raw) if raw is not None else None
        if current==entry['after_sha256']:
            states.append({'path':entry['path'],'state':'installed'});continue
        if current!=entry['before_sha256']:raise ValueError('runtime_changed:'+entry['path'])
        lines=raw.decode('utf-8').splitlines() if raw is not None else []
        for change in reversed(entry['changes']):
            if lines[change['start']:change['end']]!=change['before']:
                raise ValueError('patch_context_changed')
            lines[change['start']:change['end']]=change['after']
        content=('\n'.join(lines)+'\n').encode('utf-8')
        if sha(content)!=entry['after_sha256']:raise ValueError('patch_result_hash_mismatch')
        pending.append((entry,path,raw,content));states.append({'path':entry['path'],'state':'ready'})
    if apply:
        if backup is None:raise ValueError('backup_directory_required')
        backup=Path(backup).resolve();backup.mkdir(parents=True,exist_ok=True)
        for entry,path,raw,content in pending:
            if raw is not None:
                saved=backup/entry['path'];saved.parent.mkdir(parents=True,exist_ok=True)
                if saved.exists() and saved.read_bytes()!=raw:raise ValueError('backup_conflict')
                saved.write_bytes(raw)
        for entry,path,raw,content in pending:
            if (path.read_bytes() if path.exists() else None)!=raw:raise ValueError('runtime_changed_during_install')
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix(path.suffix+'.shipping-stage')
            with temp.open('xb') as f:f.write(content)
            os.replace(temp,path)
        for state in states:state['state']='installed'
    return {'applied':apply,'files':states,'runtime_restarted':False}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--apply',action='store_true');p.add_argument('--backup')
    a=p.parse_args();m=json.loads(Path(__file__).with_name('shipping-agent-patch-20261117.json').read_text('utf-8'))
    print(json.dumps(install(a.root,m,apply=a.apply,backup=a.backup),ensure_ascii=False))
