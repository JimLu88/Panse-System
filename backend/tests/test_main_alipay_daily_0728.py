from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.api import web_agent
from app.models.finance import AlipayFlow
from app.services import (
    agent_ingest_service as ingest,
    alipay_import,
    feishu_client,
    settings_service,
)
from app.services import order_sheet_archive_service, web_agent_service


PERSONAL_ALIPAY_CSV = (
    "支付宝交易记录明细查询\n"
    "账号:[15824198812]\n"
    "交易号,商家订单号,交易创建时间,付款时间,最近修改时间,交易来源地,类型,交易对方,商品名称,"
    "金额（元）,收/支,交易状态,服务费（元）,成功退款（元）,备注,资金状态,\n"
    "202607280001,P100,2026-07-28 10:00:00,2026-07-28 10:00:00,,,支付,万师傅,安装费,"
    "79.99,支出,交易成功,0.00,0.00,,,\n"
)


def test_main_alipay_personal_csv_is_imported_to_main_account(db_session):
    kind, status, summary = ingest._import_one(
        db_session,
        "alipay",
        Path("/app/agent_output/alipay/主力/main.csv"),
        PERSONAL_ALIPAY_CSV.encode("utf-8-sig"),
    )

    row = db_session.query(AlipayFlow).one()
    assert (kind, status) == ("alipay", "imported")
    assert summary["account"] == "主力号"
    assert row.account == "主力号"
    assert row.amount == Decimal("-79.99")
    assert summary["reconciliation_ok"] is True
    assert summary["daily_reconciliation"] == [{
        "date": "2026-07-28",
        "source_count": 1,
        "source_income": "0",
        "source_expense": "79.99",
        "erp_count": 1,
        "erp_income": "0",
        "erp_expense": "79.99",
        "ok": True,
    }]
    saved = ingest._load_json(db_session, "alipay_main_daily_reconciliation")
    assert saved["ok"] is True
    assert saved["days"][0]["date"] == "2026-07-28"


def test_main_alipay_daily_reconciliation_detects_mismatch(db_session):
    db_session.add(AlipayFlow(
        account="主力号",
        transaction_no="20260728EXTRA",
        transaction_time=datetime(2026, 7, 28, 11, 0, 0),
        transaction_type="测试",
        amount=Decimal("-1"),
    ))
    db_session.commit()

    report = alipay_import.import_alipay_csv(
        db_session, PERSONAL_ALIPAY_CSV, account="主力号"
    )

    assert report.reconciliation_ok is False
    assert report.daily_reconciliation[0]["source_count"] == 1
    assert report.daily_reconciliation[0]["erp_count"] == 2
    assert report.daily_reconciliation[0]["ok"] is False


def test_main_alipay_runs_daily_and_allows_scan_without_session(db_session, monkeypatch):
    now = datetime.now().isoformat(timespec="seconds")
    ingest._save_json(db_session, ingest.KEY_STATE, {
        "taobao_report": now,
        "settlement": now,
        "balance": now,
        "promotion": now,
    })
    db_session.commit()
    captured = {}

    monkeypatch.setattr(
        web_agent_service, "ensure_online", lambda db, **kwargs: {"online": True}
    )
    monkeypatch.setattr(web_agent_service, "list_tasks", lambda db: {
        "tasks": [{"id": ingest.MAIN_ALIPAY_FLOW_TASK, "has_session": False}]
    })

    def fake_run_task(db, task_id, variables):
        captured.update({"task_id": task_id, "variables": variables})
        return {"ok": True, "job": "job-main"}

    monkeypatch.setattr(web_agent_service, "run_task", fake_run_task)
    monkeypatch.setattr(web_agent_service, "wait_job", lambda *args, **kwargs: {
        "status": "done", "result": {"ok": True},
    })
    monkeypatch.setattr(ingest, "refresh_alipay_balances", lambda db: [])
    monkeypatch.setattr(ingest, "refresh_alipay_daily", lambda db: {"ok": True})
    monkeypatch.setattr(ingest, "run_ingest", lambda db, **kwargs: {
        "scanned": 0, "imported": 0, "pending": 0, "errors": 0, "files": [],
    })
    artifact_snapshots = iter([
        {},
        {"/app/agent_output/2026-07-28/alipay/主力/main.csv": (1, 100)},
    ])
    monkeypatch.setattr(
        ingest, "_main_alipay_artifacts", lambda: next(artifact_snapshots))
    monkeypatch.setattr(ingest, "pending_shipping_password_files", lambda db, on=None, **kwargs: [])
    monkeypatch.setattr(order_sheet_archive_service, "generate_pending", lambda db: {"created": 0})

    result = ingest._orchestrate_locked(db_session, quiet=True)

    assert [item["task"] for item in result["tasks"]] == [ingest.MAIN_ALIPAY_FLOW_TASK]
    assert captured["variables"]["wait_scan"] is False
    assert captured["variables"]["account_label"] == "支付宝主力账号"
    assert captured["variables"]["date_to"] == datetime.now().date().isoformat()
    assert captured["variables"]["date_from"] == (
        datetime.now().date() - timedelta(days=30)
    ).isoformat()
    state = ingest._load_json(db_session, ingest.KEY_STATE)
    assert datetime.fromisoformat(state[ingest.STATE_MAIN_ALIPAY_FLOW]).date() == datetime.now().date()
    assert ingest._due_today(state, ingest.STATE_MAIN_ALIPAY_FLOW, False) is False


def test_main_alipay_date_range_resumes_from_latest_flow(db_session):
    latest = datetime.now() - timedelta(days=3)
    db_session.add(AlipayFlow(
        account="主力号",
        transaction_no="LATEST",
        transaction_time=latest,
        amount=Decimal("-1"),
    ))
    db_session.flush()

    variables = ingest._task_run_variables(
        ingest.MAIN_ALIPAY_FLOW_TASK, db_session, on=datetime.now().date()
    )

    assert variables["date_from"] == latest.date().isoformat()
    assert variables["date_to"] == datetime.now().date().isoformat()
    assert variables["wait_scan"] is False


def test_main_alipay_empty_success_is_not_marked_complete(db_session, monkeypatch):
    now = datetime.now().isoformat(timespec="seconds")
    ingest._save_json(db_session, ingest.KEY_STATE, {
        "taobao_report": now,
        "settlement": now,
        "balance": now,
        "promotion": now,
    })
    db_session.commit()
    monkeypatch.setattr(
        web_agent_service, "ensure_online", lambda db, **kwargs: {"online": True}
    )
    monkeypatch.setattr(web_agent_service, "list_tasks", lambda db: {
        "tasks": [{"id": ingest.MAIN_ALIPAY_FLOW_TASK, "has_session": True}]
    })
    monkeypatch.setattr(
        web_agent_service, "run_task",
        lambda db, task_id, variables: {"ok": True, "job": "job-empty"},
    )
    monkeypatch.setattr(web_agent_service, "wait_job", lambda *args, **kwargs: {
        "status": "done", "result": {"ok": True},
    })
    monkeypatch.setattr(ingest, "_main_alipay_artifacts", lambda: {})
    monkeypatch.setattr(ingest, "refresh_alipay_balances", lambda db: [])
    monkeypatch.setattr(ingest, "refresh_alipay_daily", lambda db: {"ok": True})
    monkeypatch.setattr(ingest, "run_ingest", lambda db, **kwargs: {
        "scanned": 0, "imported": 0, "pending": 0, "errors": 0, "files": [],
    })
    monkeypatch.setattr(ingest, "pending_shipping_password_files", lambda db, on=None, **kwargs: [])
    stop_requests = []
    monkeypatch.setattr(
        web_agent_service,
        "request_stop",
        lambda db, **kwargs: stop_requests.append(kwargs) or {"ok": True},
    )

    result = ingest._orchestrate_locked(db_session, quiet=True)

    assert result["tasks"] == [{
        "task": ingest.MAIN_ALIPAY_FLOW_TASK,
        "status": "error",
        "error": "任务完成但未生成支付宝主力账号流水文件",
    }]
    assert result["pending_manual"] == []
    assert result["task_errors"][0]["task"] == ingest.MAIN_ALIPAY_FLOW_TASK
    assert stop_requests == [{"reason": "orchestration_finished"}]
    state = ingest._load_json(db_session, ingest.KEY_STATE)
    assert ingest.STATE_MAIN_ALIPAY_FLOW not in state


def test_main_alipay_notification_uses_friendly_account_name():
    assert web_agent._friendly_agent_text(
        "alipay_main 取数需要扫码登录"
    ) == "支付宝主力账号 取数需要扫码登录"
    assert web_agent._friendly_agent_text(
        "bal_alipay_main 扫码成功"
    ) == "支付宝主力账号余额 扫码成功"


def test_feishu_only_sends_first_login_expiry_and_redacts_amount(
    db_session, monkeypatch
):
    settings_service.set_value(
        db_session, "feishu_push_chat_id", "chat-test", description="test"
    )
    db_session.commit()
    sent = []
    monkeypatch.setattr(
        feishu_client, "send_text",
        lambda db, chat_id, text: sent.append((chat_id, text)),
    )

    payload = web_agent.AgentNotify(
        kind="scan_needed",
        text="alipay_main 登录失效，余额 ￥12,345.67，请扫码",
    )
    first = web_agent.agent_notify(payload, db_session)
    second = web_agent.agent_notify(payload, db_session)

    assert first["feishu"] == "已发飞书"
    assert "未重复外发" in second["feishu"]
    assert len(sent) == 1
    assert "12,345.67" not in sent[0][1]
    assert "[金额已隐藏]" in sent[0][1]

    web_agent.agent_notify(
        web_agent.AgentNotify(kind="scan_timeout", text="alipay_main 扫码超时"),
        db_session,
    )
    web_agent.agent_notify(
        web_agent.AgentNotify(kind="scan_ok", text="alipay_main 已登录"),
        db_session,
    )
    web_agent.agent_notify(payload, db_session)
    assert len(sent) == 2
