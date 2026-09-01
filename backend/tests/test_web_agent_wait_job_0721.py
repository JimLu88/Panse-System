from app.services import web_agent_service


def test_wait_job_retries_transient_read_timeout(monkeypatch):
    responses = iter([
        {"ok": False, "error": "ReadTimeout: Agent busy"},
        {"ok": True, "status": "done", "result": {"downloads": ["orders.xlsx"]}},
    ])
    monkeypatch.setattr(web_agent_service, "get_job", lambda db, job_id: next(responses))
    monkeypatch.setattr(web_agent_service.time, "sleep", lambda seconds: None)

    result = web_agent_service.wait_job(object(), "job1", timeout_s=30, poll_s=1)

    assert result["status"] == "done"


def test_wait_job_keeps_terminal_auth_error(monkeypatch):
    response = {"ok": False, "error": "token invalid"}
    monkeypatch.setattr(web_agent_service, "get_job", lambda db, job_id: response)

    assert web_agent_service.wait_job(object(), "job1", timeout_s=30) == response


def test_product_export_recovery_uses_readonly_record_endpoint(monkeypatch):
    calls = []
    recovery = {
        "id": "329309563", "sourceFileName": "exact", "rowCount": 6,
        "failedRowCount": 0, "gmtCreate": "2026-09-02 03:05:10",
        "expected_sha256": "f" * 64,
    }
    monkeypatch.setattr(
        web_agent_service, "_post",
        lambda _db, path, payload, timeout: (
            calls.append((path, payload, timeout)) or {"ok": True, "job": "job1"}))
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_a, **_k: {"status": "done", "result": {
            "ok": True, "xlsx_b64": "UEs=", "filename": "exact.xlsx",
            "sha256": "f" * 64, "record": recovery,
            "download_mode": "signed_record_url", "export_created": False,
            "platform_write": False,
        }})

    result = web_agent_service.export_product_prices(
        object(), recovery_record=recovery)

    assert calls == [("/api/product-price/export-recover", recovery, 30)]
    assert result["ok"] is True
    assert result["job_id"] == "job1"
    assert result["export_created"] is False


def test_product_export_normal_flow_sends_exact_item_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(
        web_agent_service, "_post",
        lambda _db, path, payload, timeout: (
            calls.append((path, payload, timeout)) or {"ok": False, "error": "stop"}))

    result = web_agent_service.export_product_prices(
        object(), item_ids=["2", "1", "2", "bad"])

    assert result["ok"] is False
    assert calls == [("/api/product-price/export", {"item_ids": ["1", "2"]}, 30)]
