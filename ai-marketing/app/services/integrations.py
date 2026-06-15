"""轻量集成：#4 n8n自动化编排 / #8 官方API直发连接器 / #10 AEO打通。"""
from __future__ import annotations

import logging

import httpx
from sqlalchemy.orm import Session

from . import runtime_config

log = logging.getLogger("marketing.integrations")

# #4 可供 n8n/Make 订阅的事件类型
AUTOMATION_EVENTS = ["hot_note.found", "lead.created", "lead.overdue",
                     "publish.due", "comment.high_intent", "watchdog.alert"]


def emit_event(event: str, payload: dict) -> bool:
    """把事件推到 n8n/Make webhook（best-effort）。"""
    url = runtime_config.get("automation_webhook_url")
    if not url:
        return False
    try:
        httpx.post(url, json={"event": event, "payload": payload}, timeout=10)
        return True
    except httpx.HTTPError as e:
        log.warning("automation webhook 失败: %s", e)
        return False


# #8 合规官方 API 直发连接器（区别于养号RPA：仅企业号/蒲公英等官方接口）
def official_publish(db: Session, event_id: int) -> dict:
    """对已配置官方API的账号，走 api_driver 直发（mock：标记成功）。

    真实接入：小红书企业号开放接口/蒲公英、知乎机构号等。无官方接口的号仍走 ASSIST 人工。
    """
    from ..models import Account, PublishEvent
    ev = db.get(PublishEvent, event_id)
    if ev is None:
        raise ValueError("发布事件不存在")
    acc = db.get(Account, ev.account_id)
    if not (acc and (acc.official_setup or {}).get("开通店铺组件")):
        raise ValueError("该账号未开通官方API发布能力，请走 ASSIST 人工发布")
    base = runtime_config.get("crawler_base_url")  # 占位：官方API网关地址另配
    # mock：标记直发成功
    ev.driver_used = "api"
    ev.result = "success"
    import datetime as dt
    ev.published_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return {"event_id": ev.id, "driver": "api", "result": "success",
            "note": "官方API直发(mock)；接真实网关后自动走 official endpoint" + (f" @ {base}" if base else "")}


# #10 AEO 打通：拉你的 AEO 项目数据并入大盘
def aeo_overview() -> dict:
    """从 AEO 项目拉品牌在 AI 答案引擎的被引用概况。未配置则返回提示。"""
    url = runtime_config.get("aeo_base_url")
    if not url:
        return {"connected": False,
                "hint": "未连接 AEO 项目。在系统设置填 AEO 地址后，这里展示品牌在 ChatGPT/Perplexity 的被引用率"}
    try:
        resp = httpx.get(f"{url.rstrip('/')}/api/overview", timeout=10)
        resp.raise_for_status()
        return {"connected": True, **resp.json()}
    except httpx.HTTPError as e:
        return {"connected": False, "hint": f"AEO 项目不可达: {e}"}
