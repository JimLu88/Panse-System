"""Run the fixed plan-8 final V2 recovery through the local API."""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import campaign_plan8_final_recovery_v2_service as recovery
from app.services import settings_service


_URL = "http://127.0.0.1:8000/api/campaigns/recover-super88-plan8-final-v2"
_MAX_INPUT_BYTES = 4096
_FIXED_PAYLOAD = {
    "workflow_key": recovery.WORKFLOW_KEY,
    "plan_id": recovery.PLAN_ID,
    "expected_status": recovery.EXPECTED_STATUS,
    "recovery_version": recovery.RECOVERY_VERSION,
}


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("计划8最终恢复V2 JSON缺失或超过4 KiB")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划8最终恢复V2 JSON无效: {exc}") from exc
    if body != _FIXED_PAYLOAD:
        raise ValueError("计划8最终恢复V2输入与程序固化范围不一致")
    return json.dumps(body, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _service_token() -> str:
    with SessionLocal() as db:
        token = settings_service.get(
            db, CAMPAIGN_PREPARE_SERVICE_SETTING, env_fallback=False)
    if not token:
        raise RuntimeError("活动准备服务身份未配置")
    return token


def main() -> int:
    try:
        payload = _read_payload()
        req = request.Request(
            _URL, data=payload, method="POST",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "X-API-Key": _service_token(),
                "User-Agent": "panse-campaign-plan8-final-recovery-v2/1",
            })
        opener = request.build_opener(request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=3600) as response:
                status, body = int(response.status), response.read()
        except error.HTTPError as exc:
            status, body = int(exc.code), exc.read()
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    sys.stdout.buffer.write(body)
    if body and not body.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
