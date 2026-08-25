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
from app.services import settings_service, web_agent_service, web_agent_wake_service

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


_AUTH_NOTICE_KEY = "web_agent_auth_notice_open"


def _sanitize_auth_notice(text: str) -> str:
    """企业微信扫码通知的最后一道脱敏闸：任何货币金额都不允许外发。"""
    import re

    cleaned = _friendly_agent_text(text)
    cleaned = re.sub(
        r"(?:¥|￥)\s*[+-]?\s*\d[\d,]*(?:\.\d+)?", "[金额已隐藏]", cleaned
    )
    cleaned = re.sub(
        r"((?:金额|余额|收入|支出)\s*[:：]?\s*)[+-]?\d[\d,]*(?:\.\d+)?",
        r"\1[已隐藏]",
        cleaned,
    )
    return cleaned


def _auth_notice_id(text: str) -> str:
    raw = text or ""
    if "bal_alipay_main" in raw:
        return "alipay_main"
    if "alipay_main" in raw or "支付宝主力账号" in raw:
        return "alipay_main"
    return _friendly_agent_text(raw).split(" ", 1)[0][:80] or "unknown"


def _auth_notice_open(db: Session) -> set[str]:
    import json

    try:
        value = json.loads(
            settings_service.get(db, _AUTH_NOTICE_KEY, env_fallback=False) or "[]"
        )
        return set(value) if isinstance(value, list) else set()
    except (TypeError, ValueError):
        return set()


def _save_auth_notice_open(db: Session, values: set[str]) -> None:
    import json

    settings_service.set_value(
        db,
        _AUTH_NOTICE_KEY,
        json.dumps(sorted(values), ensure_ascii=False),
        description="自动取数: 已发送且尚未恢复的登录失效提醒",
    )
    db.commit()


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
            "hint": "系统会自动点击淘宝“发送密码”并记录平台回执；淘宝决定密码发到绑定手机或主账号。收到后把「发货密码 xxx」发给飞书 ERP 机器人即可续跑；运行提醒统一发企业微信，飞书订单群只保留下单图片。",
        },
        "on_demand": web_agent_wake_service.status(db),
    }


class WakeAck(BaseModel):
    command_id: str
    agent_id: str = "windows-default"
    status: str
    detail: str = ""


@router.get("/wake/next")
def wake_next(agent_id: str = "windows-default", db: Session = Depends(get_db)):
    """Tiny Windows bridge heartbeat and pending start/stop command fetch."""
    return web_agent_wake_service.next_command(db, agent_id=agent_id)


@router.post("/wake/ack")
def wake_ack(payload: WakeAck, db: Session = Depends(get_db)):
    return web_agent_wake_service.acknowledge(
        db,
        command_id=payload.command_id,
        agent_id=payload.agent_id,
        status=payload.status,
        detail=payload.detail,
    )


@router.post("/wake/start")
def wake_start(db: Session = Depends(get_db)):
    """只唤醒 Windows Web-Agent，不启动订单/账单等业务任务。

    评价程序等轻量消费者在调用 Web-Agent 前使用这个端点。这样既能复用
    Windows 唤醒桥，又不会为了查评价而误触发一整轮 ERP 自动取数。
    """
    hb = web_agent_service.ensure_online(db, reason="review_status_sync")
    if not hb.get("online"):
        raise HTTPException(
            409,
            f"取数服务(:8500)唤醒失败: {hb.get('error', '')}。请检查 Windows 唤醒桥。",
        )
    return {"online": True, "agent": "web-agent"}


@router.post("/run")
def run_now(db: Session = Depends(get_db)):
    """立即取数: 全部类别强制触发 (忽略间隔)。后台执行, 进度看 status。"""
    hb = web_agent_service.ensure_online(db, reason="manual_full_pull")
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
    hb = web_agent_service.ensure_online(db, reason="manual_order_pull")
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
    """Web-Agent 卡点回调。

    企业微信硬边界（用户 2026-08-25）：
    - 登录/下载授权失效只提醒一次，扫码二维码也直接发企业微信；
    - 飞书订单群只保留工厂下单图片，卡点文字和二维码均不得写入飞书；
    - 扫码成功、超时、正常完成、文件预览不重复外发；
    - 所有允许外发的文本先经过金额脱敏。
    """
    import base64

    from app.services import notify_service
    result: dict = {"feishu": None, "wechat": None}
    notice_text = _sanitize_auth_notice(payload.text)

    # 成功事件只清除“已提醒”标记，不外发；下次真的再次失效时才允许重新提醒。
    if payload.kind == "scan_ok":
        opened = _auth_notice_open(db)
        opened.discard(_auth_notice_id(payload.text))
        _save_auth_notice_open(db, opened)
        result["wechat"] = "登录已恢复，未外发"
        return result

    # 超时不是新的登录失效事件，不重复打扰。
    if payload.kind == "scan_timeout":
        result["wechat"] = "扫码超时，未外发"
        return result

    if payload.kind == "scan_needed":
        notice_id = _auth_notice_id(payload.text)
        opened = _auth_notice_open(db)
        if notice_id in opened:
            result["wechat"] = "同一登录失效已提醒，未重复外发"
            return result
        ok, msg = notify_service.notify(
            db,
            notice_text or "取数登录已失效，请到 ERP 重新登录",
            level="warn",
            title="畔色 ERP | 自动取数需登录",
            wechat_allowed=True,
        )
        if ok:
            opened.add(notice_id)
            _save_auth_notice_open(db, opened)
            result["wechat"] = "已发企业微信"
        else:
            result["wechat"] = msg
        return result

    # 文件预览不外发；扫码二维码只允许进入企业微信。
    if payload.kind == "file":
        result["wechat"] = "文件事件未外发"
        return result

    is_visual = payload.kind in ("qr", "scan_wait")

    if is_visual and payload.image_b64:
        try:
            png = base64.b64decode(payload.image_b64, validate=True)
            sent = notify_service.notify_image(
                db,
                png,
                text=notice_text or "支付宝/淘宝导出需验证身份，请扫码后等待系统自动续跑。",
                level="warn",
                title="畔色 ERP | 自动取数需扫码",
                wechat_allowed=True,
            )
            result["wechat"] = (
                "文字和二维码已发企业微信"
                if sent.get("text") and sent.get("image")
                else f"企业微信发送未完成: {sent.get('detail') or 'unknown'}"
            )
        except Exception as e:  # noqa: BLE001
            result["wechat"] = f"企业微信发送失败: {type(e).__name__}: {e}"
        return result

    # 普通事件提醒也统一进企业微信，并显式越过“仅经营日报”的治噪默认门。
    wx_text = notice_text or "Panse 取数有新事件"
    ok, msg = notify_service.notify(
        db,
        wx_text,
        level="info",
        title="畔色 ERP | 自动取数",
        wechat_allowed=True,
    )
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
