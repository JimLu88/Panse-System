"""PC上线续跑取数 + 推送前新鲜度门 (pull_catchup_30min, 2026-07-07)。
根治 17:30 重启 PC → 18:00 订单取数没跑完 → 隔夜旧数据把已关闭单误推工厂群。"""
from __future__ import annotations

from datetime import datetime, timedelta
import threading

from app.services import agent_ingest_service as ai
from app.services import order_sheet_archive_service as oss
from app.services import order_sync_service, scheduler, web_agent_service
from app.models.import_file import ImportedFile


def _set_taobao_report(db, dt):
    state = ai._load_json(db, ai.KEY_STATE)
    if dt is None:
        state.pop("taobao_report", None)
        state.pop("taobao_orders_complete", None)
    else:
        state["taobao_report"] = dt.isoformat(timespec="seconds")
        state["taobao_orders_complete"] = dt.isoformat(timespec="seconds")
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


def test_fresh_after_daily_cutoff(db_session):
    today = datetime.now().replace(hour=1, minute=15, second=0, microsecond=0)
    _set_taobao_report(db_session, today)
    assert ai.order_data_fresh(db_session) is True
    assert ai.order_data_fresh(db_session, not_before_hour=18) is False
    _set_taobao_report(db_session, today.replace(hour=18, minute=5))
    assert ai.order_data_fresh(db_session, not_before_hour=18) is True


def test_fresh_after_cutoff_waits_for_shipping_password(db_session):
    now = datetime.now().replace(hour=18, minute=5, second=0, microsecond=0)
    _set_taobao_report(db_session, now)
    pending = ImportedFile(
        kind="taobao",
        original_filename="shipping.xlsx",
        stored_path="/tmp/shipping.xlsx",
        file_hash="shipping-hash",
        source="api",
        row_summary={"agent_status": "pending_password"},
    )
    db_session.add(pending)
    db_session.commit()
    assert ai.order_data_fresh(db_session, not_before_hour=18) is False

    resolved = ImportedFile(
        kind="taobao",
        original_filename="shipping.xlsx",
        stored_path="/tmp/shipping-resolved.xlsx",
        file_hash="shipping-hash",
        source="api",
        row_summary={"agent_status": "imported"},
    )
    db_session.add(resolved)
    db_session.commit()
    assert ai.order_data_fresh(db_session, not_before_hour=18) is True


def test_shipping_password_finalizes_completed_evening_pull(db_session):
    now = datetime.now().replace(hour=18, minute=25, second=0, microsecond=0)
    state = ai._load_json(db_session, ai.KEY_STATE)
    state["taobao_report"] = now.replace(minute=15).isoformat(timespec="seconds")
    ai._save_json(db_session, ai.KEY_STATE, state)
    ai._save_json(
        db_session,
        ai.KEY_ORCH_STATE,
        {
            "started_at": now.replace(minute=0).isoformat(timespec="seconds"),
            "tasks": [{"task": "taobao_orders", "status": "done"}],
            "pending_manual": [],
        },
    )
    db_session.commit()

    result = ai.finalize_order_pull_after_shipping_password(db_session)

    assert result["completed"] is True
    assert ai.order_data_fresh(db_session, not_before_hour=18) is True


def test_shipping_password_never_finalizes_without_current_pull_evidence(db_session):
    result = ai.finalize_order_pull_after_shipping_password(db_session)

    assert result["completed"] is False
    assert result["reason"] == "missing_current_order_pull_evidence"


def test_shipping_password_uses_durable_daily_job_evidence_when_state_was_overwritten(
    db_session,
):
    from app.models.scheduled_job import ScheduledJobRun

    now = datetime.now().replace(hour=20, minute=30, second=0, microsecond=0)
    state = ai._load_json(db_session, ai.KEY_STATE)
    state["taobao_report"] = now.replace(hour=18, minute=15).isoformat(timespec="seconds")
    ai._save_json(db_session, ai.KEY_STATE, state)
    ai._save_json(
        db_session,
        ai.KEY_ORCH_STATE,
        {
            "started_at": now.isoformat(timespec="seconds"),
            "tasks": [{"task": "bal_ads", "status": "done"}],
        },
    )
    db_session.add(
        ScheduledJobRun(
            job_id="daily_0630_web_agent",
            job_label="Web-Agent 自动取数编排(18:00)",
            status="fail",
            result_summary={
                "tasks": [{"task": "taobao_orders", "status": "done"}],
                "pending_manual": [],
            },
            started_at=now.replace(hour=18, minute=0),
            completed_at=now.replace(hour=18, minute=25),
        )
    )
    db_session.commit()

    result = ai.finalize_order_pull_after_shipping_password(db_session)

    assert result["completed"] is True
    assert ai.order_data_fresh(db_session, not_before_hour=18) is True


def test_orchestrate_serializes_scheduled_runs(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    first_result = {}

    def _locked(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=2)
        return {"ok": True}

    monkeypatch.setattr(ai, "_orchestrate_locked", _locked)
    t = threading.Thread(
        target=lambda: first_result.update(ai.orchestrate(None)),
        daemon=True)
    t.start()
    assert entered.wait(timeout=1)

    second = ai.orchestrate(None)
    assert second["already_running"] is True
    assert second["skipped"] == ["orchestrate_running"]

    release.set()
    t.join(timeout=2)
    assert first_result == {"ok": True}


# ---------- 推送新鲜度门 (核心: 陈旧数据绝不推) ----------

def test_catchup_push_skipped_when_stale(db_session, monkeypatch):
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)
    monkeypatch.setattr(oss, "generate_pending", _boom)
    monkeypatch.setattr(oss, "push_pending_images", _boom)
    assert scheduler._job_order_sheets_catchup(db_session) == {"skipped": "stale_order_data"}


def test_daily_push_skipped_when_stale(db_session, monkeypatch):
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)
    monkeypatch.setattr(oss, "push_daily", _boom)
    res = scheduler._job_order_sheets_daily(db_session)
    assert res["skipped"] == "stale_order_data"


def test_catchup_push_runs_when_fresh(db_session, monkeypatch):
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: True)
    monkeypatch.setattr(oss, "generate_pending", lambda db: {"generated": 0})
    monkeypatch.setattr(oss, "push_pending_images", lambda *a, **k: {"pushed": 3, "remaining": 0})
    res = scheduler._job_order_sheets_catchup(db_session)
    assert res["images_pushed"] == 3


def test_hourly_ingest_applies_remote_transition_after_new_report(db_session, monkeypatch):
    monkeypatch.setattr(ai, "run_ingest", lambda db: {"imported": 1, "errors": 0})
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})
    monkeypatch.setattr(web_agent_service, "list_tasks", lambda db: {"tasks": []})
    monkeypatch.setattr(order_sync_service, "backfill_product_code", lambda db: None)
    monkeypatch.setattr(order_sync_service, "backfill_code_from_taobao_title", lambda db: None)
    monkeypatch.setattr(oss, "void_remote_pushed", lambda db: {
        "voided_remote": ["O1"],
        "remote_transitions": [{"order_no": "O1", "old_factory_no": 322, "remote_seq": 39}],
        "feishu_notified": ["O1"], "feishu_failed": [],
    })
    monkeypatch.setattr(oss, "repush_activated", lambda db: {})
    monkeypatch.setattr(oss, "assign_remote_seqs", lambda db: {})
    monkeypatch.setattr(oss, "generate_pending", lambda db: {"generated": 0})
    res = scheduler._job_ingest_scan(db_session)
    assert res["remote_voided"] == ["O1"]
    assert res["remote_feishu_notified"] == ["O1"]


# ---------- pull_catchup 分支 ----------

def test_pull_catchup_off_window(db_session, monkeypatch):
    # 只在 18:00~23:00 补; 早上10点、晚23点都不跑(避免抢在每日定时前)
    for h in (10, 23):
        monkeypatch.setattr(scheduler, "_now_hour", lambda h=h: h)
        assert scheduler._job_pull_catchup(db_session) == {"skipped": "off_window"}


def test_pull_catchup_already_fresh(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: True)
    monkeypatch.setattr(
        oss,
        "reconcile_pending_delivery",
        lambda db, **kwargs: {
            "images_pushed": 2,
            "images_failed": 0,
            "images_remaining": 0,
        },
    )
    result = scheduler._job_pull_catchup(db_session)
    assert result["ok"] == "fresh_delivery_reconciled"
    assert result["images_pushed"] == 2


def test_pull_catchup_waits_when_pc_offline(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(ai, "pending_shipping_password_files", lambda db: [])
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": False})
    res = scheduler._job_pull_catchup(db_session)
    assert res["waiting"] == "pc_offline"
    assert res["_run_status"] == "fail"
    assert "Web-Agent" in res["_error"]


def test_pull_catchup_runs_and_pushes_when_online(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    fresh = {"v": False}   # 一开始陈旧, orchestrate 后变新鲜
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: fresh["v"])
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(ai, "pending_shipping_password_files", lambda db: [])
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})

    def _orch(db, **k):
        assert k["force_orders"] is True
        assert k["orders_only"] is True
        fresh["v"] = True
        return {"tasks": [{"status": "done"}], "pending_manual": []}

    monkeypatch.setattr(ai, "orchestrate", _orch)
    monkeypatch.setattr(
        oss,
        "reconcile_pending_delivery",
        lambda db, **kwargs: {
            "generated": {"generated": 1},
            "images_pushed": 2,
            "images_failed": 0,
            "images_remaining": 0,
        },
    )
    res = scheduler._job_pull_catchup(db_session)
    assert res["ran_orchestrate"] is True
    assert res["images_pushed"] == 2


def test_pull_catchup_still_stale_when_pull_fails(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)   # 始终陈旧(取数失败)
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(ai, "pending_shipping_password_files", lambda db: [])
    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})
    monkeypatch.setattr(ai, "orchestrate",
                        lambda db, **k: {"tasks": [], "pending_manual": [{"task": "taobao_orders"}]})
    res = scheduler._job_pull_catchup(db_session)
    assert res.get("still_stale") is True
    assert res["_run_status"] == "fail"
    assert "需人工登录" in res["_error"]


def test_pull_catchup_reports_pending_password(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(
        ai, "pending_shipping_password_files",
        lambda db: ["shipping.xlsx"])
    monkeypatch.setattr(web_agent_service, "health", _boom)
    monkeypatch.setattr(ai, "orchestrate", _boom)

    res = scheduler._job_pull_catchup(db_session)
    assert res["_run_status"] == "fail"
    assert res["waiting"] == "shipping_password"
    assert "shipping.xlsx" in res["_error"]


def test_pull_catchup_registered():
    scheduler._register_default_jobs()
    ids = {j["job_id"] for j in scheduler.list_jobs()}
    assert "pull_catchup_30min" in ids
    assert "daily_2030_finance_agent" in ids
