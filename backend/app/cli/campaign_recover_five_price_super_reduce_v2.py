from __future__ import annotations

import json
import sys
from urllib import error, request

from app.database import SessionLocal
from app.dependencies import CAMPAIGN_PREPARE_SERVICE_SETTING
from app.services import campaign_five_price_correction_service as service
from app.services import settings_service


_URL = (
    "http://127.0.0.1:8000/api/campaigns/"
    "recover-five-price-super-reduce-v2"
)


def _service_token() -> str:
    with SessionLocal() as db:
        token = settings_service.get(
            db, CAMPAIGN_PREPARE_SERVICE_SETTING, env_fallback=False)
    if not token:
        raise RuntimeError("活动准备服务身份未配置")
    return token


def call_api(*, token: str) -> tuple[int, bytes]:
    req = request.Request(
        _URL,
        data=json.dumps(
            service.super_recovery_v2_request_payload(), ensure_ascii=False,
            separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-API-Key": token,
            "User-Agent": "panse-five-price-super-recovery-v2-cli/1",
        },
    )
    try:
        with request.build_opener(request.ProxyHandler({})).open(
                req, timeout=3600) as response:
            return int(response.status), response.read()
    except error.HTTPError as exc:
        return int(exc.code), exc.read()


def main() -> int:
    try:
        status, body = call_api(token=_service_token())
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    sys.stdout.buffer.write(body)
    if body and not body.endswith(b"\n"):
        sys.stdout.buffer.write(b"\n")
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
