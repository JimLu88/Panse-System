"""Call the exact one-shot plan-7 single-discount supplement API."""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import (
    campaign_plan7_discount_supplement_service as supplement_service,
    settings_service,
)


_URL = (
    "http://127.0.0.1:8000/api/campaigns/"
    "supplement-super-reduce-plan7-single-discount"
)
_MAX_INPUT_BYTES = 16_384


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("stdin 必须提供计划7单品立减固定补报 JSON")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划7单品立减固定补报 JSON 无效: {exc}") from exc
    if not supplement_service._validate_request(body):
        raise ValueError("计划7单品立减固定补报身份不匹配")
    return json.dumps(
        body, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


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
            "User-Agent": "panse-plan7-discount-supplement-cli/1",
        })
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=3600) as response:
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
