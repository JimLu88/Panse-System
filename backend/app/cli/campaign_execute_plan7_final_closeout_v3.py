"""Call the repaired and auth-scoped plan-7 closeout endpoint exactly once."""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import campaign_plan7_final_closeout_service as service
from app.services import settings_service


_URL = "http://127.0.0.1:8000/api/campaigns/execute-super-reduce-plan7-final-closeout-v3"
_MAX_INPUT_BYTES = 12 * 1024
_FIXED_PAYLOAD = {
    "workflow_key": service.WORKFLOW_KEY,
    "plan_id": service.PLAN_ID,
    "expected_status": service.EXPECTED_STATUS,
    "bundle_id": service.BUNDLE_ID,
    "expected_source_sha256": service.SOURCE_SHA256,
    "expected_policy_sha256": service.POLICY_SHA256,
    "expected_manifest_sha256": service.MANIFEST_SHA256,
    "expected_item_scope_sha256": service.ITEM_SCOPE_SHA256,
    "recovery_id": service.RECOVERY_ID,
    "expected_web_agent_commit": service.EXPECTED_WEB_AGENT_COMMIT,
}


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw:
        raise ValueError("stdin 必须提供计划7最终收口 V3 JSON")
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("计划7最终收口 V3 JSON 超过 12 KiB 上限")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划7最终收口 V3 JSON 无效: {exc}") from exc
    if body != _FIXED_PAYLOAD:
        raise ValueError("计划7最终收口 V3 输入与固化恢复身份不一致")
    return json.dumps(
        body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _service_token() -> str:
    with SessionLocal() as db:
        token = settings_service.get(
            db, CAMPAIGN_PREPARE_SERVICE_SETTING, env_fallback=False)
    if not token:
        raise RuntimeError("活动准备服务身份未配置；必须先完成数据库迁移")
    return token


def call_closeout(payload: bytes, *, token: str) -> tuple[int, bytes]:
    req = request.Request(
        _URL, data=payload, method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": token,
            "User-Agent": "panse-campaign-plan7-final-closeout-cli/3",
        })
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=1800) as response:
            return int(response.status), response.read()
    except error.HTTPError as exc:
        return int(exc.code), exc.read()


def main() -> int:
    try:
        payload = _read_payload()
        status, body = call_closeout(payload, token=_service_token())
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
