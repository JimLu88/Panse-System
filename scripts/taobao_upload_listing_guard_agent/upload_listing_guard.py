"""Fail-closed, live ERP listing check at the byte-transfer boundary."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ERP_ROOT = Path('D:/AI/畔色ERP系统/ERP程序')
CLI = ERP_ROOT / 'scripts/check_taobao_upload_listing.py'
RECEIPTS = Path(__file__).resolve().parents[2] / 'data/output/upload-listing-guard'


class UploadListingBlocked(RuntimeError):
    def __init__(self, receipt):
        self.listing_check = receipt
        super().__init__('upload_listing_guard: ' + json.dumps(receipt, ensure_ascii=False))


def check_bytes(data):
    digest = hashlib.sha256(data).hexdigest()
    request = {'workbook_b64': base64.b64encode(data).decode('ascii')}
    try:
        executable = Path(sys.executable)
        if executable.name.lower() == 'pythonw.exe':
            executable = executable.with_name('python.exe')
        proc = subprocess.run([str(executable), str(CLI)], input=json.dumps(request),
                              encoding='utf-8', capture_output=True, timeout=45,
                              env={**os.environ, 'PYTHONIOENCODING':'utf-8'})
        receipt = json.loads(proc.stdout)
        RECEIPTS.mkdir(parents=True, exist_ok=True)
        (RECEIPTS / f'{time.time_ns()}-{digest[:16]}.json').write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        if proc.returncode or receipt.get('ok') is not True or receipt.get('sha256') != digest:
            raise UploadListingBlocked(receipt)
        return receipt
    except UploadListingBlocked:
        raise
    except Exception as exc:
        raise UploadListingBlocked({'ok': False, 'error': 'live_erp_listing_check_unavailable',
                                    'error_type': type(exc).__name__}) from exc


def guarded_payload(path, expected_sha=None):
    path = Path(path)
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if not data or (expected_sha and expected_sha != digest):
        raise UploadListingBlocked({'ok': False, 'error': 'upload_file_changed'})
    check_bytes(data)
    # These are exactly the bytes checked; Chromium never reopens a changed path.
    return {'name': path.name,
            'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'buffer': data}


async def payload(path):
    return await asyncio.to_thread(guarded_payload, path)
