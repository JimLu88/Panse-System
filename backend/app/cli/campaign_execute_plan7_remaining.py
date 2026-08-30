"""Call the exact plan-7 remaining signup route with container-held auth."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import (
    campaign_plan7_remaining_signup_service,
    settings_service,
)


_URL = "http://127.0.0.1:8000/api/campaigns/execute-super-reduce-plan7-remaining"
_MAX_INPUT_BYTES = 8 * 1024
_FIXED_PAYLOAD = {
    "workflow_key": campaign_plan7_remaining_signup_service.WORKFLOW_KEY,
    "plan_id": campaign_plan7_remaining_signup_service.PLAN_ID,
    "expected_status": campaign_plan7_remaining_signup_service.EXPECTED_STATUS,
    "expected_item_scope_sha256": (
        campaign_plan7_remaining_signup_service.AUTHORIZED_ITEM_SCOPE_SHA256),
    "recovery_incident_id": (
        campaign_plan7_remaining_signup_service.RECOVERY_INCIDENT_ID),
}


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw:
        raise ValueError("stdin 必须提供计划7剩余报名 JSON")
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("计划7剩余报名 JSON 超过 8 KiB 上限")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划7剩余报名 JSON 无效: {exc}") from exc
    if body != _FIXED_PAYLOAD:
        raise ValueError("输入与程序固化的计划7剩余商品范围不一致")
    return json.dumps(
        body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _service_token() -> str:
    with SessionLocal() as db:
        token = settings_service.get(
            db, CAMPAIGN_PREPARE_SERVICE_SETTING, env_fallback=False)
    if not token:
        raise RuntimeError("活动准备服务身份未配置")
    return token


def call_execute(payload: bytes, *, token: str) -> tuple[int, bytes]:
    req = request.Request(
        _URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": token,
            "User-Agent": "panse-campaign-plan7-remaining-cli/2",
        },
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=3600) as response:
            return int(response.status), response.read()
    except error.HTTPError as exc:
        return int(exc.code), exc.read()


def _call_with_heartbeat(
        payload: bytes, *, token: str, heartbeat_s: float = 20.0,
) -> tuple[int, bytes]:
    """Keep SSH/caller sessions visibly alive while the server owns the work."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(call_execute, payload, token=token)
        while True:
            try:
                return pending.result(timeout=heartbeat_s)
            except FutureTimeout:
                print(
                    "[plan7] 服务器仍在执行只读导出/安全校验，请勿关闭或重跑…",
                    file=sys.stderr,
                    flush=True,
                )


def main() -> int:
    try:
        payload = _read_payload()
        status, body = _call_with_heartbeat(payload, token=_service_token())
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
