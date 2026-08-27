"""Prepare one campaign through the production HTTP API without exposing a key.

Input is a single JSON object on stdin.  The module reads the encrypted,
prepare-only service credential through settings_service, calls the same
FastAPI endpoint used by human operators, and prints only the response JSON.
It cannot select another path and never opens a browser or submits to Taobao.
"""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import settings_service

_URL = "http://127.0.0.1:8000/api/campaigns/prepare"
_MAX_INPUT_BYTES = 1024 * 1024


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw:
        raise ValueError("stdin 必须提供活动准备 JSON")
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("活动准备 JSON 超过 1 MiB 上限")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"活动准备 JSON 无效: {exc}") from exc
    if not isinstance(body, dict):
        raise ValueError("活动准备输入必须是 JSON 对象")
    if not str(body.get("workflow_key") or "").strip():
        raise ValueError("活动准备输入缺少 workflow_key")
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _service_token() -> str:
    with SessionLocal() as db:
        token = settings_service.get(
            db, CAMPAIGN_PREPARE_SERVICE_SETTING, env_fallback=False)
    if not token:
        raise RuntimeError("活动准备服务身份未配置；必须先完成数据库迁移")
    return token


def call_prepare(payload: bytes, *, token: str) -> tuple[int, bytes]:
    req = request.Request(
        _URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": token,
            "User-Agent": "panse-campaign-prepare-cli/1",
        },
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=300) as response:
            return int(response.status), response.read()
    except error.HTTPError as exc:
        return int(exc.code), exc.read()


def main() -> int:
    try:
        payload = _read_payload()
        status, body = call_prepare(payload, token=_service_token())
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
