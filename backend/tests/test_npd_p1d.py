"""NPD P1d: 阶段截止提醒任务 注册 + 临期标记。"""
from __future__ import annotations

from app.services import npd_service, scheduler


def test_npd_remind_registered():
    scheduler._register_default_jobs()
    ids = {j["job_id"] for j in scheduler.list_jobs()}
    assert "daily_0915_npd_stage_remind" in ids


def test_npd_remind_flags_due_stage(db_session):
    npd_service.seed_stages(db_session)
    npd_service.seed_task_templates(db_session)
    npd_service.create_project(db_session, name="临期单")  # S01 截止=now+2天 → 临期
    res = scheduler._job_npd_stage_remind(db_session)
    assert res["due"] >= 1
    assert res["pushed"] is False  # conftest PANSE_DISABLE_NOTIFY=1
