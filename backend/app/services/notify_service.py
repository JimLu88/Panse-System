"""推送通知 (业务需求扩展: watchdog_triggered 时通知运维).

支持 4 个 webhook 兼容平台 (统一用 stdlib urllib, 不依赖 httpx):

- slack         (Slack incoming webhook)
- wechat_work   (企业微信群机器人)
- dingtalk      (钉钉自定义群机器人)
- feishu        (飞书自定义群机器人)
- none          (关闭通知)

配置项 (settings 表):
    notify_provider   slack | wechat_work | dingtalk | feishu | none
    notify_webhook    完整 URL (slack/wechat/钉钉/飞书的 webhook)

公开 API:
    notify(db, text, *, level='info', title=None)
    test_notify(db) -> (ok, error_text)

调用方:
    system_monitor.maybe_auto_restart() — 检测到 watchdog_triggered 时
    系统集中告警入口
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service

_logger = logging.getLogger("panse.notify")

SUPPORTED_PROVIDERS = (
    {"value": "none",        "label": "关闭"},
    {"value": "slack",       "label": "Slack"},
    {"value": "wechat_work", "label": "企业微信群机器人"},
    {"value": "dingtalk",    "label": "钉钉群机器人"},
    {"value": "feishu",      "label": "飞书群机器人"},
)


def get_config(db: Session) -> dict:
    """返回 {provider, webhook, webhook_set}; webhook 永远不外露明文 (除非调试)."""
    provider = settings_service.get(db, "notify_provider") or "none"
    webhook = settings_service.get(db, "notify_webhook") or ""
    return {"provider": provider, "webhook": webhook, "webhook_set": bool(webhook)}


# ----------------------------- payload 构造 ----------------------- #


def _build_payload(provider: str, text: str, *, level: str, title: Optional[str]) -> dict:
    """每个平台需要不同 JSON 结构."""
    prefix_map = {"info": "ℹ️", "warn": "⚠️", "error": "🚨"}
    prefix = prefix_map.get(level, "")
    full_text = f"{prefix} {title}\n{text}" if title else f"{prefix} {text}"

    if provider == "slack":
        return {"text": full_text}
    if provider == "wechat_work":
        return {"msgtype": "text", "text": {"content": full_text}}
    if provider == "dingtalk":
        return {"msgtype": "text", "text": {"content": full_text}}
    if provider == "feishu":
        return {"msg_type": "text", "content": {"text": full_text}}
    return {"text": full_text}


def _post_json(url: str, body: dict, *, timeout_sec: float = 5.0) -> tuple[bool, str]:
    """POST JSON via stdlib. 返回 (ok, message). 失败不抛, 上层决定要不要 log."""
    try:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            body_bytes = resp.read(1024)
            return True, body_bytes.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URL 错误: {e.reason}"
    except Exception as e:  # pragma: no cover — 兜底
        return False, f"{type(e).__name__}: {e}"


# ----------------------------- 公开 API --------------------------- #


def notify(
    db: Session, text: str, *,
    level: str = "info", title: Optional[str] = None,
) -> tuple[bool, str]:
    """发送通知. provider=none 或 webhook 未配 时静默返回 (False, '未配置').

    永远不抛异常 (即使发不出来也不能影响主业务流). 失败时 log.warning.
    """
    cfg = get_config(db)
    provider = cfg["provider"]
    webhook = cfg["webhook"]
    if provider == "none" or not webhook:
        return False, "未配置通知 provider 或 webhook"

    payload = _build_payload(provider, text, level=level, title=title)
    ok, resp = _post_json(webhook, payload)
    if not ok:
        _logger.warning("通知发送失败 (%s): %s", provider, resp)
    return ok, resp


def test_notify(db: Session) -> tuple[bool, str]:
    """admin 后台点 "测试通知" 时调用. 发一条 ping 消息."""
    return notify(
        db, "畔色 ERP 通知测试 — 如果收到这条说明 webhook 配通了。",
        level="info", title="测试通知",
    )
