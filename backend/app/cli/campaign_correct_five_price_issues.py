from __future__ import annotations

import json
import os
import sys
from urllib import request

from app.services import campaign_five_price_correction_service as service


def main() -> int:
    phase = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
    try:
        payload = service.request_payload(phase)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    token = os.environ.get("PANSE_CAMPAIGN_PREPARE_TOKEN", "").strip()
    if not token:
        print(json.dumps({"ok": False, "error": "service_token_missing"}, ensure_ascii=False))
        return 2
    req = request.Request(
        "http://127.0.0.1:8000/api/campaigns/correct-five-price-issues",
        data=json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=2500) as response:
            body = json.loads(response.read().decode())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(body, ensure_ascii=False))
    return 0 if body.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
