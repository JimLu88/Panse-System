"""PC上线续跑取数 + 推送前新鲜度门 (pull_catchup_30min, 2026-07-07)。
根治 17:30 重启 PC → 18:00 订单取数没跑完 → 隔夜旧数据把已关闭单误推工厂群。"""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
import threading
from types import SimpleNamespace

from app import database
from app.services import agent_ingest_service as ai
from app.services import order_sheet_archive_service as oss
from app.services import order_sync_service, scheduler, settings_service, web_agent_service
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


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self._target = target

    def start(self):
        self._target()


class _DummyDb:
    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


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


def test_unresolved_shipping_password_can_be_found_after_midnight(db_session):
    yesterday = datetime.now() - timedelta(days=1)
    pending = ImportedFile(
        kind="taobao",
        original_filename="yesterday-shipping.xlsx",
        stored_path="/tmp/yesterday-shipping.xlsx",
        file_hash="yesterday-shipping-hash",
        source="api",
        row_summary={"agent_status": "pending_password"},
        created_at=yesterday,
        updated_at=yesterday,
    )
    db_session.add(pending)
    db_session.commit()

    assert ai.pending_shipping_password_files(db_session) == []
    assert ai.pending_shipping_password_files(db_session, all_dates=True) == [
        "yesterday-shipping.xlsx",
    ]


def test_latest_unresolved_shipping_batch_excludes_older_passwords(db_session):
    older = datetime.now() - timedelta(days=2)
    latest = datetime.now() - timedelta(days=1)
    for name, created_at in (("older.xlsx", older), ("latest.xlsx", latest)):
        db_session.add(ImportedFile(
            kind="taobao",
            original_filename=name,
            stored_path=f"/tmp/{name}",
            file_hash=f"hash-{name}",
            source="api",
            row_summary={"agent_status": "pending_password"},
            created_at=created_at,
            updated_at=created_at,
        ))
    db_session.commit()

    assert ai.pending_shipping_password_files(
        db_session, all_dates=True, latest_only=True,
    ) == ["latest.xlsx"]


def test_shipping_password_never_expires_by_age(db_session):
    settings_service.set_value(db_session, "taobao_shipping_pwd_latest", "example-password")
    settings_service.set_value(
        db_session,
        "taobao_shipping_pwd_at",
        (datetime.now() - timedelta(days=30)).isoformat(),
    )
    db_session.commit()

    assert ai._latest_shipping_password(db_session) == "example-password"


def test_new_shipping_report_records_existing_password_mismatch(
    db_session, monkeypatch, tmp_path,
):
    from app.services import automation_pipeline_service, import_storage

    settings_service.set_value(
        db_session, "taobao_shipping_pwd_latest", "existing-password"
    )
    report_file = tmp_path / "ExportOrderList-new.xlsx"
    report_file.write_bytes(b"encrypted-placeholder")
    monkeypatch.setattr(ai, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(ai, "_classify", lambda rel: "taobao_report")
    monkeypatch.setattr(
        ai,
        "_import_one",
        lambda db, category, path, raw: (
            "taobao",
            "pending_password",
            {"note": "发货报表解密失败: password mismatch"},
        ),
    )

    def _archive(db, *, content, original_name, kind, source, row_summary):
        row = ImportedFile(
            kind=kind,
            original_filename=original_name,
            stored_path=f"/tmp/{original_name}",
            file_hash=sha256(content).hexdigest(),
            source=source,
            row_summary=row_summary,
        )
        db.add(row)
        db.flush()
        return SimpleNamespace(file=row)

    monkeypatch.setattr(import_storage, "archive", _archive)

    result = ai.run_ingest(db_session, only_paths=[str(report_file)])

    assert result["pending"] == 1
    password_result = ai.get_shipping_password_result(db_session)
    assert password_result["status"] == "password_mismatch"
    assert password_result["pending_files"] == [report_file.name]
    assert "未过期" in password_result["reason"]
    pipeline = automation_pipeline_service.get_pipeline(db_session, "order_delivery")
    assert pipeline["waiting_input"] is True
    assert pipeline["final"] is True


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

    result = ai.finalize_order_pull_after_shipping_password(db_session, now=now)

    assert result["completed"] is True
    assert ai.order_data_fresh(db_session, not_before_hour=18) is True


def test_shipping_password_finalizes_successful_daytime_manual_recovery(db_session):
    now = datetime.now().replace(hour=14, minute=30, second=0, microsecond=0)
    state = ai._load_json(db_session, ai.KEY_STATE)
    state["taobao_report"] = now.replace(minute=15).isoformat(timespec="seconds")
    ai._save_json(db_session, ai.KEY_STATE, state)
    ai._save_json(
        db_session,
        ai.KEY_ORCH_STATE,
        {
            "manual_recovery": True,
            "started_at": now.replace(minute=0).isoformat(timespec="seconds"),
            "tasks": [{"task": "taobao_orders", "status": "done"}],
            "pending_manual": [],
        },
    )
    db_session.commit()

    result = ai.finalize_order_pull_after_shipping_password(db_session, now=now)

    assert result["completed"] is True


def test_shipping_password_rejects_daytime_scheduled_evidence(db_session):
    now = datetime.now().replace(hour=14, minute=30, second=0, microsecond=0)
    state = ai._load_json(db_session, ai.KEY_STATE)
    state["taobao_report"] = now.replace(minute=15).isoformat(timespec="seconds")
    ai._save_json(db_session, ai.KEY_STATE, state)
    ai._save_json(
        db_session,
        ai.KEY_ORCH_STATE,
        {
            "started_at": now.replace(minute=0).isoformat(timespec="seconds"),
            "tasks": [{"task": "taobao_orders", "status": "done"}],
        },
    )
    db_session.commit()

    result = ai.finalize_order_pull_after_shipping_password(db_session, now=now)

    assert result["completed"] is False
    assert result["reason"] == "missing_current_order_pull_evidence"


def test_manual_pull_persists_durable_success_evidence(monkeypatch):
    dummy = _DummyDb()
    saved: dict = {}
    monkeypatch.setattr(ai.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(database, "SessionLocal", lambda: dummy)
    monkeypatch.setattr(
        web_agent_service, "run_task", lambda *_args, **_kwargs: {"job": "job-1"})
    monkeypatch.setattr(
        web_agent_service,
        "wait_job",
        lambda *_args, **_kwargs: {
            "status": "done",
            "result": {"ok": True, "downloads": ["a.xlsx", "b.xlsx", "c.xlsx"]},
        },
    )
    monkeypatch.setattr(ai, "run_ingest", lambda _db: {"errors": 0, "pending": 1})
    monkeypatch.setattr(
        ai, "_save_json", lambda _db, _key, value: saved.clear() or saved.update(value))

    result = ai.pull_orders_async(dummy)

    assert result["started"] is True
    assert saved["manual_recovery"] is True
    assert saved["tasks"] == [{"task": "taobao_orders", "status": "done"}]
    assert saved["reports"] == ["a.xlsx", "b.xlsx", "c.xlsx"]


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

    result = ai.finalize_order_pull_after_shipping_password(db_session, now=now)

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


def test_daily_push_preserves_upstream_failure_when_stale(db_session, monkeypatch):
    from app.services import automation_pipeline_service

    automation_pipeline_service.record_failure(
        db_session,
        "order_delivery",
        "现有口令与新发货报表不匹配",
        retry_slots=[datetime.now().astimezone() + timedelta(hours=1)],
    )
    before = automation_pipeline_service.get_pipeline(db_session, "order_delivery")
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)
    monkeypatch.setattr(oss, "push_daily", _boom)

    result = scheduler._job_order_sheets_daily(db_session)

    after = automation_pipeline_service.get_pipeline(db_session, "order_delivery")
    assert result["_run_status"] == "skipped"
    assert result["upstream_error"] == "现有口令与新发货报表不匹配"
    assert after["failures"] == before["failures"]
    assert after["last_error"] == before["last_error"]


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


def test_pull_catchup_recovers_final_pipeline_when_fresh_evidence_arrives(
    db_session, monkeypatch
):
    from app.services import automation_pipeline_service as pipeline

    pipeline.record_failure(
        db_session,
        "order_delivery",
        "发货报表待口令",
        retry_slots=[],
    )
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 22)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: True)
    monkeypatch.setattr(
        oss,
        "reconcile_pending_delivery",
        lambda db, **kwargs: {
            "images_pushed": 1,
            "images_failed": 0,
            "images_remaining": 0,
        },
    )
    monkeypatch.setattr(
        scheduler,
        "_sync_factory_dispatch_after_orders",
        lambda db, result: result,
    )

    result = scheduler._job_pull_catchup(db_session)

    assert result["images_pushed"] == 1
    state = pipeline.get_pipeline(db_session, "order_delivery")
    assert state["success"] is True
    assert state["final"] is False


def test_pull_catchup_waits_when_pc_offline(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(ai, "pending_shipping_password_files", lambda db: [])
    monkeypatch.setattr(
        web_agent_service,
        "ensure_online",
        lambda db, **kwargs: {"online": False, "error": "唤醒桥未响应"},
    )
    res = scheduler._job_pull_catchup(db_session)
    assert res["waiting"] == "pc_offline"
    assert res["_run_status"] == "fail"
    assert "Web-Agent" in res["_error"]
    assert "唤醒桥未响应" in res["_error"]


def test_pull_catchup_runs_and_pushes_when_online(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    fresh = {"v": False}   # 一开始陈旧, orchestrate 后变新鲜
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: fresh["v"])
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(ai, "pending_shipping_password_files", lambda db: [])
    wake_calls = []
    monkeypatch.setattr(
        web_agent_service,
        "ensure_online",
        lambda db, **kwargs: wake_calls.append(kwargs) or {"online": True},
    )

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
    assert wake_calls == [{"reason": "order_delivery_retry", "wait_s": 75}]


def test_pull_catchup_still_stale_when_pull_fails(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)   # 始终陈旧(取数失败)
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(ai, "pending_shipping_password_files", lambda db: [])
    monkeypatch.setattr(web_agent_service, "ensure_online", lambda db, **kwargs: {"online": True})
    monkeypatch.setattr(ai, "orchestrate",
                        lambda db, **k: {"tasks": [], "pending_manual": [{"task": "taobao_orders"}]})
    res = scheduler._job_pull_catchup(db_session)
    assert res.get("still_stale") is True
    assert res["_run_status"] == "fail"
    assert "需人工登录" in res["_error"]


def test_pull_catchup_reports_task_failure_without_calling_it_login(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 19)
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: False)
    monkeypatch.setattr(ai, "is_running", lambda: False)
    monkeypatch.setattr(ai, "pending_shipping_password_files", lambda db: [])
    monkeypatch.setattr(web_agent_service, "ensure_online", lambda db, **kwargs: {"online": True})
    monkeypatch.setattr(
        ai,
        "orchestrate",
        lambda db, **kwargs: {
            "tasks": [{
                "task": "taobao_orders",
                "status": "error",
                "error": "订单报表:未收到文件",
            }],
            "pending_manual": [],
            "task_errors": [{
                "task": "taobao_orders",
                "reason": "任务error: 订单报表:未收到文件",
            }],
        },
    )

    res = scheduler._job_pull_catchup(db_session)

    assert "取数任务失败" in res["_error"]
    assert "订单报表:未收到文件" in res["_error"]
    assert "需人工登录" not in res["_error"]


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


def test_scan_done_with_business_failure_stays_pending(monkeypatch):
    dummy = _DummyDb()
    saved: list[str] = []
    monkeypatch.setattr(ai.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(database, "SessionLocal", lambda: dummy)
    monkeypatch.setattr(ai, "get_pending_scans", lambda _db: ["taobao_orders"])
    monkeypatch.setattr(ai, "get_scan_results", lambda _db: {})
    monkeypatch.setattr(
        settings_service, "set_value",
        lambda _db, _key, value, **_kwargs: saved.append(value),
    )
    monkeypatch.setattr(
        web_agent_service, "run_task",
        lambda *_args, **_kwargs: {"job": "job-1"},
    )
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_args, **_kwargs: {
            "status": "done",
            "result": {"ok": False, "reason": "session_expired"},
        },
    )
    monkeypatch.setattr(ai, "run_ingest", lambda _db: {"errors": [], "pending": 0})
    monkeypatch.setattr(oss, "reconcile_pending_delivery", _boom)

    result = ai.start_pending_scans(dummy)

    assert result["started"] is True
    assert '["taobao_orders"]' in saved


def test_successful_order_scan_marks_fresh_and_reconciles_delivery(monkeypatch):
    dummy = _DummyDb()
    saved_state: dict = {}
    reconciled: list[dict] = []
    monkeypatch.setattr(ai.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(database, "SessionLocal", lambda: dummy)
    monkeypatch.setattr(ai, "get_pending_scans", lambda _db: ["taobao_orders"])
    monkeypatch.setattr(ai, "get_scan_results", lambda _db: {})
    monkeypatch.setattr(settings_service, "set_value", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        web_agent_service, "run_task",
        lambda *_args, **_kwargs: {"job": "job-2"},
    )
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_args, **_kwargs: {
            "status": "done",
            "result": {"ok": True, "downloads": ["orders.xlsx"]},
        },
    )
    monkeypatch.setattr(ai, "run_ingest", lambda _db: {"errors": [], "pending": 0})
    monkeypatch.setattr(
        ai, "pending_shipping_password_files", lambda _db, **_kwargs: [])
    monkeypatch.setattr(ai, "_load_json", lambda _db, _key: {})
    monkeypatch.setattr(
        ai, "_save_json",
        lambda _db, _key, value: saved_state.update(value),
    )
    monkeypatch.setattr(
        oss, "reconcile_pending_delivery",
        lambda _db, **kwargs: reconciled.append(kwargs) or {"images_pushed": 0},
    )
    monkeypatch.setattr(
        "app.services.automation_pipeline_service.record_success",
        lambda *args, **kwargs: {"recovered": True},
    )

    result = ai.start_pending_scans(dummy)

    assert result["started"] is True
    assert datetime.fromisoformat(saved_state["taobao_orders_complete"])
    assert reconciled == [{"limit": 50, "quiet": True}]


def test_successful_main_flow_scan_closes_retry_immediately(monkeypatch):
    dummy = _DummyDb()
    saved_values: dict[str, str] = {}
    recovered: list[tuple[str, str]] = []
    artifacts = iter([
        {},
        {"main-flow.xlsx": (123, 456)},
    ])
    monkeypatch.setattr(ai.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(database, "SessionLocal", lambda: dummy)
    monkeypatch.setattr(ai, "get_pending_scans", lambda _db: [ai.MAIN_ALIPAY_FLOW_TASK])
    monkeypatch.setattr(
        settings_service,
        "set_value",
        lambda _db, key, value, **_kwargs: saved_values.update({key: value}),
    )
    monkeypatch.setattr(ai, "get_scan_results", lambda _db: {})
    monkeypatch.setattr(
        ai,
        "_task_run_variables",
        lambda *_args, **_kwargs: {"wait_scan": True},
    )
    monkeypatch.setattr(ai, "_main_alipay_artifacts", lambda: next(artifacts))
    monkeypatch.setattr(
        web_agent_service,
        "run_task",
        lambda *_args, **_kwargs: {"job": "job-main"},
    )
    monkeypatch.setattr(
        web_agent_service,
        "wait_job",
        lambda *_args, **_kwargs: {
            "status": "done",
            "result": {"ok": True},
        },
    )
    monkeypatch.setattr(ai, "run_ingest", lambda _db: {"errors": 0, "pending": 0})
    state: dict = {}
    monkeypatch.setattr(ai, "_load_json", lambda _db, _key: dict(state))
    monkeypatch.setattr(
        ai, "_save_json", lambda _db, _key, value: state.update(value),
    )
    monkeypatch.setattr(
        "app.services.automation_pipeline_service.record_success",
        lambda _db, name, **kwargs: (
            recovered.append((name, kwargs.get("success_detail")))
            or {"recovered": True}
        ),
    )

    result = ai.start_pending_scans(dummy)

    assert result["started"] is True
    assert json.loads(saved_values[ai.KEY_PENDING_SCAN]) == []
    scan_result = json.loads(saved_values[ai.KEY_SCAN_RESULTS])
    assert scan_result[ai.MAIN_ALIPAY_FLOW_TASK]["status"] == "success"
    assert datetime.fromisoformat(state[ai.STATE_MAIN_ALIPAY_FLOW])
    assert recovered == [
        ("flow_pull", "扫码后主力号流水已下载并完成入库"),
    ]


def test_main_flow_download_does_not_close_retry_when_ingest_fails(monkeypatch):
    dummy = _DummyDb()
    saved_values: dict[str, str] = {}
    recovered: list[str] = []
    artifacts = iter([{}, {"main-flow.xlsx": (123, 456)}])
    monkeypatch.setattr(ai.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(database, "SessionLocal", lambda: dummy)
    monkeypatch.setattr(ai, "get_pending_scans", lambda _db: [ai.MAIN_ALIPAY_FLOW_TASK])
    monkeypatch.setattr(ai, "get_scan_results", lambda _db: {})
    monkeypatch.setattr(
        ai,
        "_task_run_variables",
        lambda *_args, **_kwargs: {"wait_scan": True},
    )
    monkeypatch.setattr(ai, "_main_alipay_artifacts", lambda: next(artifacts))
    monkeypatch.setattr(
        settings_service,
        "set_value",
        lambda _db, key, value, **_kwargs: saved_values.update({key: value}),
    )
    monkeypatch.setattr(
        web_agent_service,
        "run_task",
        lambda *_args, **_kwargs: {"job": "job-main"},
    )
    monkeypatch.setattr(
        web_agent_service,
        "wait_job",
        lambda *_args, **_kwargs: {"status": "done", "result": {"ok": True}},
    )
    monkeypatch.setattr(
        ai,
        "run_ingest",
        lambda _db: {"errors": 1, "pending": 0},
    )
    monkeypatch.setattr(
        "app.services.automation_pipeline_service.record_success",
        lambda _db, name, **_kwargs: recovered.append(name),
    )

    result = ai.start_pending_scans(dummy)

    assert result["started"] is True
    assert json.loads(saved_values[ai.KEY_PENDING_SCAN]) == [ai.MAIN_ALIPAY_FLOW_TASK]
    scan = json.loads(saved_values[ai.KEY_SCAN_RESULTS])[ai.MAIN_ALIPAY_FLOW_TASK]
    assert scan["status"] == "failed"
    assert "失败 1 份，待处理 0 份" in scan["reason"]
    assert recovered == []
