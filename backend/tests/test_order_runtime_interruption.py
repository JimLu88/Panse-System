import json
from datetime import datetime

import pytest

from app.services import agent_ingest_service as ai, web_agent_service as wa, scheduler


def test_interrupted_is_maintenance_but_offline_remains_execution():
    from app.services.automation_pipeline_service import classify_failure
    assert classify_failure("order_export_interrupted")["owner"] == "program_maintenance"
    assert classify_failure("order_export_unknown")["owner"] == "program_maintenance"
    assert classify_failure("order_export_owner_unavailable")["owner"] == "execution"


def test_missing_job_retains_failure_reason(monkeypatch):
    monkeypatch.setattr(wa, "get_job", lambda *a: {"ok": False, "error": "HTTPError: 404 no such job"})
    out = wa.wait_job(None, "old-job")
    assert out["status"] == "error" and "404" in out["error"]


def test_transient_timeout_does_not_end_live_job(monkeypatch):
    answers = iter([{"ok": False, "error": "ReadTimeout"}, {"ok": True, "status": "done"}])
    monkeypatch.setattr(wa, "get_job", lambda *a: next(answers))
    monkeypatch.setattr(wa.time, "sleep", lambda *a: None)
    assert wa.wait_job(None, "id")["status"] == "done"


@pytest.mark.parametrize("live,reason", [("running", "order_export_running"), ("interrupted", "order_export_interrupted"), ("unknown", "order_export_unknown"), ("done", "order_export_terminal_pending"), ("offline", "order_export_owner_unavailable")])
def test_running_receipt_needs_live_identity(db_session, monkeypatch, tmp_path, live, reason):
    now = datetime.now().replace(hour=18)
    value = {"started_at": now.isoformat(), "order_business_date": now.date().isoformat(),
             "order_batch_id": f"orders-{now:%Y%m%d}-" + "a" * 32, "order_attempt_id": "b" * 32,
             "tasks": [{"task": "taobao_orders", "status": "timeout"}]}
    ai._save_json(db_session, ai.KEY_ORCH_STATE, value)
    monkeypatch.setattr(ai, "OUTPUT_DIR", tmp_path)
    path = tmp_path / "order-runs" / ("b" * 32 + ".json")
    path.parent.mkdir()
    path.write_text(json.dumps({**value, "version": 1, "status": "running"}))
    before = path.read_bytes()
    monkeypatch.setattr(wa, "order_receipt_status", lambda *a: {**value, "ok": live != "offline", "status": live})
    assert ai.recover_order_receipt(db_session)["reason"] == reason
    assert path.read_bytes() == before


@pytest.mark.parametrize("reason", ["order_export_interrupted", "order_export_unknown", "order_export_owner_unavailable", "order_export_partial"])
def test_catchup_never_reexports_unknown_original(db_session, monkeypatch, reason):
    monkeypatch.setattr(scheduler, "_now_hour", lambda: 20)
    monkeypatch.setattr(ai, "recover_order_receipt", lambda *a: {"recovered": False, "reason": reason})
    monkeypatch.setattr(wa, "ensure_online", lambda *a, **k: {"online": False})
    monkeypatch.setattr(ai, "orchestrate", lambda *a, **k: pytest.fail("blind export"))
    monkeypatch.setattr(scheduler, "_record_pipeline_result", lambda db, key, result, **kw: result)
    out = scheduler._job_pull_catchup(db_session)
    assert out["_run_status"] == "fail" and reason in out["_error"]
