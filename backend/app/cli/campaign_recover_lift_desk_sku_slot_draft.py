"""Repair the exact existing lift-desk SKU draft and keep it unsubmitted."""
from __future__ import annotations

import json

from app.cli.campaign_stage_lift_desk_sku_slot import MANIFEST
from app.database import SessionLocal
from app.services import sku_identity_service, web_agent_service


AUTHORIZATION_REF = "user:2026-09-01:recover-lift-desk-sku-draft"
RECOVERY_IDENTITY = {
    "draft_id": "1355242198",
    "saved_at": "2026-09-01 22:54:21",
}


def main() -> int:
    with SessionLocal() as db:
        sku_identity_service.ensure_lift_desk_proposal(
            db, authorization_ref=AUTHORIZATION_REF)
        result = web_agent_service.product_sku_slot_draft_recover(db, MANIFEST)
        result["ledger_proposal"] = (
            sku_identity_service.mark_lift_desk_draft_save_result(
                db, result=result,
                required_recovery_identity=RECOVERY_IDENTITY))
        db.commit()
    result.pop("screenshot_base64", None)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("ok") and result.get("draft_saved") else 1


if __name__ == "__main__":
    raise SystemExit(main())
