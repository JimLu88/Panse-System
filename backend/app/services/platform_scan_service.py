"""Explicit Feishu platform selection. Login only; never dispatch business tasks."""
from __future__ import annotations

import hashlib
import json
import threading

from sqlalchemy import text

from app.services import settings_service, web_agent_service

SCAN_WORDS = frozenset(("扫码", "扫码登录", "开始扫码", "/扫码", "二维码", "发二维码", "发送二维码", "我要扫码"))
CHOICES = {"店铺后台扫码": "shop", "支付宝扫码": "alipay"}
# Checked against task login_url/login_key, NOT the task's broad platform label.
# bal_wanshifu is labelled taobao but logs in at wanshifu.com: excluded.
LOGIN_TASKS = {"shop": "taobao_orders", "alipay": "alipay_main"}
LOGIN_KEYS = {"shop": "taobao", "alipay": "alipay_main"}
ACTIVE_KEY = "feishu_platform_login_active"
MENU = "请选择扫码平台：\n1. 店铺后台扫码\n2. 支付宝扫码\n\n请回复完整文字「店铺后台扫码」或「支付宝扫码」。\n只登录所选平台，不自动拉单或运行其他待处理任务。"


def selection(value: str) -> str | None:
    return CHOICES.get(value.strip())


def status_text(db) -> str:
    value = _load(db, ACTIVE_KEY)
    names = {"shop": "店铺后台", "alipay": "支付宝"}
    states = {"claimed": "已受理", "running": "等待登录完成",
              "success": "会话已保存，是否有效须由原业务流程核验",
              "failed": "登录流程未完成", "unknown": "结果待核对，请勿重复启动"}
    if not value:
        return "尚无本入口的扫码记录。回复「扫码」选择平台。"
    return names.get(value.get("platform"), "平台") + "：" + states.get(value.get("state"), "结果待核对") + "。本入口不自动拉单。"


def _load(db, key):
    try:
        return json.loads(settings_service.get(db, key, env_fallback=False) or "{}")
    except (ValueError, TypeError):
        return {"state": "unknown"}


def _claim(db, request_id, platform):
    # Serialize claims across processes; SQLite tests use the process locks.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(20261021, 41)"))
    key = "feishu_login_msg_" + hashlib.sha256(request_id.encode()).hexdigest()
    if _load(db, key):
        db.rollback()
        return None, "这条扫码选择已受理，不重复启动。"
    active = _load(db, ACTIVE_KEY)
    if active.get("state") in {"claimed", "running", "unknown"}:
        db.rollback()
        return None, "已有登录流程进行中或结果待核对，请先完成原流程。"
    value = {"state": "claimed", "platform": platform, "request_key": key}
    settings_service.set_value(db, key, json.dumps(value))
    settings_service.set_value(db, ACTIVE_KEY, json.dumps(value))
    db.commit()  # durable intent before any network action
    return key, None


def _record(db, key, platform, state, job=None):
    value = {"state": state, "platform": platform, "request_key": key, "job": job}
    settings_service.set_value(db, key, json.dumps(value))
    if _load(db, ACTIVE_KEY).get("request_key") == key:
        settings_service.set_value(db, ACTIVE_KEY, json.dumps(value))
    db.commit()


def start_login(db, platform: str, *, request_id: str) -> dict:
    if platform not in LOGIN_TASKS or not request_id:
        return {"started": False, "reason": "请先选择店铺后台扫码或支付宝扫码。"}
    from app.services import agent_ingest_service as ingest
    if not ingest._scan_lock.acquire(blocking=False):
        return {"started": False, "reason": "已有扫码流程进行中，请先完成。"}
    if not ingest._orch_lock.acquire(blocking=False):
        ingest._scan_lock.release()
        return {"started": False, "reason": "已有取数任务进行中，请结束后再扫码。"}
    try:
        key, reason = _claim(db, request_id, platform)
        if not key:
            ingest._orch_lock.release()
            ingest._scan_lock.release()
            return {"started": False, "reason": reason}
    except Exception:
        ingest._orch_lock.release()
        ingest._scan_lock.release()
        raise

    def run():
        from app.database import SessionLocal
        d = SessionLocal()
        job = None
        state = "unknown"
        try:
            online = web_agent_service.ensure_online(d, reason="selected_platform_login")
            if not online.get("online"):
                state = "failed"  # no login submission was attempted
                return
            # No transport retry after an ambiguous submission; never call run_task.
            result = web_agent_service._post(
                d, "/api/login/" + LOGIN_TASKS[platform], {}, auto_wake=False,
            )
            job = result.get("job")
            if not job:
                return  # unknown intent is retained rather than replayed
            _record(d, key, platform, "running", job)
            final = web_agent_service.wait_job(d, job, timeout_s=720, poll_s=8)
            status = str(final.get("status") or "").lower()
            if status in {"done", "ok", "success"}:
                meta = final.get("result") or {}
                if meta.get("platform") == LOGIN_KEYS[platform] and meta.get("saved_at") and meta.get("cookie_count", 0) > 0:
                    state = "success"  # saved session, not proof of business readiness
                elif meta.get("ok") is False:
                    state = "failed"
            elif status in {"error", "failed", "cancelled", "canceled"}:
                state = "failed"
        except Exception:
            d.rollback()
            # Keep unknown: a timeout or process failure is not permission to retry.
        finally:
            try:
                _record(d, key, platform, state, job)
                if state in {"success", "failed"}:
                    web_agent_service.request_stop(d, reason="selected_platform_login_finished")
            finally:
                d.close()
                ingest._orch_lock.release()
                ingest._scan_lock.release()
    try:
        threading.Thread(target=run, name="feishu-platform-login", daemon=True).start()
    except Exception:
        ingest._orch_lock.release()
        ingest._scan_lock.release()
        raise
    return {"started": True, "platform": platform, "login_only": True}
