"""PC上线续跑取数 + 推送前新鲜度门 (pull_catchup_30min, 2026-07-07)。
根治 17:30 重启 PC → 18:00 订单取数没跑完 → 隔夜旧数据把已关闭单误推工厂群。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services import agent_ingest_service as ai
from app.services import order_sheet_archive_service as oss
from app.services import scheduler, web_agent_service


def _set_taobao_report(db, dt):
    state = ai._load_json(db, ai.KEY_STATE)
    if dt is None:
        state.pop("taobao_report", None)
    else:
        state["taobao_report"] = dt.isoformat(timespec="seconds")
    ai._save_json(db, ai.KEY_STATE, state)
    db.commit()


def _boom(*a, **k):
    raise AssertionError("陈旧数据下不应生成/推送")


# ---------- order_data_fresh ----------

def test_fresh_true_when_report_today(db_session):
    _set_taobao_report(db_session, datetime.now())
    assert ai.order_data_fresh(db_session) is True


def test_fresh_false_when_report_yesterday(db_session):
    _set_taobao_report(db_session, datetime.now() - timedelta(days=1))
    assert ai.order_data_fresh(db_session) is False


def test_fresh_false_when_report_missing(db_session):
    _set_taobao_report(db_session, None)
    assert ai.order_data_fresh(db_session) is False


# ---------- 推送新鲜度门 (核心: 陈旧数据绝不推) ----------

def test_catchup_push_skipped_when_stale(db_session, monkeypatch):
    monkeypatch.setattr(ai, "order_data_fresh", lambda db: False)
    monkeypatch.setattr(oss, "generate_pending", _boom)
    monkeypatch.setattr(oss, "push_pending_images", _boom)
    assert scheduler._job_order_sheets_catchup(db_session) == {"skipped": "stale_order_data"}


def test_daily_push_skipped_when_stale(db_session, monkeypatch):
    monkeypatch.setattr(ai, "order_data_fresh", lambda db: False)
    monkeypatch.setattr(oss, "push_daily", _boom)
    res = scheduler._job_order_sheets_daily(db_session)
    assert res["skipped"] == "stale_order_data"


def test_catchup_push_runs_when_fresh(db_session, monkeypatch):
    monkeypatch.setattr(ai, "order_data_fresh", lambda db: True)
    monkeypatch.setattr(oss, "generate_pending", lambda db: {"generated": 0})
    monkeypatch.setattr(oss, "push_pending_images", lambda *a, **k: {"pushed": 3, "remaining": 0})
    res = scheduler._job_order_sheets_catchup(db_session)
    assert res["images_pushed"] == 3


# ---------- pull_catchup 分支 ----------

def test_pull_catchup_off_hours(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 23)
    assert scheduler._job_pull_catchup(db_session) == {"skipped": "off_hours"}


def test_pull_catchup_already_fresh(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 10)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db: True)
    assert scheduler._job_pull_catchup(db_session) == {"ok": "already_fresh"}


def test_pull_catchup_waits_when_pc_offline(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 10)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db: False)
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": False})
    assert scheduler._job_pull_catchup(db_session) == {"waiting": "pc_offline"}


def test_pull_catchup_runs_and_pushes_when_online(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 10)
    fresh = {"v": False}   # 一开始陈旧, orchestrate 后变新鲜
    monkeypatch.setattr(ai, "order_data_fresh", lambda db: fresh["v"])
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})

    def _orch(db, **k):
        fresh["v"] = True
        return {"tasks": [{"status": "done"}], "pending_manual": []}

    monkeypatch.setattr(ai, "orchestrate", _orch)
    monkeypatch.setattr(oss, "generate_pending", lambda db: {"generated": 1})
    monkeypatch.setattr(oss, "push_pending_images", lambda *a, **k: {"pushed": 2, "remaining": 0})
    res = scheduler._job_pull_catchup(db_session)
    assert res["ran_orchestrate"] is True
    assert res["images_pushed"] == 2


def test_pull_catchup_still_stale_when_pull_fails(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 10)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db: False)   # 始终陈旧(取数失败)
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})
    monkeypatch.setattr(ai, "orchestrate",
                        lambda db, **k: {"tasks": [], "pending_manual": [{"task": "taobao_orders"}]})
    res = scheduler._job_pull_catchup(db_session)
    assert res.get("still_stale") is True


def test_pull_catchup_registered():
    scheduler._register_default_jobs()
    ids = {j["job_id"] for j in scheduler.list_jobs()}
    assert "pull_catchup_30min" in ids
