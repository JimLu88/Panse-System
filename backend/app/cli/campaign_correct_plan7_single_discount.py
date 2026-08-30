"""Call the fixed plan-7 four-row correction through the production API."""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import campaign_discount_correction_service, settings_service


_URL = "http://127.0.0.1:8000/api/campaigns/correct-super-reduce-plan7-discount"
_MAX_INPUT_BYTES = 4096
_FIXED_PAYLOAD = {
    "workflow_key": campaign_discount_correction_service.WORKFLOW_KEY,
    "plan_id": campaign_discount_correction_service.PLAN_ID,
    "expected_snapshot_id": (
        campaign_discount_correction_service.EXPECTED_SNAPSHOT_ID),
    "expected_snapshot_artifact_sha256": (
        campaign_discount_correction_service.EXPECTED_SNAPSHOT_ARTIFACT_SHA256),
    "expected_missing_scope_sha256": (
        campaign_discount_correction_service.EXPECTED_MISSING_SCOPE_SHA256),
}


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("stdin 必须提供固定计划7单品立减4行修正 JSON")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划7单品立减4行修正 JSON 无效: {exc}") from exc
    if body != _FIXED_PAYLOAD:
        raise ValueError("输入与程序固化的 workflow/plan/snapshot/4行指纹不一致")
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()


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
            "User-Agent": "panse-plan7-single-discount-correction-cli/1",
        })
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=2400) as response:
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
