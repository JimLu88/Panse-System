"""Execute the one approved Super Reduce plan-7 recovery via the real API.

The destination and all four CAS identity fields are fixed.  The encrypted
service token stays inside the production container.  This CLI makes one HTTP
request and has no retry branch.
"""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import campaign_resume_service, settings_service


_URL = "http://127.0.0.1:8000/api/campaigns/resume-super-reduce-plan7"
_MAX_INPUT_BYTES = 8 * 1024
_FIXED_PAYLOAD = {
    "workflow_key": campaign_resume_service.WORKFLOW_KEY,
    "plan_id": campaign_resume_service.PLAN_ID,
    "expected_status": campaign_resume_service.EXPECTED_STATUS,
    "expected_scope_sha256": campaign_resume_service.EXPECTED_SCOPE_SHA256,
}


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw:
        raise ValueError("stdin 必须提供计划7恢复 JSON")
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("计划7恢复 JSON 超过 8 KiB 上限")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划7恢复 JSON 无效: {exc}") from exc
    if body != _FIXED_PAYLOAD:
        raise ValueError("计划7恢复输入与程序固化的 workflow/plan/status/scope 不一致")
    return json.dumps(
        body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _service_token() -> str:
    with SessionLocal() as db:
        token = settings_service.get(
            db, CAMPAIGN_PREPARE_SERVICE_SETTING, env_fallback=False)
    if not token:
        raise RuntimeError("活动准备服务身份未配置；必须先完成数据库迁移")
    return token


def call_resume(payload: bytes, *, token: str) -> tuple[int, bytes]:
    req = request.Request(
        _URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": token,
            "User-Agent": "panse-campaign-plan7-resume-cli/1",
        },
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=1200) as response:
            return int(response.status), response.read()
    except error.HTTPError as exc:
        return int(exc.code), exc.read()


def main() -> int:
    try:
        payload = _read_payload()
        status, body = call_resume(payload, token=_service_token())
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
