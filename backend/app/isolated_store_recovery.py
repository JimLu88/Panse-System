"""Offline recovery tools: destination must be new; never restore into a live store.
Backup verification uses a separately retained trusted store.json, not archive keys.
"""
import hashlib,hmac,io,json,sqlite3,uuid,zipfile
from contextlib import closing
from pathlib import Path
from app.isolated_store import Store,dumps,safe_child

MAX_BACKUP_BYTES=512*1024*1024

def backup(store):
    # A consistent SQLite snapshot; source media are immutable and never deleted.
    snapshot=safe_child(store.root,'backups/'+uuid.uuid4().hex+'.sqlite')
    with store.db() as source, closing(sqlite3.connect(snapshot)) as dest:source.backup(dest)
    files={'store.sqlite':snapshot.read_bytes()}
    with closing(sqlite3.connect(snapshot)) as db:
        for path,sha in db.execute('SELECT DISTINCT path,sha256 FROM media'):
            if path!='media/'+sha:raise ValueError('noncanonical_media_path')
            raw=safe_child(store.root,path).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('media_hash_mismatch')
            files[path]=raw
    # The snapshot is our own temporary backup file, never the live database.
    snapshot.unlink()
    manifest={'schema':'isolated-store-backup-v1','store_id':store.owner,'instance_id':store.instance,
              'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()}}
    signature=hmac.new(bytes.fromhex(store.config['backup_key']),dumps(manifest).encode(),hashlib.sha256).hexdigest()
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json',dumps({'manifest':manifest,'signature':signature}))
        for name,raw in files.items():archive.writestr(name,raw)
    return out.getvalue()

def recover(trusted_config_path, archive_bytes, destination):
    config=json.loads(Path(trusted_config_path).read_text(encoding='utf8'))
    target=Path(destination)
    if not target.is_absolute() or target.exists():raise ValueError('new_recovery_directory_required')
    if len(archive_bytes)>MAX_BACKUP_BYTES:raise ValueError('backup_too_large')
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        infos=archive.infolist();names=[i.filename for i in infos]
        if len(set(names))!=len(names) or sum(i.file_size for i in infos)>MAX_BACKUP_BYTES:raise ValueError('unsafe_archive')
        doc=json.loads(archive.read('manifest.json'));m=doc['manifest']
        if m.get('store_id')!=config['store_id'] or m.get('instance_id')!=config['instance_id'] or m.get('schema')!='isolated-store-backup-v1':raise ValueError('foreign_backup_rejected')
        signature=hmac.new(bytes.fromhex(config['backup_key']),dumps(m).encode(),hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,doc.get('signature','')):raise ValueError('modified_backup_rejected')
        if set(names)!=set(m['files'])|{'manifest.json'} or 'store.sqlite' not in m['files']:raise ValueError('invalid_archive_members')
        payload={}
        for name,sha in m['files'].items():
            if name!='store.sqlite' and name!='media/'+sha:raise ValueError('invalid_archive_path')
            raw=archive.read(name)
            if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('backup_hash_mismatch')
            payload[name]=raw
    target.mkdir(parents=True)
    for folder in ['media','thumbs','exports','backups','logs']:(target/folder).mkdir()
    for name,raw in payload.items():safe_child(target,name).write_bytes(raw)
    (target/'store.json').write_text(dumps(config),encoding='utf8');(target/'store.json').chmod(0o600)
    restored=Store(target,config['store_id'])
    # Authentication sessions do not survive restoration.
    with restored.db() as db:db.execute('DELETE FROM sessions')
    return restored

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    b=sub.add_parser('backup');b.add_argument('--root',required=True);b.add_argument('--store',required=True)
    r=sub.add_parser('recover');r.add_argument('--trusted-config',required=True);r.add_argument('--archive',required=True);r.add_argument('--destination',required=True)
    a=p.parse_args()
    if a.action=='backup':
        s=Store(a.root,a.store);data=backup(s);path=safe_child(s.root,'backups/'+uuid.uuid4().hex+'.zip');path.write_bytes(data);path.chmod(0o600);print(path)
    else:
        s=recover(a.trusted_config,Path(a.archive).read_bytes(),a.destination);print('Recovered independent store: '+s.owner+'; inspect before starting, do not mount both copies simultaneously')
