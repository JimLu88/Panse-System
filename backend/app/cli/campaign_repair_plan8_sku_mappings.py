"""Run the fixed, user-approved Plan-8 eight-SKU database repair once."""
from __future__ import annotations

import json
import sys

from app.database import SessionLocal
from app.services import campaign_plan8_sku_mapping_repair_service as repair


def main() -> int:
    try:
        body = json.load(sys.stdin)
        if body != repair.fixed_payload():
            raise ValueError("计划8八SKU修复输入与固化范围不一致")
        with SessionLocal() as db:
            result = repair.preview(db) if "--preflight" in sys.argv[1:] else repair.execute(db)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
