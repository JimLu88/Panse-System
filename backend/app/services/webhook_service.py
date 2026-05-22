"""Outbound Webhooks (Phase 10, Tier 3 #15, 借鉴 Stripe).

业务: 系统重要事件 (订单状态变化 / 告警 / 工厂单生成) 时, POST 给用户配的 URL,
让 BI 工具 / Zapier / 自家数据中台能集成.

配置在 system_settings:
    webhook_endpoints = JSON 数组 [{url, secret, events: ["order.paid", ...]}]
    一个 endpoint 订阅多个 event。

调用方:
    publish(db, event="order.paid", payload={order_id: 1, ...})
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import urllib.error
import urllib.request

from sqlalchemy.orm import Session

from app.services import settings_service

_logger = logging.getLogger("panse.webhook")

WEBHOOK_ENDPOINTS_KEY = "webhook_endpoints"


def get_endpoints(db: Session) -> list[dict]:
    raw = settings_service.get(db, WEBHOOK_ENDPOINTS_KEY)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return []


def set_endpoints(db: Session, endpoints: list[dict]) -> None:
    settings_service.set_value(
        db, WEBHOOK_ENDPOINTS_KEY, json.dumps(endpoints, ensure_ascii=False),
    )


def _sign(payload: dict, secret: str) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _post(url: str, payload: dict, signature: str, *, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Panse-Signature": signature,
                "X-Panse-Event": payload.get("event", ""),
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, resp.read(512).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return False, str(e)
    except Exception as e:  # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


def publish(db: Session, *, event: str, payload: dict) -> dict:
    """业务调: 发布一个事件给所有订阅了的 endpoint.

    异步发: 用 threading (不阻塞主调用). 失败只 log, 不抛.
    """
    endpoints = [ep for ep in get_endpoints(db) if event in (ep.get("events") or [])]
    if not endpoints:
        return {"event": event, "delivered": 0}

    full_payload = {
        "event": event,
        "timestamp": int(time.time()),
        "data": payload,
    }
    for ep in endpoints:
        url = ep.get("url")
        secret = ep.get("secret", "")
        if not url:
            continue
        sig = _sign(full_payload, secret)
        # 异步发, 不阻塞调用方
        threading.Thread(
            target=lambda u=url, p=full_payload, s=sig:
                _logger.info("webhook %s → %s", event, _post(u, p, s)),
            daemon=True,
        ).start()
    return {"event": event, "delivered": len(endpoints)}
