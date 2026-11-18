from datetime import datetime, timedelta, timezone

from app.services import automation_pipeline_service as pipeline
from app.services import interaction_notice_service as notices
from app.services import notify_service, settings_service


def test_shipping_silent_across_days_until_verified_recovery(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "_send_feishu", lambda db, text: (sent.append(text) is None, "message-id"))
    start = datetime(2026, 11, 18, 18, tzinfo=timezone(timedelta(hours=8)))
    for day in range(15):
        pipeline.record_failure(db_session, "order_delivery", f"发货报表待口令 file-{day}",
                                retry_slots=[], now=start + timedelta(days=day))
        db_session.commit()
    assert len(sent) == 1
    pipeline.record_success(db_session, "order_delivery", now=start + timedelta(days=15))
    db_session.commit()
    pipeline.record_failure(db_session, "order_delivery", "发货报表待口令",
                            retry_slots=[], now=start + timedelta(days=16))
    assert len(sent) == 2


def test_alipay_wait_does_not_hide_other_sources(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(pipeline, "_send_feishu", lambda db, text: (sent.append(text) is None, "message-id"))
    start = datetime(2026, 11, 18, 20, tzinfo=timezone(timedelta(hours=8)))
    reason = ("alipay_main流水未完成: 需扫码 — 请在本飞书提醒群回复扫码；"
              "选择对应平台后在电脑弹出的窗口登录；其他平台请使用原专用入口；"
              "流水来源没有今日成功标记: alipay_main")
    for day in range(15):
        pipeline.record_failure(db_session, "flow_pull", reason, retry_slots=[], now=start + timedelta(days=day))
        db_session.commit()
    assert len(sent) == 1
    pipeline.record_failure(db_session, "flow_pull", reason + "；wechat_bill流水未完成: wechat_bill_table_missing",
                            retry_slots=[], now=start + timedelta(days=15))
    assert len(sent) == 2
    assert "微信账单" in sent[-1] and "支付宝" not in sent[-1]
    notices.resolve(db_session, notices.ALIPAY_LOGIN)
    db_session.commit()
    pipeline.record_failure(db_session, "flow_pull", reason, retry_slots=[], now=start + timedelta(days=16))
    assert len(sent) == 3


def test_direct_password_notice_shares_gate_and_never_retries_unknown(db_session, monkeypatch):
    from app.services import feishu_client
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    settings_service.set_value(db_session, "notify_route_mode", "feishu_split")
    settings_service.set_value(db_session, "feishu_alert_chat_id", "test-chat")
    db_session.commit()
    sent = []
    monkeypatch.setattr(feishu_client, "send_text", lambda *a: sent.append(a) or {})
    first = notify_service.notify(db_session, "待口令 a", title="畔色 ERP | 发货报表待口令")
    db_session.commit()
    second = notify_service.notify(db_session, "待口令 b", title="畔色 ERP | 发货报表待口令")
    assert first == (False, "delivery_unknown")
    assert second == (False, "waiting_already_reported")
    assert pipeline._new_interaction_reasons(db_session, "order_delivery", "加密发货报表待口令") == ""
    assert len(sent) == 1


def test_balance_and_flow_share_alipay_gate(db_session):
    assert pipeline._new_interaction_reasons(db_session, "flow_pull", "alipay_main流水未完成: 需扫码")
    db_session.commit()
    assert not pipeline._new_interaction_reasons(db_session, "balance_pull",
        "余额任务未完成: bal_alipay_main(需扫码)；余额日期未更新: 主力号")
    other = pipeline._new_interaction_reasons(db_session, "balance_pull",
        "余额任务未完成: bal_alipay_main(需扫码),bal_wanshifu(需扫码)；余额日期未更新: 主力号,万师傅")
    assert "alipay" not in other and "主力号" not in other and "万师傅" in other


def test_unknown_failure_is_not_silenced_by_alipay_wait(db_session):
    notices.claim(db_session, notices.ALIPAY_LOGIN)
    assert pipeline._new_interaction_reasons(db_session, "flow_pull", "alipay_main流水未完成: 下载文件损坏")


def test_no_daily_gap_alert_while_alipay_waits_but_real_mismatch_still_alerts(db_session, monkeypatch):
    from app.services import alipay_collection_audit as audit, agent_ingest_service as ingest
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)
    monkeypatch.setattr(ingest, "get_pending_scans", lambda db: ["alipay_main"])
    monkeypatch.setattr(notify_service, "get_alert_chat_id", lambda db: "test-chat")
    sent = []
    send = lambda *args: sent.append(args) or {"message_id": "m1"}
    result = {"ok": False, "issues": [
        {"key": "coverage:2026-11-18", "message": "未下载账单"},
        {"key": "collection_or_ingest_failed", "message": "采集未完成"},
    ]}
    first = audit.record_and_notify(db_session, result, send=send)
    assert not sent and first["waiting_login_deferred"] == 2 and not first["ok"]
    result["issues"].append({"key": "missing_rows:abc", "message": "已下载账单与入库记录不一致"})
    audit.record_and_notify(db_session, result, send=send)
    assert len(sent) == 1 and "未下载账单" not in sent[0][-1]
    monkeypatch.setattr(ingest, "get_pending_scans", lambda db: [])
    audit.record_and_notify(db_session, result, send=send)
    assert len(sent) == 2
