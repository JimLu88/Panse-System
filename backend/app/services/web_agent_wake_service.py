"""Persistent NAS -> Windows Web-Agent wake command channel.

The Windows wake bridge is deliberately tiny and does no browser work.  It
polls this authenticated command slot, starts the full Agent only when asked,
and stops the Agent after the business task becomes idle.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.json_utils import to_jsonable
from app.services import settings_service

COMMAND_KEY = "web_agent_wake_command_v1"
BRIDGE_KEY = "web_agent_wake_bridge_v1"


def _now(value: Optional[datetime] = None) -> datetime:
    current = value or datetime.now().astimezone()
    return current if current.tzinfo is not None else current.astimezone()


def _load(db: Session, key: str) -> dict:
    try:
        value = json.loads(settings_service.get(db, key, env_fallback=False) or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _save(db: Session, key: str, value: dict, description: str) -> None:
    settings_service.set_value(
        db,
        key,
        json.dumps(to_jsonable(value), ensure_ascii=False, separators=(",", ":")),
        description=description,
    )


def request(
    db: Session,
    action: str,
    *,
    reason: str,
    ttl_minutes: int = 10,
    now: Optional[datetime] = None,
) -> dict:
    if action not in {"start", "stop"}:
        raise ValueError(f"unsupported Web-Agent action: {action}")
    current = _now(now)
    existing = _load(db, COMMAND_KEY)
    if (
        existing.get("action") == action
        and existing.get("status") in {"pending", "accepted"}
    ):
        try:
            expires = datetime.fromisoformat(str(existing.get("expires_at") or ""))
            if _now(expires) > current:
                return existing
        except (TypeError, ValueError):
            pass
    command = {
        "id": uuid4().hex,
        "action": action,
        "reason": str(reason or "scheduled_task")[:200],
        "status": "pending",
        "requested_at": current.isoformat(),
        "expires_at": (current + timedelta(minutes=max(2, ttl_minutes))).isoformat(),
    }
    _save(db, COMMAND_KEY, command, "Web-Agent按需启停命令")
    db.commit()
    return command


def next_command(
    db: Session,
    *,
    agent_id: str,
    now: Optional[datetime] = None,
) -> dict:
    current = _now(now)
    bridge = _load(db, BRIDGE_KEY)
    bridge.update({"agent_id": agent_id[:80], "last_seen_at": current.isoformat()})
    _save(db, BRIDGE_KEY, bridge, "Windows按需唤醒桥状态")
    db.commit()

    command = _load(db, COMMAND_KEY)
    if not command or command.get("status") not in {"pending", "accepted"}:
        return {"action": "noop"}
    try:
        expires = datetime.fromisoformat(str(command.get("expires_at") or ""))
        if _now(expires) <= current:
            command.update({"status": "expired", "finished_at": current.isoformat()})
            _save(db, COMMAND_KEY, command, "Web-Agent按需启停命令")
            db.commit()
            return {"action": "noop"}
    except (TypeError, ValueError):
        return {"action": "noop"}
    return command


def acknowledge(
    db: Session,
    *,
    command_id: str,
    agent_id: str,
    status: str,
    detail: str = "",
    now: Optional[datetime] = None,
) -> dict:
    current = _now(now)
    command = _load(db, COMMAND_KEY)
    if command.get("id") != command_id:
        return {"ok": False, "ignored": "stale_command"}
    command.update({
        "status": status[:40],
        "agent_id": agent_id[:80],
        "detail": detail[:300],
        "acknowledged_at": current.isoformat(),
    })
    if status in {"running", "online", "stopped", "failed"}:
        command["finished_at"] = current.isoformat()
    _save(db, COMMAND_KEY, command, "Web-Agent按需启停命令")
    bridge = _load(db, BRIDGE_KEY)
    bridge.update({
        "agent_id": agent_id[:80],
        "last_seen_at": current.isoformat(),
        "last_command_id": command_id,
        "last_status": status[:40],
        "last_detail": detail[:300],
    })
    _save(db, BRIDGE_KEY, bridge, "Windows按需唤醒桥状态")
    db.commit()
    return {"ok": True, "command": command}


def status(db: Session) -> dict:
    return {"command": _load(db, COMMAND_KEY), "bridge": _load(db, BRIDGE_KEY)}
