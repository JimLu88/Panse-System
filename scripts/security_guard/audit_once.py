"""Daily Codex self-check of NAS monitor receipts, NOT an ERP uptime probe."""
import json
from pathlib import Path
import subprocess
import time

from common import atomic_json, load_json

STATE = Path('D:/AI/畔色ERP系统/outputs/security-guard/audit-state.json')


def main():
    before = load_json(STATE, {})
    cmd = ['C:/Program Files/Git/usr/bin/ssh.exe', '-i', str(Path.home()/'.ssh/panse_nas'),
           '-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
           '-p','2222','15068803006@DS923plus',
           'sudo -n /usr/bin/python3 /volume1/docker/panse/security-guard/nas_guard.py audit']
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if p.returncode:
            raise RuntimeError('nas_audit_unreachable')
        result = json.loads(p.stdout)
        result['reachable'] = True
        if before.get('reachable') is False:
            result['notify'] = True
            result['recovered_audit_connection'] = True
    except Exception as exc:
        result = {'reachable':False,'notify':before.get('reachable') is not False,
                  'error_type':type(exc).__name__,
                  'message':'未能读取群晖轻量监测回执；不能推断ERP或群晖已宕机。'}
    result['checked_at'] = time.time()
    atomic_json(STATE, result)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
