# -*- coding: utf-8 -*-
"""Panse-Web-Agent (浏览器自动取数, Windows 宿主机 :8500) 的 HTTP 客户端。

集成形态 (交接方案 §7.1): Agent 独立进程/端口, ERP 经 HTTP 触发任务、轮询 job、
读共享 output 目录取文件。token 存 system_settings(web_agent_token, write-only)。
Agent 离线/未配 token 时所有调用返回带 ok=False 的字典, 绝不抛异常拖垮调用方。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import requests
from sqlalchemy.orm import Session

from app.services import settings_service

_log = logging.getLogger("panse.web_agent")

BASE_URL = os.environ.get("WEB_AGENT_URL", "http://host.docker.internal:8500")
_TIMEOUT = 10

TOKEN_KEY = "web_agent_token"


def _headers(db: Session) -> dict:
    token = settings_service.get(db, TOKEN_KEY) or ""
    return {"Authorization": f"Bearer {token}"} if token else {}


def _get_raw(db: Session, path: str, timeout: int = _TIMEOUT) -> dict:
    try:
        r = requests.get(f"{BASE_URL}{path}", headers=_headers(db), timeout=timeout)
        if r.status_code == 401:
            return {"ok": False, "error": "token 无效或未配置 (设置里填 Web-Agent token)"}
        r.raise_for_status()
        out = r.json()
        out.setdefault("ok", True)
        return out
    except Exception as e:  # noqa: BLE001 - 离线是常态场景, 不能抛
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _post_raw(db: Session, path: str, payload: Optional[dict] = None,
              timeout: int = _TIMEOUT) -> dict:
    try:
        r = requests.post(f"{BASE_URL}{path}", headers=_headers(db),
                          json=payload or {}, timeout=timeout)
        if r.status_code == 401:
            return {"ok": False, "error": "token 无效或未配置"}
        r.raise_for_status()
        out = r.json()
        out.setdefault("ok", True)
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def request_start(db: Session, *, reason: str) -> dict:
    from app.services import web_agent_wake_service

    return web_agent_wake_service.request(db, "start", reason=reason)


def request_stop(db: Session, *, reason: str) -> dict:
    from app.services import web_agent_wake_service

    return web_agent_wake_service.request(db, "stop", reason=reason)


def ensure_online(db: Session, *, reason: str, wait_s: int = 180) -> dict:
    """Ask the Windows bridge to start the full Agent, then wait for health."""
    from app.services import web_agent_wake_service

    current = _get_raw(db, "/api/tasks", timeout=5)
    if current.get("ok"):
        current["online"] = True
        return current
    command = request_start(db, reason=reason)
    deadline = time.monotonic() + max(5, wait_s)
    last = current
    while time.monotonic() < deadline:
        time.sleep(3)
        last = _get_raw(db, "/api/tasks", timeout=5)
        if last.get("ok"):
            last.update({"online": True, "wake_command_id": command.get("id")})
            return last
        # Surface a bridge-confirmed startup failure immediately.  Connection
        # timeouts alone only say that :8500 is offline; the wake command keeps
        # the actual reason (cold-start timeout, missing runtime, early exit).
        wake_status = web_agent_wake_service.status(db).get("command") or {}
        if (
            wake_status.get("id") == command.get("id")
            and wake_status.get("status") == "failed"
        ):
            return {
                "ok": False,
                "online": False,
                "wake_requested": True,
                "wake_command_id": command.get("id"),
                "error": wake_status.get("detail") or "Windows唤醒桥启动Web-Agent失败",
            }
    return {
        "ok": False,
        "online": False,
        "wake_requested": True,
        "wake_command_id": command.get("id"),
        "error": last.get("error") or "Windows唤醒桥未在时限内启动Web-Agent",
    }


def _get(db: Session, path: str, timeout: int = _TIMEOUT) -> dict:
    return _get_raw(db, path, timeout=timeout)


def _post(db: Session, path: str, payload: Optional[dict] = None,
          timeout: int = _TIMEOUT, *, auto_wake: bool = True) -> dict:
    out = _post_raw(db, path, payload, timeout=timeout)
    error = str(out.get("error") or "")
    if (
        auto_wake
        and not out.get("ok")
        and any(marker in error for marker in (
            "ConnectionError", "ConnectTimeout", "Connection refused",
            "Failed to establish", "WinError 10061",
        ))
    ):
        awakened = ensure_online(db, reason=f"POST {path}")
        if awakened.get("online"):
            return _post_raw(db, path, payload, timeout=timeout)
        out["wake"] = awakened
    return out


def health(db: Session) -> dict:
    """探活: Agent 是否在线 (token 不对也算"在线但未授权")。

    用 /api/tasks 而非 /api/health 探 — 后者会预检 LLM 通道, 可能 >5s 超时误判离线。
    """
    out = _get_raw(db, "/api/tasks", timeout=8)
    out["online"] = out.get("ok", False) or "token" in str(out.get("error", ""))
    return out


def list_tasks(db: Session) -> dict:
    """任务清单 (含 has_session 登录态 / has_credentials)。"""
    return _get(db, "/api/tasks")


def sessions(db: Session) -> dict:
    return _get(db, "/api/sessions")


def run_task(db: Session, task_id: str, variables: Optional[dict] = None) -> dict:
    """触发一个任务, 返回 {job: <id>}。Agent 侧自带同任务防并发。"""
    return _post(db, f"/api/tasks/{task_id}/run",
                 {"variables": variables or {}}, timeout=30)


def get_job(db: Session, job_id: str) -> dict:
    return _get(db, f"/api/jobs/{job_id}")


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload_file(db: Session, channel: str, phase: str, xlsx_bytes: bytes,
                filename: str, *, level: str = "SKU级", timeout: int = 30,
                start_dt: str | None = None, end_dt: str | None = None,
                campaign_title: str | None = None,
                campaign_phase: str | None = None,
                campaign_start: str | None = None,
                campaign_end: str | None = None,
                official_rate: str | None = None,
                campaign_id: str | None = None,
                united_activity_id: str | None = None,
                expected_rows: int | None = None) -> dict:
    """把 ERP 生成的 xlsx 传给 Web-Agent 千牛上传管线 (multipart)。返回 {job:<id>}。
    phase='stage' 挂文件停在提交前; phase='commit' ★不可逆★ 真提交 (仅比对表确认后调)。
    start_dt/end_dt(单品立减用, 'YYYY-MM-DD HH:MM:SS'): 给了则把千牛活动时间填成该精确档期。"""
    data = {"phase": phase, "level": level}
    if start_dt:
        data["start_dt"] = start_dt
    if end_dt:
        data["end_dt"] = end_dt
    for key, value in {
        "campaign_title": campaign_title,
        "campaign_phase": campaign_phase,
        "campaign_start": campaign_start,
        "campaign_end": campaign_end,
        "official_rate": official_rate,
        "campaign_id": campaign_id,
        "united_activity_id": united_activity_id,
    }.items():
        if value:
            data[key] = str(value)
    if expected_rows is not None:
        data["expected_rows"] = str(int(expected_rows))
    try:
        online = ensure_online(db, reason=f"upload:{channel}:{phase}")
        if not online.get("online"):
            return {"ok": False, "error": online.get("error", "取数服务未能按需启动")}
        r = requests.post(
            f"{BASE_URL}/api/upload/{channel}", headers=_headers(db),
            data=data,
            files={"file": (filename, xlsx_bytes, _XLSX_MIME)}, timeout=timeout)
        if r.status_code == 401:
            return {"ok": False, "error": "token 无效或未配置"}
        r.raise_for_status()
        out = r.json()
        out.setdefault("ok", True)
        return out
    except Exception as e:  # noqa: BLE001 - 离线是常态, 不抛
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def export_product_prices(db: Session, *, timeout_s: int = 260) -> dict:
    """全自动推标价·第1步: 让 Web-Agent 触发千牛「excel商品批量导出」+ 从下载中心下载发布模版。
    返回 {ok, xlsx_bytes, filename} 或 {ok:False, need_scan?/error}。异步导出~2min, 给足等待。"""
    import base64
    j = _post(db, "/api/product-price/export", {}, timeout=30)
    if not j.get("ok") or not j.get("job"):
        return {"ok": False, "error": j.get("error", "取数服务(:8500)未响应")}
    final = wait_job(db, j["job"], timeout_s=timeout_s)
    res = final.get("result") or {}
    if res.get("need_scan"):
        return {"ok": False, "need_scan": True, "message": res.get("message")}
    if not res.get("ok") or not res.get("xlsx_b64"):
        return {"ok": False, "error": res.get("message", "导出/下载失败"),
                "screenshot_base64": res.get("screenshot_base64")}
    return {"ok": True, "xlsx_bytes": base64.b64decode(res["xlsx_b64"]),
            "filename": res.get("filename"), "screenshot_base64": res.get("screenshot_base64")}


def campaign_export_items(db: Session, campaign_title: str, *, timeout_s: int = 720) -> dict:
    """活动生命周期 P4: WA「导出已报商品」(POST /api/campaign/export-items → wait_job)。
    WA 侧按标题从营销列表进活动并做★标题校验★, 不一致中止 (error=campaign_title_mismatch)。
    返回 {ok, xlsx_bytes, filename} 或 {ok:False, need_scan?/step?/error}。异步导出~2min, 给足等待。"""
    import base64
    j = _post(db, "/api/campaign/export-items",
              {"campaign_title": campaign_title}, timeout=30)
    if not j.get("ok") or not j.get("job"):
        return {"ok": False, "error": j.get("error", "取数服务(:8500)未响应")}
    final = wait_job(db, j["job"], timeout_s=timeout_s)
    res = final.get("result") or {}
    if res.get("need_scan"):
        return {"ok": False, "need_scan": True, "message": res.get("message")}
    if not res.get("ok") or not res.get("xlsx_b64"):
        return {"ok": False, "step": res.get("step"),
                "error": res.get("error") or res.get("message") or "活动导出/下载失败",
                "screenshot_base64": res.get("screenshot_base64")}
    return {"ok": True, "xlsx_bytes": base64.b64decode(res["xlsx_b64"]),
            "filename": res.get("filename"),
            "screenshot_base64": res.get("screenshot_base64")}


def campaign_inspect_detail(db: Session, campaign_title: str, *,
                            timeout_s: int = 200) -> dict:
    """只读进入指定活动，返回可见详情和 URL，供自动计划锁定档期/力度/活动 ID。"""
    j = _post(db, "/api/campaign/inspect-detail",
              {"campaign_title": campaign_title}, timeout=30)
    if not j.get("ok") or not j.get("job"):
        return {"ok": False, "error": j.get("error", "取数服务(:8500)未响应")}
    final = wait_job(db, j["job"], timeout_s=timeout_s)
    res = final.get("result") or {}
    if res.get("need_scan"):
        return {"ok": False, "need_scan": True, "error": "淘宝登录态已失效"}
    if not res.get("ok"):
        return {"ok": False, "step": res.get("step"),
                "error": res.get("error") or res.get("message") or "活动详情读取失败",
                "screenshot_base64": res.get("screenshot_base64")}
    return {"ok": True, "campaign_title": res.get("campaign_title"),
            "url": res.get("url"), "body_text": res.get("body_text") or "",
            "actual_titles": res.get("actual_titles") or [],
            "screenshot_base64": res.get("screenshot_base64")}


def campaign_discover(db: Session, *, timeout_s: int = 200) -> dict:
    """活动生命周期 P4: WA 抓千牛营销活动列表 (POST /api/campaign/discover → wait_job)。
    返回 {ok, campaigns: [{title, start, end, status, raw}, ...]} 或 {ok:False, error}。"""
    # The full Windows Agent is intentionally off while idle.  Wake it before
    # opening the discovery endpoint instead of first spending 30 seconds on a
    # guaranteed connection timeout.  Browser imports plus the 20-second wake
    # bridge poll can take about a minute on this workstation, so keep a real
    # startup margin here.
    online = ensure_online(db, reason="campaign_discovery", wait_s=120)
    if not online.get("online"):
        return {
            "ok": False,
            "error": online.get("error") or "活动发现前未能按需启动 Web-Agent",
            "wake": online,
        }
    j = _post(db, "/api/campaign/discover", {}, timeout=30, auto_wake=False)
    if not j.get("ok") or not j.get("job"):
        return {"ok": False, "error": j.get("error", "取数服务(:8500)未响应")}
    final = wait_job(db, j["job"], timeout_s=timeout_s)
    res = final.get("result") or {}
    if res.get("need_scan"):
        return {"ok": False, "need_scan": True, "message": res.get("message")}
    if not res.get("ok"):
        return {"ok": False,
                "error": res.get("error") or res.get("message") or "活动发现抓取失败",
                "tabs": res.get("tabs"),
                "calendar_opened": res.get("calendar_opened"),
                "calendar_error": res.get("calendar_error"),
                "screenshot_base64": res.get("screenshot_base64")}
    return {"ok": True, "campaigns": res.get("campaigns") or [],
            "count": res.get("count"), "tabs": res.get("tabs"),
            "calendar_opened": res.get("calendar_opened"),
            "calendar_error": res.get("calendar_error")}


def alipay_accounts(db: Session) -> list:
    """Web-Agent 已配的支付宝 API 账号 (含 id/name, 不含私钥)。"""
    s = _get(db, "/api/settings")
    return (s.get("alipay_accounts") or []) if s.get("ok", False) else []


def alipay_balance(db: Session, account_id: str) -> dict:
    """调 Web-Agent 用支付宝 API 查指定账号余额。返回 {ok, balance, raw{available/total/freeze}}。"""
    return _post(db, "/api/alipay/test", {"account_id": account_id}, timeout=30)


def alipay_bill(db: Session, account_id: str, bill_type: str, bill_date: str) -> dict:
    """调 Web-Agent 用支付宝 API 下账单 (落 Web-Agent output/alipay_api/, Panse 经共享目录读)。"""
    return _post(db, "/api/alipay/test-bill",
                 {"account_id": account_id, "bill_type": bill_type, "bill_date": bill_date},
                 timeout=60)


def wait_job(db: Session, job_id: str, *, timeout_s: int = 900,
             poll_s: int = 10) -> dict:
    """轮询 job 直到结束/超时。返回最终 job dict (status: done/error/timeout)。"""
    deadline = time.monotonic() + timeout_s
    last: dict = {}
    while time.monotonic() < deadline:
        last = get_job(db, job_id)
        status = (last.get("status") or "").lower()
        if status in ("done", "ok", "success", "error", "failed"):
            return last
        if not last.get("ok", True) and "error" in last:
            # Long deterministic browser actions can temporarily block the
            # Agent HTTP loop. A ReadTimeout/ConnectTimeout is not the job's
            # terminal state; keep polling instead of launching the next task
            # concurrently. Authentication/configuration errors remain terminal.
            err = str(last.get("error") or "")
            if any(marker in err for marker in (
                    "ReadTimeout", "ConnectTimeout", "ConnectionError",
                    "Connection aborted", "timed out")):
                time.sleep(poll_s)
                continue
            return last
        time.sleep(poll_s)
    last["status"] = "timeout"
    return last
