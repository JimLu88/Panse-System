"""Read-only live NAS gate for the Windows uploader; no cached success fallback."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys


def check(request):
    # Transport carries file bytes or explicit scope only, never executable input.
    encoded = base64.b64encode(json.dumps(request).encode()).decode()
    code = '''import base64,json
from app.database import SessionLocal
from app.services.taobao_listing_guard_service import validate_workbook,validate_rows
request=json.loads(base64.b64decode("%s"))
with SessionLocal() as db:
 result=(validate_workbook(db,base64.b64decode(request['workbook_b64'],validate=True))
         if 'workbook_b64' in request else validate_rows(db,request['rows']))
 print(json.dumps(result,ensure_ascii=False))
''' % encoded
    ssh = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Git/usr/bin/ssh.exe'
    args = [str(ssh), '-i', str(Path.home()/'.ssh/panse_nas'), '-o', 'BatchMode=yes',
            '-o', 'ConnectTimeout=10', '-p', '2222', '15068803006@DS923plus',
            'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i -w /app panse-system-api-1 python -']
    result = subprocess.run(args, input=code, capture_output=True, encoding='utf-8', timeout=35)
    if result.returncode:
        raise RuntimeError('live_erp_listing_check_failed')
    return json.loads(result.stdout)


def main():
    try:
        result = check(json.load(sys.stdin))
    except Exception as exc:
        result = {'ok': False, 'error': 'live_erp_listing_check_unavailable',
                  'error_type': type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') else 2


if __name__ == '__main__':
    raise SystemExit(main())
