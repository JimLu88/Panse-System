"""Compile or read one campaign's immutable pre-submit bundle via the API."""
from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARATION_BUNDLE_SERVICE_SETTING
from app.services import settings_service


_URL = "http://127.0.0.1:8000/api/campaigns/prepare-final-bundle"
_MAX_INPUT_BYTES = 64 * 1024
_MODES = {"compile", "refresh_and_compile", "read_latest"}


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw:
        raise ValueError("stdin 必须提供最终准备包 JSON")
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("最终准备包 JSON 超过 64 KiB 上限")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"最终准备包 JSON 无效: {exc}") from exc
    if not isinstance(body, dict):
        raise ValueError("最终准备包输入必须是 JSON 对象")
    allowed = {"workflow_key", "plan_id", "expected_status", "mode"}
    extra = sorted(set(body) - allowed)
    if extra:
        raise ValueError(f"最终准备包输入含不允许字段: {extra}")
    if not str(body.get("workflow_key") or "").strip():
        raise ValueError("最终准备包输入缺少 workflow_key")
    try:
        plan_id = int(body.get("plan_id"))
    except (TypeError, ValueError) as exc:
        raise ValueError("plan_id 必须是正整数") from exc
    if plan_id <= 0:
        raise ValueError("plan_id 必须是正整数")
    body["plan_id"] = plan_id
    mode = str(body.get("mode") or "compile")
    if mode not in _MODES:
        raise ValueError(f"mode 必须是 {sorted(_MODES)} 之一")
    body["mode"] = mode
    if body.get("expected_status") is None:
        body.pop("expected_status", None)
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8")


def _service_token() -> str:
    with SessionLocal() as db:
        token = settings_service.get(
            db, CAMPAIGN_PREPARATION_BUNDLE_SERVICE_SETTING,
            env_fallback=False)
    if not token:
        raise RuntimeError("活动准备服务身份未配置；必须先完成数据库迁移")
    return token


def call_api(payload: bytes, *, token: str) -> tuple[int, bytes]:
    req = request.Request(
        _URL, data=payload, method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": token,
            "User-Agent": "panse-campaign-final-bundle-cli/1",
        },
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=1500) as response:
            return int(response.status), response.read()
    except error.HTTPError as exc:
        return int(exc.code), exc.read()


def main() -> int:
    try:
        payload = _read_payload()
        status, body = call_api(payload, token=_service_token())
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
