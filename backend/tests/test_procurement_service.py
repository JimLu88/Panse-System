from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.models.procurement import ProcurementInquiry, ProcurementMessage
from app.services import procurement_service


def _task(db_session, **overrides):
    reviewed = overrides.pop("_reviewed", True)
    payload = {
        "title": "岩板批量询价",
        "category": "production",
        "item_name": "12mm 岩板",
        "specification": "1600×3200mm",
        "quantity": 10,
        "unit": "张",
        "target_unit_price": 480,
        "requirements": "需要说明切割损耗、木架和送货费用",
        "channels": ["taobao", "1688"],
        "planned_merchant_count": 10,
        "max_followup_rounds": 3,
        "ab_test_enabled": True,
        "ab_test_sample_size": 6,
    }
    payload.update(overrides)
    task = procurement_service.create_task(db_session, payload, created_by="tester")
    if reviewed:
        procurement_service.review_scripts(
            db_session,
            task,
            script_a="您好，请按含税含运口径提供报价、起订量、交期和阶梯价。",
            script_b="您好，我们在筛选长期供应商，请说明材质工艺、样品、交期和批量价格。",
            reviewed_by="tester",
        )
    db_session.flush()
    return task


def test_queue_is_blocked_until_generated_scripts_are_manually_edited(db_session):
    task = _task(db_session, _reviewed=False)
    generated = procurement_service.fallback_scripts(task)
    task.script_a = generated["script_a"]
    task.script_b = generated["script_b"]
    task.script_a_ai_draft = generated["script_a"]
    task.script_b_ai_draft = generated["script_b"]

    with pytest.raises(ValueError, match="人工修改并确认"):
        procurement_service.prepare_inquiries(db_session, task)
    with pytest.raises(ValueError, match="A 组仍是 AI 原稿"):
        procurement_service.review_scripts(
            db_session,
            task,
            script_a=generated["script_a"],
            script_b=generated["script_b"],
            reviewed_by="tester",
        )

    procurement_service.review_scripts(
        db_session,
        task,
        script_a=f"{generated['script_a']} 请优先文字回复。",
        script_b=f"{generated['script_b']} 请分别列项。",
        reviewed_by="tester",
    )
    assert procurement_service.prepare_inquiries(db_session, task)


def test_prepare_queue_splits_ab_sample_and_waits_for_winner(db_session):
    task = _task(db_session)
    rows = procurement_service.prepare_inquiries(db_session, task)

    assert len(rows) == 10
    assert [row.message_variant for row in rows[:6]] == ["A", "B", "A", "B", "A", "B"]
    assert {row.status for row in rows[:6]} == {"ready"}
    assert {row.message_variant for row in rows[6:]} == {"winner_pending"}
    assert {row.status for row in rows[6:]} == {"waiting_winner"}
    assert [row.channel for row in rows[:4]] == ["taobao", "1688", "taobao", "1688"]

    # 幂等：重复准备不会复制或清空已经存在的队列。
    second = procurement_service.prepare_inquiries(db_session, task)
    assert [row.id for row in second] == [row.id for row in rows]


def test_ab_metrics_and_apply_winner(db_session):
    task = _task(db_session)
    rows = procurement_service.prepare_inquiries(db_session, task)
    sent_at = datetime(2026, 7, 27, 1, 0, tzinfo=timezone.utc)
    for row in rows[:6]:
        procurement_service.mark_message_sent(
            db_session, task, row, sent_at=sent_at
        )

    # A 组 3 家回复且 2 家完整报价；B 组仅 1 家回复且报价不完整。
    for index in (0, 2, 4):
        procurement_service.record_reply(
            db_session,
            task,
            rows[index],
            content="可以做，含运报价已发",
            quote_complete=index != 4,
            normalized_unit_price=Decimal("470"),
        )
    procurement_service.record_reply(
        db_session,
        task,
        rows[1],
        content="有货，具体再看",
        quote_complete=False,
    )

    metrics = procurement_service.experiment_metrics(db_session, task)
    assert metrics["winner"] == "A"
    assert metrics["A"]["replied"] == 3
    assert metrics["A"]["quote_complete"] == 2
    assert metrics["B"]["replied"] == 1

    applied = procurement_service.apply_winner(db_session, task)
    assert applied["winner"] == "A"
    assert applied["activated"] == 4
    assert {row.message_variant for row in rows[6:]} == {"A"}
    assert {row.status for row in rows[6:]} == {"ready"}


def test_wechat_reply_is_surfaced_for_manual_handoff(db_session):
    task = _task(
        db_session,
        channels=["xiaohongshu"],
        planned_merchant_count=2,
        ab_test_sample_size=2,
    )
    rows = procurement_service.prepare_inquiries(db_session, task)
    procurement_service.mark_message_sent(db_session, task, rows[0])
    msg = procurement_service.record_reply(
        db_session,
        task,
        rows[0],
        content="可以购买，加微信 wx-rock-01 详聊",
        wechat_contact="wx-rock-01",
    )

    assert rows[0].status == "needs_manual"
    assert rows[0].requires_wechat is True
    assert rows[0].manual_reason == "商家要求加微信"
    assert task.status == "needs_review"
    assert msg.requires_manual_review is True
    assert msg.message_meta["manual_reason"] == "商家要求加微信"


def test_followup_round_is_bounded_and_audited(db_session):
    task = _task(
        db_session,
        planned_merchant_count=2,
        ab_test_sample_size=2,
        max_followup_rounds=1,
    )
    inquiry = procurement_service.prepare_inquiries(db_session, task)[0]
    procurement_service.mark_message_sent(db_session, task, inquiry)
    procurement_service.record_reply(
        db_session, task, inquiry, content="什么规格？"
    )
    procurement_service.review_inquiry_message(
        db_session,
        task,
        inquiry,
        content="我们需要 1600×3200×12mm，请按含税含运口径重新报价。",
        reviewed_by="tester",
    )
    followup = procurement_service.mark_message_sent(db_session, task, inquiry)

    assert inquiry.followup_round == 1
    assert followup.round_no == 1
    assert inquiry.next_followup_at is None
    messages = db_session.query(ProcurementMessage).filter_by(
        inquiry_id=inquiry.id
    ).all()
    assert [message.direction for message in messages] == [
        "outbound", "inbound", "outbound",
    ]


def test_due_actions_do_not_include_winner_pending_slots(db_session):
    task = _task(db_session)
    procurement_service.prepare_inquiries(db_session, task)
    actions = procurement_service.due_actions(db_session, task_id=task.id)

    assert len(actions) == 6
    assert all(action["action"] == "initial_message" for action in actions)
    assert all(action["requires_confirmed_send_callback"] for action in actions)
    assert all(action["review_required"] is False for action in actions)
    assert all(action["approved_message"] for action in actions)
    assert all(action["daily_limit"] > 0 for action in actions)


def test_due_actions_respect_channel_daily_limit(db_session):
    task = _task(
        db_session,
        channels=["taobao"],
        channel_daily_limits={"taobao": 3},
    )
    procurement_service.prepare_inquiries(db_session, task)
    actions = procurement_service.due_actions(db_session, task_id=task.id)

    assert len(actions) == 3
    assert {action["channel"] for action in actions} == {"taobao"}


def test_fallback_scripts_contain_price_and_delivery_questions(db_session):
    task = _task(db_session)
    scripts = procurement_service.fallback_scripts(task)

    assert "含运" in scripts["script_a"]
    assert "阶梯价" in scripts["script_a"]
    assert "交期" in scripts["script_b"]
    assert scripts["script_a"] != scripts["script_b"]


def test_agent_dry_run_previews_without_leasing(db_session):
    task = _task(
        db_session,
        execution_mode="agent",
        channels=["taobao"],
        planned_merchant_count=2,
        ab_test_sample_size=2,
    )
    rows = procurement_service.prepare_inquiries(
        db_session,
        task,
        [{"merchant_name": "商家一"}, {"merchant_name": "商家二"}],
    )
    actions = procurement_service.claim_agent_actions(
        db_session,
        agent_id="agent-1",
        mode="dry_run",
        capabilities=["taobao_desktop"],
        max_actions=1,
    )

    assert len(actions) == 1
    assert actions[0]["preview"] is True
    assert actions[0]["lease_token"] is None
    assert rows[0].leased_by is None
    assert rows[0].execution_attempts == 0


def test_agent_lease_send_and_callback_are_idempotent(db_session):
    task = _task(
        db_session,
        execution_mode="agent",
        channels=["taobao"],
        planned_merchant_count=1,
        ab_test_enabled=False,
        ab_test_sample_size=0,
    )
    inquiry = procurement_service.prepare_inquiries(
        db_session,
        task,
        [{"merchant_name": "商家一"}],
    )[0]
    action = procurement_service.claim_agent_actions(
        db_session,
        agent_id="agent-1",
        mode="review",
        capabilities=["taobao_desktop"],
        max_actions=1,
    )[0]

    sent, duplicate = procurement_service.confirm_agent_sent(
        db_session,
        inquiry=inquiry,
        agent_id="agent-1",
        lease_token=action["lease_token"],
        content=action["suggested_message"],
        external_message_id="tb-msg-001",
        external_thread_id="tb-thread-001",
    )
    same, duplicate_again = procurement_service.confirm_agent_sent(
        db_session,
        inquiry=inquiry,
        agent_id="agent-1",
        lease_token="already-cleared",
        content=action["suggested_message"],
        external_message_id="tb-msg-001",
        external_thread_id="tb-thread-001",
    )

    assert duplicate is False
    assert duplicate_again is True
    assert same.id == sent.id
    assert inquiry.status == "waiting_reply"
    assert inquiry.external_thread_id == "tb-thread-001"
    assert inquiry.lease_token is None
    assert db_session.query(ProcurementMessage).filter_by(
        inquiry_id=inquiry.id, direction="outbound"
    ).count() == 1


def test_agent_reply_is_idempotent_and_wechat_goes_manual(db_session):
    task = _task(
        db_session,
        execution_mode="agent",
        channels=["xiaohongshu"],
        planned_merchant_count=2,
        ab_test_sample_size=2,
    )
    inquiry = procurement_service.prepare_inquiries(
        db_session,
        task,
        [{"merchant_name": "小红书商家"}, {"merchant_name": "小红书商家二"}],
    )[0]
    action = procurement_service.claim_agent_actions(
        db_session,
        agent_id="agent-xhs",
        mode="live",
        capabilities=["xiaohongshu_chrome"],
    )[0]
    procurement_service.confirm_agent_sent(
        db_session,
        inquiry=inquiry,
        agent_id="agent-xhs",
        lease_token=action["lease_token"],
        content=action["suggested_message"],
        external_message_id="xhs-out-1",
    )

    reply, duplicate = procurement_service.record_agent_reply(
        db_session,
        inquiry=inquiry,
        agent_id="agent-xhs",
        content="可以购买，请加微信 xhs-rock",
        external_message_id="xhs-in-1",
        wechat_contact="xhs-rock",
    )
    same, duplicate_again = procurement_service.record_agent_reply(
        db_session,
        inquiry=inquiry,
        agent_id="agent-xhs",
        content="可以购买，请加微信 xhs-rock",
        external_message_id="xhs-in-1",
        wechat_contact="xhs-rock",
    )

    assert duplicate is False
    assert duplicate_again is True
    assert same.id == reply.id
    assert inquiry.status == "needs_manual"
    assert inquiry.requires_wechat is True
    assert db_session.query(ProcurementMessage).filter_by(
        inquiry_id=inquiry.id, direction="inbound"
    ).count() == 1


def test_agent_cannot_claim_followup_until_buyer_edits_it(db_session):
    task = _task(
        db_session,
        execution_mode="agent",
        channels=["taobao"],
        planned_merchant_count=1,
        ab_test_enabled=False,
        ab_test_sample_size=0,
    )
    inquiry = procurement_service.prepare_inquiries(
        db_session,
        task,
        [{"merchant_name": "商家一"}],
    )[0]
    initial = procurement_service.claim_agent_actions(
        db_session,
        agent_id="agent-1",
        mode="review",
        capabilities=["taobao_desktop"],
    )[0]
    procurement_service.confirm_agent_sent(
        db_session,
        inquiry=inquiry,
        agent_id="agent-1",
        lease_token=initial["lease_token"],
        content=initial["suggested_message"],
        external_message_id="tb-initial-review-gate",
    )
    procurement_service.record_reply(
        db_session,
        task,
        inquiry,
        content="可以做，价格稍后确认。",
    )

    due = procurement_service.due_actions(db_session, task_id=task.id)
    followup_due = next(
        item for item in due if item["inquiry_id"] == inquiry.id
    )
    assert followup_due["review_required"] is True
    assert procurement_service.claim_agent_actions(
        db_session,
        agent_id="agent-1",
        mode="review",
        capabilities=["taobao_desktop"],
    ) == []

    approved = "收到，麻烦今天回复含税含运单价、起订量和预计交期，谢谢。"
    procurement_service.review_inquiry_message(
        db_session,
        task,
        inquiry,
        content=approved,
        reviewed_by="tester",
    )
    action = procurement_service.claim_agent_actions(
        db_session,
        agent_id="agent-1",
        mode="review",
        capabilities=["taobao_desktop"],
    )[0]
    assert action["suggested_message"] == approved
    with pytest.raises(ValueError, match="最后确认的文案不一致"):
        procurement_service.confirm_agent_sent(
            db_session,
            inquiry=inquiry,
            agent_id="agent-1",
            lease_token=action["lease_token"],
            content="代理擅自改写的内容",
            external_message_id="tb-followup-mismatch",
        )


def test_agent_failure_stops_after_three_attempts(db_session):
    task = _task(
        db_session,
        execution_mode="agent",
        channels=["taobao"],
        planned_merchant_count=2,
        ab_test_sample_size=2,
    )
    inquiry = procurement_service.prepare_inquiries(
        db_session,
        task,
        [{"merchant_name": "商家一"}, {"merchant_name": "商家二"}],
    )[0]
    for attempt in range(1, 4):
        action = procurement_service.claim_agent_actions(
            db_session,
            agent_id="agent-1",
            mode="review",
            capabilities=["taobao_desktop"],
            max_actions=1,
        )[0]
        procurement_service.agent_failure(
            db_session,
            inquiry=inquiry,
            agent_id="agent-1",
            lease_token=action["lease_token"],
            error="窗口定位失败",
            retryable=True,
        )
        assert inquiry.execution_attempts == attempt

    assert inquiry.status == "needs_manual"
    assert "窗口定位失败" in (inquiry.manual_reason or "")
    assert task.status == "needs_review"


def test_agent_heartbeat_and_watch_list(db_session):
    task = _task(
        db_session,
        execution_mode="agent",
        channels=["taobao"],
        planned_merchant_count=2,
        ab_test_sample_size=2,
    )
    inquiry = procurement_service.prepare_inquiries(
        db_session,
        task,
        [{"merchant_name": "商家一"}, {"merchant_name": "商家二"}],
    )[0]
    action = procurement_service.claim_agent_actions(
        db_session,
        agent_id="agent-1",
        mode="review",
        capabilities=["taobao_desktop"],
    )[0]
    procurement_service.confirm_agent_sent(
        db_session,
        inquiry=inquiry,
        agent_id="agent-1",
        lease_token=action["lease_token"],
        content=action["suggested_message"],
        external_message_id="tb-out-watch",
        external_thread_id="tb-thread-watch",
    )
    procurement_service.heartbeat_agent(
        db_session,
        agent_id="agent-1",
        display_name="采购电脑",
        host_label="WIN-PROC",
        version="0.1.0",
        mode="review",
        status="online",
        capabilities=["taobao_desktop"],
    )

    watched = procurement_service.agent_watch_list(
        db_session, capabilities=["taobao_desktop"]
    )
    runtime = procurement_service.agent_runtime_status(db_session)

    assert watched[0]["external_thread_id"] == "tb-thread-watch"
    assert runtime["agents"][0]["online"] is True
    assert runtime["agents"][0]["display_name"] == "采购电脑"
