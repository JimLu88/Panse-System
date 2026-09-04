"""Run the fixed Plan 8 V8 continuation through the local API."""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import campaign_plan8_final_recovery_v8_service as recovery
from app.services import settings_service


_URL = "http://127.0.0.1:8000/api/campaigns/recover-super88-plan8-final-v8"
_MAX_INPUT_BYTES = 400000


def _read_payload() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("计划8最终恢复V8 JSON缺失或超过4 KiB")
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划8最终恢复V8 JSON无效: {exc}") from exc
    confirmations = {
        "execute": recovery.EXECUTE_CONFIRMATION,
        "readback": recovery.READBACK_CONFIRMATION,
        "resume_preclaim_v3": recovery.PRECLAIM_RESUME_CONFIRMATION,
        "resume_claimed_preupload_v4": (
            recovery.CLAIMED_PREUPLOAD_RESUME_CONFIRMATION),
        "resume_claimed_preupload_v5": (
            recovery.CLAIMED_PREUPLOAD_POST_READBACK_CONFIRMATION),
        "resume_claimed_preupload_v6": (
            recovery.CLAIMED_PREUPLOAD_LEASE_SCOPE_CONFIRMATION),
        "resume_claimed_preupload_v7": (
            recovery.CLAIMED_PREUPLOAD_BUSY_WAIT_CONFIRMATION),
        "resume_claimed_preupload_v8": (
            recovery.CLAIMED_PREUPLOAD_LEASE_EXPIRY_CONFIRMATION),
        "resume_claimed_preupload_v9": (
            recovery.CLAIMED_PREUPLOAD_DIALOG_FIX_CONFIRMATION),
        "resume_claimed_preupload_v10": (
            recovery.CLAIMED_PREUPLOAD_CAMPAIGN_GUARD_CONFIRMATION),
        "resume_claimed_preupload_v11": (
            recovery.CLAIMED_PREUPLOAD_CLAIM_VERIFY_CONFIRMATION),
        "resume_claimed_preupload_v12": (
            recovery.CLAIMED_PREUPLOAD_LAZY_IMPORT_CONFIRMATION),
        "resume_claimed_preupload_v13": (
            recovery.CLAIMED_PREUPLOAD_ALLOWLIST_CONFIRMATION),
        "resume_claimed_preupload_v14": (
            recovery.CLAIMED_PREUPLOAD_SEMANTIC_MODAL_CONFIRMATION),
        "resume_claimed_preupload_v15": (
            recovery.CLAIMED_PREUPLOAD_EDITOR_IDENTITY_CONFIRMATION),
        "resume_claimed_preupload_v16": (
            recovery.CLAIMED_PREUPLOAD_NESTED_MODAL_CONFIRMATION),
        "resume_claimed_preupload_v17": (
            recovery.CLAIMED_PREUPLOAD_MOBAN_TEXT_CONFIRMATION),
        "resume_claimed_preupload_v18": (
            recovery.CLAIMED_POSTUPLOAD_READBACK_CONFIRMATION),
        "resume_claimed_preupload_v19": (
            recovery.CLAIMED_OFFICIAL_TEMPLATE_CONFIRMATION),
        "resume_claimed_preupload_v20": (
            recovery.CLAIMED_TEMPLATE_GENERATION_CONFIRMATION),
        "resume_claimed_preupload_v21": (
            recovery.CLAIMED_TEMPLATE_CLAIM_VERIFY_CONFIRMATION),
        "resume_claimed_preupload_v22": (
            recovery.CLAIMED_TEMPLATE_CLOSE_CONFIRMATION),
        "resume_claimed_preupload_v23": (
            recovery.CLAIMED_TEMPLATE_CONTRACT_CONFIRMATION),
        "resume_claimed_preupload_v24": (
            recovery.CLAIMED_EXPORT_RETRY_CONFIRMATION),
        "resume_claimed_preupload_v26": (
            recovery.CLAIMED_MANUAL_EXPORT_V26_CONFIRMATION),
        "resume_claimed_preupload_v27": (
            recovery.CLAIMED_MANUAL_EXPORT_V27_CONFIRMATION),
        "resume_claimed_preupload_v28": (
            recovery.CLAIMED_MANUAL_EXPORT_V28_CONFIRMATION),
    }
    expected = {
        "workflow_key": recovery.WORKFLOW_KEY,
        "plan_id": recovery.PLAN_ID,
        "expected_status": recovery.EXPECTED_STATUS,
        "recovery_version": recovery.RECOVERY_VERSION,
        "mode": body.get("mode"),
        "confirmation": confirmations.get(body.get("mode")),
        "target_scope_sha256": recovery.EXPECTED_TARGET_SCOPE_SHA256,
    }
    if body.get("mode") == "resume_claimed_preupload_v28":
        expected.update({
            "manual_export_filename": recovery.MANUAL_EXPORT_FILENAME,
            "manual_export_size": recovery.MANUAL_EXPORT_SIZE,
            "manual_export_sha256": recovery.MANUAL_EXPORT_SHA256,
            "manual_export_base64": body.get("manual_export_base64"),
        })
        if not isinstance(body.get("manual_export_base64"), str):
            raise ValueError("计划8人工导出文件内容缺失")
    if body != expected or body.get("mode") not in confirmations:
        raise ValueError("计划8最终恢复V8输入与程序固化范围不一致")
    if body.get("mode") == "resume_claimed_preupload_v28":
        try:
            workbook = base64.b64decode(
                body["manual_export_base64"], validate=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("计划8人工导出文件编码无效") from exc
        if (len(workbook) != recovery.MANUAL_EXPORT_SIZE
                or hashlib.sha256(workbook).hexdigest()
                != recovery.MANUAL_EXPORT_SHA256):
            raise ValueError("计划8人工导出文件指纹不一致")
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
            headers={"Content-Type": "application/json; charset=utf-8",
                     "X-API-Key": _service_token(),
                     "User-Agent": "panse-campaign-plan8-final-recovery-v8/1"})
        opener = request.build_opener(request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=5400) as response:
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
