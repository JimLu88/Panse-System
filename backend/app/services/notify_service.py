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


# 企微(微信)推送治理 (用户 2026-07-06: 微信只想收每天10点经营日报, 其余全是噪音)。
# 默认 briefing_only: notify() 走的 webhook(企微)只放行标了 wechat_allowed=True 的调用(=经营日报/测试);
# 其余静默(仍走各自飞书/业务群, 不受影响)。设 wechat_push_scope=all 可一键恢复全部企微推送。
WECHAT_SCOPE_KEY = "wechat_push_scope"


def notify(
    db: Session, text: str, *,
    level: str = "info", title: Optional[str] = None, wechat_allowed: bool = False,
) -> tuple[bool, str]:
    """发送通知到 notify webhook(当前=企业微信). provider=none 或 webhook 未配 时静默返回.

    wechat_allowed: 是否放行到企微。默认 False —— 除非设置 wechat_push_scope=all, 否则只有
      标了 wechat_allowed=True 的调用(经营日报/测试)才推企微, 其余静默(治噪音)。
    永远不抛异常 (即使发不出来也不能影响主业务流). 失败时 log.warning.
    """
    # 测试/维护环境总开关: 跑 pytest 时绝不往真实飞书群推 (2026-06-11: C6 并发测试
    # 每跑一次全量测试就给群里推一条缺货告警, 用户被轰炸)
    import os
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return False, "通知已被 PANSE_DISABLE_NOTIFY 关闭 (测试环境)"

    cfg = get_config(db)
    provider = cfg["provider"]
    webhook = cfg["webhook"]
    if provider == "none" or not webhook:
        return False, "未配置通知 provider 或 webhook"

    # 企微推送治理: 默认只放行经营日报; 其余静默(设 wechat_push_scope=all 恢复)
    scope = settings_service.get(db, WECHAT_SCOPE_KEY, env_fallback=False) or "briefing_only"
    if scope != "all" and not wechat_allowed:
        return False, "企微推送已限为仅经营日报 (wechat_push_scope=briefing_only)"

    payload = _build_payload(provider, text, level=level, title=title)
    ok, resp = _post_json(webhook, payload)
    if not ok:
        _logger.warning("通知发送失败 (%s): %s", provider, resp)
        _enqueue_retry(db, text, title=title, level=level)
    return ok, resp


# ── 失败重试队列 (用户审核项 17): 失败入库, 调度每 30 分钟重发, 最多 5 次 ──

_MAX_RETRY = 5


def _enqueue_retry(db: Session, text: str, *, title: Optional[str], level: str) -> None:
    """发送失败 → 入重试队列。任何错误都吞掉 (审计性质, 不影响主业务)。"""
    try:
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import text as _sql
        db.execute(_sql(
            "INSERT INTO notify_retries (text, title, level, attempts, next_at) "
            "VALUES (:t, :ti, :lv, 0, :nx)"
        ), {"t": text, "ti": title, "lv": level,
            "nx": datetime.now(timezone.utc) + timedelta(minutes=30)})
        db.flush()
    except Exception:  # pragma: no cover - 表不存在(测试库)/入队失败不阻断
        _logger.debug("notify 重试入队失败 (忽略)", exc_info=True)


def retry_pending(db: Session) -> dict:
    """调度任务: 重发到期的失败通知。成功标 sent_at; 超过 _MAX_RETRY 次放弃。"""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import text as _sql
    now = datetime.now(timezone.utc)
    try:
        rows = db.execute(_sql(
            "SELECT id, text, title, level, attempts FROM notify_retries "
            "WHERE sent_at IS NULL AND attempts < :mx AND (next_at IS NULL OR next_at <= :now) "
            "ORDER BY id LIMIT 20"
        ), {"mx": _MAX_RETRY, "now": now}).fetchall()
    except Exception:  # pragma: no cover
        return {"retried": 0, "sent": 0}
    sent = 0
    for rid, text_, title, level, attempts in rows:
        cfg = get_config(db)
        ok = False
        if cfg["provider"] != "none" and cfg["webhook"]:
            payload = _build_payload(cfg["provider"], text_, level=level, title=title)
            ok, _ = _post_json(cfg["webhook"], payload)
        if ok:
            db.execute(_sql("UPDATE notify_retries SET sent_at=:now, attempts=attempts+1 WHERE id=:id"),
                       {"now": now, "id": rid})
            sent += 1
        else:
            # 指数退避: 30min × 2^attempts
            db.execute(_sql(
                "UPDATE notify_retries SET attempts=attempts+1, next_at=:nx WHERE id=:id"
            ), {"nx": now + timedelta(minutes=30 * (2 ** (attempts + 1))), "id": rid})
    db.commit()
    return {"retried": len(rows), "sent": sent}


def test_notify(db: Session) -> tuple[bool, str]:
    """admin 后台点 "测试通知" 时调用. 发一条 ping 消息."""
    return notify(
        db, "畔色 ERP 通知测试 — 如果收到这条说明 webhook 配通了。",
        level="info", title="测试通知", wechat_allowed=True,   # 测试按钮需能真发, 放行企微
    )


# ── 纯文本通知统一广播 (可切换/双推; 富内容[图/文件/卡片]不走这里, 仍直连 feishu_client) ──

TEXT_CHANNELS_KEY = "notify_text_channels"
DEFAULT_TEXT_CHANNELS = "feishu,webhook"  # 双推: 飞书应用机器人 + notify webhook(企微); 可在设置改单边


def broadcast_text(
    db: Session, text: str, *, title: Optional[str] = None, level: str = "info",
    wechat_allowed: bool = False,
) -> dict:
    """纯文本通知统一入口, 按 notify_text_channels 推送 (逗号分隔):
      - feishu  → 飞书应用机器人 feishu_client.send_text(chat=feishu_push_chat_id)
      - webhook → notify() 走 notify_provider 配的 webhook (当前=企业微信)

    wechat_allowed 透传给 notify(): 默认 False, 配合 wechat_push_scope=briefing_only 时
      企微静默 (只有经营日报等标 True 的才推企微); 飞书那条不受影响。
    返回 {channel: ok}。永不抛 (通知失败不阻断业务)。
    """
    import os
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        return {"disabled": True}
    raw = settings_service.get(db, TEXT_CHANNELS_KEY, env_fallback=False)
    channels = [c.strip() for c in (raw or DEFAULT_TEXT_CHANNELS).split(",") if c.strip()]
    results: dict = {}
    full = f"{title}\n{text}" if title else text
    if "feishu" in channels:
        try:
            from app.services import feishu_client
            chat = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
            if chat:
                feishu_client.send_text(db, chat, full)
                results["feishu"] = True
            else:
                results["feishu"] = False
        except Exception:  # noqa: BLE001 - 通知失败不阻断业务
            _logger.warning("broadcast_text 飞书推送失败", exc_info=True)
            results["feishu"] = False
    if "webhook" in channels:
        ok, _ = notify(db, text, level=level, title=title, wechat_allowed=wechat_allowed)
        results["webhook"] = ok
    return results
