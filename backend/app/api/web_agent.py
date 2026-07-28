# -*- coding: utf-8 -*-
"""自动取数 (Panse-Web-Agent 集成) API。

GET  /api/web-agent/status     — Agent 在线/任务登录态/新鲜度/最近扫描/待人工
POST /api/web-agent/run        — 立即编排取数 (后台线程, 防并发)
POST /api/web-agent/ingest     — 只扫描共享目录导入 (不触发浏览器任务)
GET  /api/web-agent/settings   — 更新间隔等设置
PUT  /api/web-agent/settings   — 修改 (订单默认1天 / 余额流水默认3天; token write-only)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import agent_ingest_service as ingest
from app.services import settings_service, web_agent_service

router = APIRouter(prefix="/api/web-agent", tags=["web-agent"])


def _freshness(state: dict, category: str, interval_days: int) -> dict:
    last = state.get(category)
    status = "missing"
    if last:
        try:
            dt = datetime.fromisoformat(last)
            if dt > datetime.now() - timedelta(days=interval_days):
                status = "fresh"
            elif dt > datetime.now() - timedelta(days=interval_days * 2):
                status = "due"
            else:
                status = "stale"
        except ValueError:
            pass
    return {"category": category, "last_success": last,
            "interval_days": interval_days, "status": status}


def _friendly_agent_text(text: str) -> str:
    """把 Web-Agent 内部任务 ID 转成用户能辨认的账号名称。"""
    return (text or "").replace(
        "bal_alipay_main", "支付宝主力账号余额"
    ).replace(
        "alipay_main", "支付宝主力账号"
    )


@router.get("/status")
def status(db: Session = Depends(get_db)):
    hb = web_agent_service.health(db)
    tasks = web_agent_service.list_tasks(db).get("tasks") or [] if hb.get("online") else []
    state = ingest._load_json(db, ingest.KEY_STATE)
    iv_orders = ingest._get_int(db, ingest.KEY_INTERVAL_ORDERS, 1)
    iv_balance = ingest._get_int(db, ingest.KEY_INTERVAL_BALANCE, 3)
    return {
        "agent": {"online": hb.get("online", False), "error": hb.get("error"),
                  "url": web_agent_service.BASE_URL,
                  "token_configured": bool(settings_service.get(db, web_agent_service.TOKEN_KEY))},
        "tasks": [
            {"id": t.get("id"), "title": t.get("title"),
             "has_session": t.get("has_session"), "cadence": t.get("cadence"),
             "skip_reason": ingest.SKIPPED_TASKS.get(t.get("id"))}
            for t in tasks
        ],
        "freshness": [
            _freshness(state, "taobao_report", iv_orders),
            _freshness(state, "settlement", iv_balance),
            _freshness(state, "promotion", iv_balance),
            _freshness(state, "wanshifu", iv_balance),
            _freshness(state, "balance", iv_balance),
            _freshness(state, ingest.STATE_MAIN_ALIPAY_FLOW, 1),
        ],
        "last_ingest": ingest._load_json(db, ingest.KEY_LAST_INGEST),
        "orchestration": {**ingest._load_json(db, ingest.KEY_ORCH_STATE),
                          "running": ingest.is_running()},
        "not_ready": [
            {"item": "支付宝企业号 流水/余额", "reason": "官方 API 应用审核中, 上线后自动接入"},
        ],
        "shipping_password": {
            "configured": bool(settings_service.get(db, "taobao_shipping_pwd_latest", env_fallback=False)),
            "received_at": settings_service.get(db, "taobao_shipping_pwd_at", env_fallback=False),
            "hint": "导加密发货报表前, 把淘宝发到微信的口令以「发货密码 xxx」转发给飞书机器人(60分钟内有效)。",
        },
    }


@router.post("/run")
def run_now(db: Session = Depends(get_db)):
    """立即取数: 全部类别强制触发 (忽略间隔)。后台执行, 进度看 status。"""
    hb = web_agent_service.health(db)
    if not hb.get("online"):
        raise HTTPException(409, f"取数服务(:8500)不在线: {hb.get('error', '')}。"
                                 f"请在 Windows 上启动 Panse-Web-Agent 后重试。")
    if not ingest.start_orchestrate_async(force=True):
        raise HTTPException(409, "已有一轮取数在进行中, 请稍候 (状态见本页)。")
    return {"started": True}


@router.post("/ingest")
def ingest_now(db: Session = Depends(get_db)):
    """只扫描共享目录把新文件导入 (不开浏览器)。"""
    return ingest.run_ingest(db)


@router.post("/pull-orders")
def pull_orders(db: Session = Depends(get_db)):
    """订单页「手动更新拉取订单」: 实时触发淘宝订单近3月全量下载+导入 (后台)。
    发工厂制单图前可点一下, 拉到最新订单/状态。"""
    hb = web_agent_service.health(db)
    if not hb.get("online"):
        raise HTTPException(409, f"取数服务(:8500)不在线: {hb.get('error', '')}。请先在 Windows 启动 Panse-Web-Agent。")
    res = ingest.pull_orders_async(db)
    if not res.get("started"):
        raise HTTPException(409, res.get("reason", "已有取数在进行中"))
    return {"started": True}


class AgentNotify(BaseModel):
    kind: str = "event"            # qr | scan_wait | file | event | scan_needed | scan_timeout
    text: str = ""
    image_b64: Optional[str] = None  # 二维码/文件预览 PNG (base64)


@router.post("/notify")
def agent_notify(payload: AgentNotify, db: Session = Depends(get_db)):
    """Web-Agent 卡点回调 (用户拍板 2026-06-12 渠道分流):
    - 二维码/文件 (kind=qr/scan_wait/file) → 飞书 (图片发会话, 供手机扫码);
    - 扫码提示/超时 (kind=scan_needed/scan_timeout) → **飞书文本** (用户在飞书回复『扫码』启动);
    - 其它事件 (kind=event) → 企业微信。
    """
    import base64

    from app.services import feishu_client, notify_service
    result: dict = {"feishu": None, "wechat": None}
    notice_text = _friendly_agent_text(payload.text)

    # 扫码相关纯文本 → 飞书 (扫码这件事整个在飞书对话里完成); scan_ok = 扫码成功回执
    if payload.kind in ("scan_needed", "scan_timeout", "scan_ok"):
        chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
        if chat_id and notice_text:
            try:
                feishu_client.send_text(db, chat_id, notice_text)
                result["feishu"] = "已发飞书"
            except Exception as e:  # noqa: BLE001
                result["feishu"] = f"飞书发送失败: {type(e).__name__}: {e}"
        else:
            result["feishu"] = "无外发会话或无文本"
        return result

    is_visual = payload.kind in ("qr", "scan_wait", "file")

    if is_visual and payload.image_b64:
        try:
            from app.services import feishu_client
            chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
            if not chat_id:
                result["feishu"] = "未知外发会话 — 请先在飞书给机器人发一条消息(它会记住会话)"
            else:
                png = base64.b64decode(payload.image_b64)
                key = feishu_client.upload_image(db, png)
                if notice_text:
                    feishu_client.send_text(db, chat_id, notice_text)
                feishu_client.send_image(db, chat_id, key)
                result["feishu"] = "已发飞书"
        except Exception as e:  # noqa: BLE001
            result["feishu"] = f"飞书发送失败: {type(e).__name__}: {e}"

    # 事件/提醒 → 企业微信 (扫码类也补一条文字叫醒)
    wx_text = notice_text or "Panse 取数有新事件"
    if payload.kind in ("qr", "scan_wait"):
        wx_text = f"⚠️ 需要扫码: {notice_text or '支付宝/淘宝导出需验证身份'} — 二维码已发到飞书, 请去飞书扫。"
    ok, msg = notify_service.notify(db, wx_text, level="urgent" if is_visual else "info",
                                    title="畔色 ERP [自动取数]")
    result["wechat"] = "已发企业微信" if ok else msg
    return result


import re as _re

_HHMM = _re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")


class WebAgentSettings(BaseModel):
    interval_orders_days: Optional[int] = None
    interval_balance_days: Optional[int] = None
    schedule_time: Optional[str] = None       # 每日触发时刻 HH:MM (转发给 Agent local_schedule_time)
    schedule_enabled: Optional[bool] = None   # 定时取数总开关
    token: Optional[str] = None          # write-only, 不回显


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    # 每日触发时刻/开关存在 Agent 端 (local_schedule_*), 取数服务在线时回读
    schedule_time, schedule_enabled = "18:00", True
    agent_s = web_agent_service._get(db, "/api/settings")
    if agent_s.get("ok"):
        schedule_time = str(agent_s.get("local_schedule_time") or "18:00")
        schedule_enabled = bool(agent_s.get("local_schedule_enabled", True))
    return {
        "interval_orders_days": ingest._get_int(db, ingest.KEY_INTERVAL_ORDERS, 1),
        "interval_balance_days": ingest._get_int(db, ingest.KEY_INTERVAL_BALANCE, 3),
        "schedule_time": schedule_time,
        "schedule_enabled": schedule_enabled,
        "token_configured": bool(settings_service.get(db, web_agent_service.TOKEN_KEY)),
        "agent_url": web_agent_service.BASE_URL,
    }


@router.put("/settings")
def put_settings(payload: WebAgentSettings, db: Session = Depends(get_db)):
    if payload.interval_orders_days is not None:
        if payload.interval_orders_days < 1:
            raise HTTPException(400, "订单更新间隔至少 1 天")
        settings_service.set_value(db, ingest.KEY_INTERVAL_ORDERS,
                                   str(payload.interval_orders_days),
                                   description="自动取数: 订单更新间隔(天)")
    if payload.interval_balance_days is not None:
        if payload.interval_balance_days < 1:
            raise HTTPException(400, "余额/流水更新间隔至少 1 天")
        settings_service.set_value(db, ingest.KEY_INTERVAL_BALANCE,
                                   str(payload.interval_balance_days),
                                   description="自动取数: 余额与流水更新间隔(天)")
    if payload.token is not None:
        settings_service.set_value(db, web_agent_service.TOKEN_KEY, payload.token,
                                   description="Panse-Web-Agent API token")
    # 每日触发时刻/开关 → 转发给取数服务 (Agent local_schedule_*), 改完即时重排, 不用重启
    agent_payload: dict = {}
    if payload.schedule_time is not None:
        if not _HHMM.match(payload.schedule_time.strip()):
            raise HTTPException(400, "触发时刻格式应为 HH:MM (如 17:30)")
        agent_payload["local_schedule_time"] = payload.schedule_time.strip()
    if payload.schedule_enabled is not None:
        agent_payload["local_schedule_enabled"] = bool(payload.schedule_enabled)
    if agent_payload:
        res = web_agent_service._post(db, "/api/settings", agent_payload)
        if not res.get("ok"):
            raise HTTPException(409, f"取数服务未接受时间设置: {res.get('error', '')}")
    db.commit()
    return get_settings(db)
