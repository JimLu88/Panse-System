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


def _get(db: Session, path: str, timeout: int = _TIMEOUT) -> dict:
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


def _post(db: Session, path: str, payload: Optional[dict] = None,
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


def health(db: Session) -> dict:
    """探活: Agent 是否在线 (token 不对也算"在线但未授权")。

    用 /api/tasks 而非 /api/health 探 — 后者会预检 LLM 通道, 可能 >5s 超时误判离线。
    """
    out = _get(db, "/api/tasks", timeout=8)
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
            return last
        time.sleep(poll_s)
    last["status"] = "timeout"
    return last
