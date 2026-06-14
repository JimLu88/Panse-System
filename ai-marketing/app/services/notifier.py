"""飞书群机器人通知（自定义机器人 webhook，免应用审批）。

用途：看门狗告警 / 超期线索 / 到点发布提醒——重要的事追着人走，不用盯工作台。
未配置 FEISHU_WEBHOOK_URL 时静默跳过，业务不受影响。
"""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings

log = logging.getLogger("marketing.notifier")


def send_feishu(text: str) -> bool:
    """发送文本到飞书群（best-effort：失败只记日志，不抛异常）。地址走运行时配置(界面可改)。"""
    from . import runtime_config
    url = runtime_config.get("feishu_webhook_url")
    if not url:
        return False
    try:
        resp = httpx.post(url, json={"msg_type": "text", "content": {"text": text}},
                          timeout=10)
        resp.raise_for_status()
        return True
    except httpx.HTTPError as e:
        log.warning("飞书通知失败: %s", e)
        return False
