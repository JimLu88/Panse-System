"""Notification gate for campaign automation.

Campaign execution can be operated in chat-only mode without disabling other
ERP alerts.  The default remains enabled for backward compatibility.
"""
from __future__ import annotations

from sqlalchemy.orm import Session


SETTING_KEY = "campaign_notifications_enabled"


def enabled(db: Session) -> bool:
    from app.services import settings_service

    raw = settings_service.get(db, SETTING_KEY, env_fallback=False)
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def broadcast_text(db: Session, text: str, *, title: str = "", level: str = "info") -> dict:
    if not enabled(db):
        return {
            "feishu": False,
            "webhook": False,
            "skipped": "campaign_notifications_disabled",
        }
    from app.services import notify_service

    return notify_service.broadcast_text(db, text, title=title, level=level)
