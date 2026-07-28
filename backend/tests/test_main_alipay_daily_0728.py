from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from app.api import web_agent
from app.models.finance import AlipayFlow
from app.services import agent_ingest_service as ingest
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

    monkeypatch.setattr(web_agent_service, "health", lambda db: {"online": True})
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
    monkeypatch.setattr(ingest, "run_ingest", lambda db: {
        "scanned": 0, "imported": 0, "pending": 0, "errors": 0, "files": [],
    })
    monkeypatch.setattr(ingest, "pending_shipping_password_files", lambda db, on=None: [])
    monkeypatch.setattr(order_sheet_archive_service, "generate_pending", lambda db: {"created": 0})

    result = ingest._orchestrate_locked(db_session, quiet=True)

    assert [item["task"] for item in result["tasks"]] == [ingest.MAIN_ALIPAY_FLOW_TASK]
    assert captured["variables"]["wait_scan"] is True
    assert captured["variables"]["account_label"] == "支付宝主力账号"
    assert captured["variables"]["date_to"] == datetime.now().date().isoformat()
    assert captured["variables"]["date_from"] == (
        datetime.now().date() - timedelta(days=35)
    ).isoformat()
    state = ingest._load_json(db_session, ingest.KEY_STATE)
    assert datetime.fromisoformat(state[ingest.STATE_MAIN_ALIPAY_FLOW]).date() == datetime.now().date()
    assert ingest._due_today(state, ingest.STATE_MAIN_ALIPAY_FLOW, False) is False


def test_main_alipay_notification_uses_friendly_account_name():
    assert web_agent._friendly_agent_text(
        "alipay_main 取数需要扫码登录"
    ) == "支付宝主力账号 取数需要扫码登录"
    assert web_agent._friendly_agent_text(
        "bal_alipay_main 扫码成功"
    ) == "支付宝主力账号余额 扫码成功"
