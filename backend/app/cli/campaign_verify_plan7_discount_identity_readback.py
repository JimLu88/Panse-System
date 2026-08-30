"""Call the fixed read-only closeout for the submitted plan-7 recovery."""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import (
    campaign_discount_identity_recovery_service as recovery_service,
    settings_service,
)


_URL = (
    "http://127.0.0.1:8000/api/campaigns/"
    "verify-super-reduce-plan7-discount-sku-identity-readback"
)
_MAX_INPUT_BYTES = 16_384
_FIXED_PAYLOAD = {
    "workflow_key": recovery_service.WORKFLOW_KEY,
    "plan_id": recovery_service.PLAN_ID,
    "expected_attempt_id": recovery_service.EXPECTED_RECOVERY_ATTEMPT_ID,
    "expected_terminal_evidence_request_id": (
        recovery_service.EXPECTED_TERMINAL_EVIDENCE_REQUEST_ID),
    "expected_scope_sha256": recovery_service.EXPECTED_NEW_MISSING_SCOPE_SHA256,
}


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("stdin 必须提供固定计划7只读收口 JSON")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划7只读收口 JSON 无效: {exc}") from exc
    if body != _FIXED_PAYLOAD:
        raise ValueError("输入与固化的 workflow/plan/attempt/终端回执不一致")
    return json.dumps(
        body, ensure_ascii=False, separators=(",", ":")).encode()


def _service_token() -> str:
    with SessionLocal() as db:
        token = settings_service.get(
            db, CAMPAIGN_PREPARE_SERVICE_SETTING, env_fallback=False)
    if not token:
        raise RuntimeError("活动准备服务身份未配置")
    return token


def call_api(payload: bytes, *, token: str) -> tuple[int, bytes]:
    req = request.Request(
        _URL, data=payload, method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": token,
            "User-Agent": "panse-plan7-discount-identity-readback-cli/1",
        })
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=1200) as response:
            return int(response.status), response.read()
    except error.HTTPError as exc:
        return int(exc.code), exc.read()


def main() -> int:
    try:
        status, body = call_api(_read_payload(), token=_service_token())
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
