import hashlib
import json
from datetime import datetime, timedelta

from app.models.scheduled_job import ScheduledJobRun
from app.services import agent_ingest_service as ai, scheduler


def evidence(now, status="timeout", artifacts=None):
    return {"started_at": now.isoformat(), "order_business_date": now.date().isoformat(),
            "order_batch_id": f"orders-{now:%Y%m%d}-" + "a" * 32,
            "order_attempt_id": "b" * 32,
            "tasks": [{"task": "taobao_orders", "status": status, "artifacts": artifacts or []}]}


def test_finance_files_do_not_replace_order_batch(db_session):
    now = datetime.now().replace(hour=18)
    original = evidence(now)
    db_session.add(ScheduledJobRun(job_id="daily_0630_web_agent", job_label="orders",
        status="fail", started_at=now, result_summary=original))
    ai._save_json(db_session, ai.KEY_ORCH_STATE, {
        "started_at": now.replace(hour=20).isoformat(),
        "tasks": [{"task": "alipay_main", "status": "done"}], "artifacts": ["finance.csv"]})
    db_session.commit()
    assert ai.latest_order_pull_evidence(db_session) == original


def test_current_timeout_never_falls_back_to_yesterday_password(db_session):
    now = datetime.now().replace(hour=21)
    today = evidence(now.replace(hour=18))
    yesterday = evidence(now - timedelta(days=1), "done", ["old_shipping.xlsx"])
    ai._save_json(db_session, ai.KEY_ORCH_STATE, today)
    db_session.add(ScheduledJobRun(job_id="daily_0630_web_agent", job_label="orders",
        status="fail", started_at=now - timedelta(days=1), result_summary=yesterday))
    db_session.commit()
    result = ai.finalize_order_pull_after_shipping_password(
        db_session, now=now, resolved_artifacts=["new_shipping.xlsx"])
    assert result["reason"] == "current_order_pull_not_complete"


def test_shipping_callback_cannot_select_unrelated_completed_batch(db_session):
    now = datetime.now().replace(hour=21)
    ai._save_json(db_session, ai.KEY_ORCH_STATE, evidence(now, "done", ["other.xlsx"]))
    db_session.commit()
    result = ai.finalize_order_pull_after_shipping_password(
        db_session, now=now, resolved_artifacts=["actual.xlsx"])
    assert result["reason"] == "missing_current_order_pull_evidence"


def test_push_waits_for_running_export_without_counting_failure(db_session, monkeypatch):
    monkeypatch.setattr(ai, "order_data_fresh", lambda *a, **k: False)
    ai._save_json(db_session, ai.KEY_ORCH_STATE, {"running": True, "current": "taobao_orders"})
    monkeypatch.setattr(scheduler, "_record_pipeline_result", lambda *a, **k: (_ for _ in ()).throw(AssertionError("counted failure")))
    assert scheduler._job_order_sheets_daily(db_session)["skipped"] == "order_pull_in_progress"


def test_late_receipt_validates_hash_and_recovers_only_exact_manifest(db_session, monkeypatch, tmp_path):
    now = datetime.now().replace(hour=18)
    original = evidence(now)
    ai._save_json(db_session, ai.KEY_ORCH_STATE, original)
    monkeypatch.setattr(ai, "OUTPUT_DIR", tmp_path)
    files = []
    for role in ("orders", "sales_detail", "shipping"):
        path = tmp_path / now.date().isoformat() / "taobao" / f"{role}.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(role.encode())
        files.append({"path": path.relative_to(tmp_path).as_posix(), "report": role,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    receipt = {"version": 1, "status": "done", "order_batch_id": original["order_batch_id"],
               "order_attempt_id": original["order_attempt_id"],
               "order_business_date": now.date().isoformat(), "started_at": now.isoformat(), "artifacts": files}
    root = tmp_path / "order-runs"
    root.mkdir()
    target = root / (original["order_attempt_id"] + ".json")
    target.write_text(json.dumps(receipt))
    calls = []
    monkeypatch.setattr(ai, "run_ingest", lambda *a, **k: calls.append(k) or {})
    assert ai.recover_order_receipt(db_session)["recovered"]
    assert len(calls) == 1 and len(calls[0]["only_paths"]) == 3
    assert calls[0]["order_batch_id"] == original["order_batch_id"]
    assert ai.latest_order_pull_evidence(db_session)["tasks"][0]["status"] == "done"
    monkeypatch.setattr(ai, "taobao_artifact_states", lambda *a, **k: {name: "imported" for name in calls[0]["artifact_roles"]})
    assert ai.recover_order_receipt(db_session)["recovered"]
    assert len(calls) == 1
    (tmp_path / files[0]["path"]).write_bytes(b"changed")
    assert ai.recover_order_receipt(db_session)["reason"] == "invalid_order_receipt"
    assert len(calls) == 1


def test_exact_local_path_not_replaced_by_same_name(tmp_path, monkeypatch):
    import os
    monkeypatch.setattr(ai, "OUTPUT_DIR", tmp_path)
    first = tmp_path / "first" / "report.xlsx"
    later = tmp_path / "later" / "report.xlsx"
    for p in (first, later):
        p.parent.mkdir()
        p.write_bytes(b"data")
    os.utime(later, (2000000000, 2000000000))
    assert ai._ingest_candidates([str(first)]) == [first]
