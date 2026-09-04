"""Resume the exact pre-write plan-7 sample-cent attempt once."""
from __future__ import annotations

import json
import sys

from app.database import SessionLocal
from app.services import campaign_plan7_sample_cent_service as service


_MAX_INPUT_BYTES = 20_000


def _read_payload() -> dict:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("stdin 必须提供计划7样块原 attempt 续接 JSON")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"计划7样块续接 JSON 无效: {exc}") from exc
    if not service._validate_resume_request(payload):
        raise ValueError("计划7样块续接身份不匹配")
    return payload


def main() -> int:
    try:
        payload = _read_payload()
        with SessionLocal() as db:
            result = service.resume_plan7_sample_cent(
                db, request_payload=payload)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
