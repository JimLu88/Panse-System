"""NPD P1a: 阶段待办模板 instantiate + 必做项过门。"""
from __future__ import annotations

import pytest

from app.models.npd import NpdStage, NpdTask
from app.services import npd_service


def _setup(db):
    npd_service.seed_stages(db)
    npd_service.seed_task_templates(db)


def test_task_templates_seed_idempotent(db_session):
    _setup(db_session)
    assert npd_service.seed_task_templates(db_session) == 0  # 再 seed 不重复


def test_create_instantiates_start_stage_tasks(db_session):
    _setup(db_session)
    p = npd_service.create_project(db_session, name="测试单")
    tasks = db_session.query(NpdTask).filter_by(project_id=p.id).all()
    assert len(tasks) >= 2          # S01 立项有 3 个模板
    assert any(t.is_required for t in tasks)
    assert all(t.due_date is not None for t in tasks)


def test_gate_blocks_forward_until_required_done(db_session):
    _setup(db_session)
    p = npd_service.create_project(db_session, name="测试单")
    s04 = db_session.query(NpdStage).filter_by(code="S04").one()
    # S01 必做项未完成 → 前进被拦
    with pytest.raises(ValueError):
        npd_service.move_project(db_session, p, s04.id)
    # 完成当前阶段所有必做项
    cur = npd_service.get_active_instance(db_session, p.id)
    for t in db_session.query(NpdTask).filter_by(stage_instance_id=cur.id, is_required=True).all():
        npd_service.toggle_task(db_session, t, True)
    # 现在可前进, 且目标阶段任务被 instantiate
    npd_service.move_project(db_session, p, s04.id)
    assert p.current_stage_id == s04.id
    s04_inst = npd_service.get_active_instance(db_session, p.id)
    assert db_session.query(NpdTask).filter_by(stage_instance_id=s04_inst.id).count() >= 1


def test_force_bypasses_gate(db_session):
    _setup(db_session)
    p = npd_service.create_project(db_session, name="测试单")
    s04 = db_session.query(NpdStage).filter_by(code="S04").one()
    npd_service.move_project(db_session, p, s04.id, force=True)  # 不抛
    assert p.current_stage_id == s04.id


def test_backward_move_not_gated(db_session):
    _setup(db_session)
    p = npd_service.create_project(db_session, name="测试单")
    s04 = db_session.query(NpdStage).filter_by(code="S04").one()
    s03 = db_session.query(NpdStage).filter_by(code="S03").one()
    npd_service.move_project(db_session, p, s04.id, force=True)
    # S04 必做项未完成, 但回退到 S03(序号更小)不卡
    npd_service.move_project(db_session, p, s03.id)
    assert p.current_stage_id == s03.id


def test_timeline_has_current_and_tasks(db_session):
    _setup(db_session)
    p = npd_service.create_project(db_session, name="测试单")
    tl = npd_service.project_timeline(db_session, p)
    cur = [row for row in tl if row["is_current"]]
    assert len(cur) == 1
    assert cur[0]["stage"].code == "S01"
    assert len(cur[0]["tasks"]) >= 2
