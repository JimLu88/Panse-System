from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from app.models.finance import AccountBalance
from app.services import (
    agent_ingest_service,
    alert_service,
    automation_pipeline_service as pipeline,
    scheduler,
    settings_service,
)


TZ = timezone(timedelta(hours=8))


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 29, hour, minute, tzinfo=TZ)


def test_each_failure_notifies_next_retry_and_final_failure(db_session, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "_send_feishu",
        lambda db, text: (sent.append(text) is None or True, "sent"),
    )
    slots = [_at(10), _at(11), _at(12)]

    first = pipeline.record_failure(
        db_session,
        "balance_pull",
        "接口异常，余额 ￥12,345.67",
        retry_slots=slots,
        now=_at(9),
        max_failures=4,
    )
    second = pipeline.record_failure(
        db_session,
        "balance_pull",
        "仍未完成",
        retry_slots=slots,
        now=_at(10),
        max_failures=4,
    )
    third = pipeline.record_failure(
        db_session,
        "balance_pull",
        "仍未完成",
        retry_slots=slots,
        now=_at(11),
        max_failures=4,
    )
    final = pipeline.record_failure(
        db_session,
        "balance_pull",
        "仍未完成",
        retry_slots=slots,
        now=_at(12),
        max_failures=4,
    )

    assert first["next_retry_at"].startswith("2026-07-29T10:00")
    assert second["next_retry_at"].startswith("2026-07-29T11:00")
    assert third["next_retry_at"].startswith("2026-07-29T12:00")
    assert final["final"] is True
    assert len(sent) == 4
    assert "第1次执行失败" in sent[0]
    assert "10:00" in sent[0]
    assert "12,345" not in sent[0]
    assert "今日失败" in sent[-1]


def test_success_after_failure_sends_one_recovery_and_stops_retry(db_session, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr(
        pipeline,
        "_send_feishu",
        lambda db, text: (sent.append(text) is None or True, "sent"),
    )
    pipeline.record_failure(
        db_session,
        "order_delivery",
        "PC离线",
        retry_slots=[_at(20)],
        now=_at(19),
        max_failures=2,
    )
    recovered = pipeline.record_success(
        db_session, "order_delivery", now=_at(19, 30)
    )
    repeated = pipeline.record_success(
        db_session, "order_delivery", now=_at(19, 40)
    )

    assert recovered["recovered"] is True
    assert repeated["already_success"] is True
    assert pipeline.needs_retry(db_session, "order_delivery", now=_at(19, 50)) is False
    assert len(sent) == 2
    assert "自动重试成功" in sent[-1]


def test_failed_feishu_send_is_persisted_and_retried(db_session, monkeypatch):
    calls = {"count": 0}

    def _send(db, text):
        calls["count"] += 1
        return (calls["count"] > 1, "network")

    monkeypatch.setattr(pipeline, "_send_feishu", _send)
    result = pipeline.record_failure(
        db_session,
        "flow_pull",
        "网络异常",
        retry_slots=[_at(22)],
        now=_at(21),
        max_failures=2,
    )
    assert result["notification"]["queued"] is True

    retried = pipeline.retry_pending_notifications(
        db_session, now=_at(21, 31)
    )
    assert retried == {"retried": 1, "sent": 1, "exhausted": 0}
    raw = settings_service.get(db_session, pipeline.SETTING_KEY, env_fallback=False)
    queue = json.loads(raw)["notification_queue"]
    assert queue[0]["sent_at"].startswith("2026-07-29T21:31")


def test_recovery_waits_behind_queued_failure_notification(db_session, monkeypatch):
    delivered: list[str] = []
    available = {"value": False}

    def _send(db, text):
        if not available["value"]:
            return False, "network"
        delivered.append(text)
        return True, "sent"

    monkeypatch.setattr(pipeline, "_send_feishu", _send)
    pipeline.record_failure(
        db_session,
        "flow_pull",
        "网络异常",
        retry_slots=[_at(22)],
        now=_at(21),
        max_failures=2,
    )
    available["value"] = True
    recovered = pipeline.record_success(
        db_session, "flow_pull", now=_at(21, 5)
    )
    assert recovered["notification"]["detail"] == "ordered_after_pending"
    assert delivered == []

    retried = pipeline.retry_pending_notifications(
        db_session, now=_at(21, 31)
    )
    assert retried == {"retried": 2, "sent": 2, "exhausted": 0}
    assert "执行失败" in delivered[0]
    assert "自动重试成功" in delivered[1]


def test_finance_outcomes_require_all_accounts_and_both_flow_sources(db_session):
    for account in (
        "支付宝-企业账号",
        "淘宝聚合账户",
        "淘宝推广账户",
        "万师傅",
        "主力号",
    ):
        db_session.add(
            AccountBalance(
                account_name=account,
                period_year=2026,
                period_month=7,
                as_of_date=date.today(),
                opening_balance=Decimal("0"),
                closing_balance=Decimal("0"),
            )
        )
    settings_service.set_value(
        db_session,
        agent_ingest_service.KEY_STATE,
        json.dumps({
            agent_ingest_service.STATE_MAIN_ALIPAY_FLOW:
                datetime.now().isoformat(timespec="seconds")
        }),
    )
    db_session.flush()
    result = {
        "tasks": [
            {"task": task, "status": "done"}
            for task in (
                "bal_taobao_aggregate",
                "bal_ads",
                "bal_wanshifu",
                "bal_alipay_main",
                agent_ingest_service.MAIN_ALIPAY_FLOW_TASK,
            )
        ],
        "pending_manual": [],
        "alipay_balance": [{"account": "支付宝-企业账号", "balance": "hidden"}],
        "alipay_daily": {"pulled": 1, "fail": 0},
        "ingest": {"files": []},
    }

    outcomes = scheduler._finance_outcomes(db_session, result)
    assert outcomes["balance_pull"][0] is True
    assert outcomes["flow_pull"][0] is True

    result["tasks"] = [
        item for item in result["tasks"] if item["task"] != "bal_ads"
    ]
    outcomes = scheduler._finance_outcomes(db_session, result)
    assert outcomes["balance_pull"][0] is False
    assert "bal_ads" in outcomes["balance_pull"][1]


def test_finance_scheduler_forces_daily_finance_only(db_session, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(agent_ingest_service, "is_running", lambda: False)

    def _orchestrate(db, **kwargs):
        captured.update(kwargs)
        return {}

    monkeypatch.setattr(agent_ingest_service, "orchestrate", _orchestrate)
    monkeypatch.setattr(
        scheduler,
        "_finance_outcomes",
        lambda db, result: {
            "balance_pull": (True, "ok"),
            "flow_pull": (True, "ok"),
        },
    )
    monkeypatch.setattr(
        scheduler,
        "_record_pipeline_result",
        lambda db, name, result, **kwargs: {
            **result,
            "automation_pipeline": {"pipeline": name},
        },
    )
    monkeypatch.setattr(alert_service, "resolve_by_dedupe", lambda *args, **kwargs: None)

    result = scheduler._job_web_agent_finance(db_session)
    assert result["_finance_status"] == "ok"
    assert captured["force_finance"] is True
    assert captured["force_orders"] is False
    assert captured["orders_only"] is False
