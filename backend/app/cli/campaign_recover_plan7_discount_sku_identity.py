"""Call the fixed plan-7 SKU-identity repair and recovery API."""
from __future__ import annotations

import base64
import hashlib
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
    "recover-super-reduce-plan7-discount-sku-identity"
)
_MAX_INPUT_BYTES = 3_000_000


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("stdin 必须提供固定计划7 SKU身份修正恢复 JSON")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划7 SKU身份修正恢复 JSON 无效: {exc}") from exc
    fixed = {
        "workflow_key": recovery_service.WORKFLOW_KEY,
        "plan_id": recovery_service.PLAN_ID,
        "expected_old_attempt_id": recovery_service.EXPECTED_OLD_ATTEMPT_ID,
        "official_product_export_sha256": (
            recovery_service.EXPECTED_OFFICIAL_EXPORT_SHA256),
        "expected_new_scope_sha256": (
            recovery_service.EXPECTED_NEW_MISSING_SCOPE_SHA256),
    }
    if {key: body.get(key) for key in fixed} != fixed:
        raise ValueError("输入与固化的 workflow/plan/失败回执/新范围不一致")
    if set(body) != {*fixed, "official_product_export_b64"}:
        raise ValueError("输入字段超出固定 SKU 身份恢复协议")
    try:
        artifact = base64.b64decode(
            body["official_product_export_b64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("官方商品导出 base64 无效") from exc
    if hashlib.sha256(artifact).hexdigest() != fixed[
            "official_product_export_sha256"]:
        raise ValueError("官方商品导出 SHA256 不匹配")
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
            "User-Agent": "panse-plan7-discount-sku-identity-recovery-cli/1",
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
