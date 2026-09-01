"""Read back the fixed lift-desk rotation SKU draft without saving it again."""
from __future__ import annotations

import json

from app.cli.campaign_stage_lift_desk_sku_slot import MANIFEST
from app.database import SessionLocal
from app.services import sku_identity_service, web_agent_service


def main() -> int:
    with SessionLocal() as db:
        result = web_agent_service.product_sku_slot_draft_readback(db, MANIFEST)
        result["ledger_proposal"] = (
            sku_identity_service.mark_lift_desk_draft_readback_result(
                db, result=result))
        db.commit()
    result.pop("screenshot_base64", None)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["ledger_proposal"].get("verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
