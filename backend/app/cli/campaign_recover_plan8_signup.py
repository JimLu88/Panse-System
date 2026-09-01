"""Call the fixed plan-8 signup-only recovery route from the API container."""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import campaign_plan8_signup_recovery_service as recovery
from app.services import settings_service


_URL = "http://127.0.0.1:8000/api/campaigns/recover-super88-plan8-signup"
_MAX_INPUT_BYTES = 16 * 1024
_FIXED_PAYLOAD = {
    "workflow_key": recovery.WORKFLOW_KEY,
    "plan_id": recovery.PLAN_ID,
    "expected_status": recovery.EXPECTED_STATUS,
    "expected_original_attempt_id": recovery.ORIGINAL_ATTEMPT_ID,
    "expected_original_scope_sha256": recovery.ORIGINAL_OUTER_SCOPE_SHA256,
    "expected_full_signup_scope_sha256": recovery.EXPECTED_FULL_SIGNUP_SCOPE_SHA256,
    "expected_pending_scope_sha256": recovery.EXPECTED_PENDING_SCOPE_SHA256,
    "expected_policy_sha256": recovery.EXPECTED_POLICY_SHA256,
    "expected_candidate_sha256": recovery.EXPECTED_CANDIDATE_SHA256,
}


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("计划8补报名 JSON 缺失或超过 16 KiB")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划8补报名 JSON 无效: {exc}") from exc
    if body != _FIXED_PAYLOAD:
        raise ValueError("计划8补报名输入与程序固化的失败回执/范围/证据不一致")
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
                "User-Agent": "panse-campaign-plan8-signup-recovery-cli/1",
            })
        opener = request.build_opener(request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=1800) as response:
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
