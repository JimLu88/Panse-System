import base64
from types import SimpleNamespace

from app.api import campaigns as campaign_api
from app.services import web_agent_service


class _DB:
    def __init__(self, plan=None):
        self.plan = plan

    def get(self, _model, _pk):
        return self.plan


def test_single_discount_error_service_decodes_original_file(monkeypatch):
    raw = b"PK\x03\x04original-xlsx"
    monkeypatch.setattr(
        web_agent_service, "_post",
        lambda *args, **kwargs: {"ok": True, "job": "job-1"})
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *args, **kwargs: {
            "result": {
                "ok": True,
                "activity_id": "142761375321",
                "success_count": 0,
                "failed_count": 20,
                "detail_rows": 0,
                "empty_detail": True,
                "filename": "错误文件.xlsx",
                "xlsx_b64": base64.b64encode(raw).decode("ascii"),
            }
        })

    result = web_agent_service.single_discount_error_file(object(), "142761375321")

    assert result["ok"] is True
    assert result["xlsx_bytes"] == raw
    assert result["failed_count"] == 20
    assert result["empty_detail"] is True


def test_single_discount_error_endpoint_returns_evidence_headers(monkeypatch):
    plan = SimpleNamespace(id=5)
    monkeypatch.setattr(
        web_agent_service, "single_discount_error_file",
        lambda *args, **kwargs: {
            "ok": True,
            "xlsx_bytes": b"PK\x03\x04file",
            "filename": "单品立减错误.xlsx",
            "activity_id": "142761375321",
            "success_count": 0,
            "failed_count": 20,
            "detail_rows": 0,
            "empty_detail": True,
        })

    response = campaign_api.download_single_discount_error_file(
        5, "142761375321", db=_DB(plan), _=SimpleNamespace())

    assert response.body == b"PK\x03\x04file"
    assert response.headers["x-panse-failed-count"] == "20"
    assert response.headers["x-panse-empty-detail"] == "true"


def test_super_reduce_operation_feedback_uses_read_only_fallback(monkeypatch):
    plan = SimpleNamespace(
        id=5,
        campaign_type="super_reduce",
        qn_campaign_title="超级立减长期活动",
        name="超级立减",
        remark="",
    )
    monkeypatch.setattr(
        web_agent_service, "super_reduce_feedback",
        lambda *args, **kwargs: {
            "ok": True,
            "xlsx_bytes": b"PK\x03\x04feedback",
            "filename": "操作反馈.xlsx",
            "feedback": {"failed": [], "by_reason": []},
        })

    response = campaign_api.download_campaign_operation_feedback(
        5, db=_DB(plan), _=SimpleNamespace())

    assert response.body == b"PK\x03\x04feedback"
    assert response.headers["x-panse-failed-rows"] == "0"


def test_operation_feedback_prefers_immutable_campaign_identity(monkeypatch):
    plan = SimpleNamespace(
        id=6,
        campaign_type="big88",
        qn_campaign_title="开学季-现货",
        name="开学季",
        remark="campaignId=123; unitedActivityId=456",
    )
    called = {}

    def _feedback(_db, title, **kwargs):
        called.update({"title": title, **kwargs})
        return {
            "ok": True,
            "xlsx_bytes": b"PK\x03\x04feedback",
            "filename": "操作反馈.xlsx",
            "feedback": {"failed": [{"item_id": "1"}], "by_reason": [{"reason": "动销"}]},
        }

    monkeypatch.setattr(web_agent_service, "campaign_feedback", _feedback)

    response = campaign_api.download_campaign_operation_feedback(
        6, db=_DB(plan), _=SimpleNamespace())

    assert called == {
        "title": "开学季-现货",
        "campaign_id": "123",
        "united_activity_id": "456",
    }
    assert response.headers["x-panse-failed-rows"] == "1"
