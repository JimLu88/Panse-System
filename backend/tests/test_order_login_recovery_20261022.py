"""Ten reproducible rounds across login, receipt, freshness and duplicate gates."""
import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from app.services import automation_pipeline_service as P
from app.services import agent_ingest_service as A
from app.services import order_sheet_archive_service as O
from app.services import scheduler as S
from app.services import settings_service as settings
from app.services import web_agent_service as W
from app.services.platform_scan_service import ACTIVE_KEY


@pytest.mark.parametrize("seed", range(2210, 2220))
def test_ten_rounds_login_to_delivery(db_session, monkeypatch, seed):
    rng = random.Random(seed)
    now = datetime.now().astimezone()
    sent, pulls, deliveries = [], [], []
    fresh = {"value": False}
    monkeypatch.setattr(P, "_send_feishu", lambda db, text: (sent.append(text) is None, "sent"))
    monkeypatch.setattr(S, "_now_hour", lambda: 20)
    monkeypatch.setattr(A, "recover_order_receipt", lambda db: {"reason": "order_export_error"})
    monkeypatch.setattr(A, "order_data_fresh", lambda db, **kw: fresh["value"])
    monkeypatch.setattr(A, "is_running", lambda: False)
    monkeypatch.setattr(A, "pending_shipping_password_files", lambda db, **kw: [])
    monkeypatch.setattr(A, "latest_order_pull_batch_id", lambda db: f"batch-{seed}")
    monkeypatch.setattr(A, "latest_order_pull_artifact_names", lambda db: [])
    monkeypatch.setattr(W, "ensure_online", lambda db, **kw: {"online": True})
    monkeypatch.setattr(S, "_sync_factory_dispatch_after_orders", lambda db, result: result)

    def pull(db, **kw):
        pulls.append(kw)
        fresh["value"] = True
        return {"tasks": [{"task": "taobao_orders", "status": "done"}], "pending_manual": []}

    count = rng.randint(1, 20)
    def deliver(db, **kw):
        deliveries.append(kw)
        return {"images_pushed": count, "images_failed": 0, "images_remaining": 0}

    monkeypatch.setattr(A, "orchestrate", pull)
    monkeypatch.setattr(O, "reconcile_pending_delivery", deliver)
    error = rng.choice(["session_expired:淘宝登录已失效", "需人工登录: taobao_orders", "登录态缺失 — 需扫码"])
    result = P.record_failure(db_session, "order_delivery", error,
                              retry_slots=[now + timedelta(hours=1)], now=now - timedelta(minutes=2))
    db_session.commit()
    assert result["routing"]["reason"] == "login_required"
    assert result["next_retry_at"] is None and result["final"]
    assert "等待扫码登录" in sent[0] and "订单资料" not in sent[0]
    # Repeated scheduled checks while awaiting login must not contact export.
    for _ in range(rng.randint(2, 5)):
        assert S._job_pull_catchup(db_session)["skipped"] == "waiting_for_shop_login"
    assert not pulls and not deliveries and len(sent) == 1

    def receipt(**changes):
        value = {"platform": "shop", "state": "success", "job": f"login-{seed}", "request_key": f"request-{seed}"}
        saved = changes.pop("saved", now - timedelta(minutes=1))
        value.update(changes)
        row = settings.set_value(db_session, ACTIVE_KEY, json.dumps(value))
        row.updated_at = saved.astimezone(timezone.utc)
        db_session.commit()

    invalid = [dict(platform="alipay"), dict(state="unknown"), dict(state="running"),
               dict(job=None), dict(saved=now - timedelta(days=1)), dict(saved=now + timedelta(hours=1))]
    rng.shuffle(invalid)
    for changes in invalid:
        receipt(**changes)
        assert not P.resume_order_after_login(db_session, now=now)
        assert not pulls and not deliveries
    receipt()
    # Even a valid login cannot override an unknown export outcome.
    monkeypatch.setattr(A, "recover_order_receipt", lambda db: {"reason": "order_export_unknown"})
    assert S._job_pull_catchup(db_session)["_run_status"] == "fail"
    assert not pulls and not deliveries
    monkeypatch.setattr(A, "recover_order_receipt", lambda db: {"reason": "order_export_error"})
    result = S._job_pull_catchup(db_session)
    assert result["images_pushed"] == count
    assert len(pulls) == len(deliveries) == 1
    assert pulls[0]["order_batch_id"] == f"batch-{seed}"
    assert pulls[0]["orders_only"] is True
    assert P.get_pipeline(db_session, "order_delivery")["success"]
    assert S._job_pull_catchup(db_session)["skipped"] == "order_pipeline_closed"
    assert not P.resume_order_after_login(db_session, now=now)
    assert len(pulls) == len(deliveries) == 1


def test_failed_export_never_reports_no_new_orders(db_session, monkeypatch):
    monkeypatch.setattr(P, "_send_feishu", lambda *a: (True, "sent"))
    result = S._record_pipeline_result(db_session, "order_delivery", {
        "_run_status": "fail", "_error": "session_expired", "_success_message": "没有新增订单",
    }, retry_times=((21, 17),))
    assert "_success_message" not in result
    assert not P.get_pipeline(db_session, "order_delivery")["success"]
