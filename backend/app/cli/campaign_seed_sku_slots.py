"""Idempotently freeze first-managed SKU price baselines after migration 0146."""
from __future__ import annotations

import json

from app.database import SessionLocal
from app.services import campaign_sku_slot_service


def main() -> int:
    with SessionLocal() as db:
        try:
            result = campaign_sku_slot_service.seed_active_slots(db)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - startup must fail closed
            db.rollback()
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                             ensure_ascii=False))
            return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
