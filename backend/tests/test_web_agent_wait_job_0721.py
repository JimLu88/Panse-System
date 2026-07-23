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
