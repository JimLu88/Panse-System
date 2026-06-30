"""NPD P1b: 验收模板 instantiate + 数值自动判 + 必检全过才过门。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.npd import NpdInspectionItem, NpdStage, NpdTask
from app.services import npd_service


def _setup(db):
    npd_service.seed_stages(db)
    npd_service.seed_task_templates(db)
    npd_service.seed_inspection_templates(db)


def _stage(db, code):
    return db.query(NpdStage).filter_by(code=code).one()


def test_inspection_seed_idempotent(db_session):
    _setup(db_session)
    assert npd_service.seed_inspection_templates(db_session) == 0


def test_inspection_instantiated_on_enter(db_session):
    _setup(db_session)
    p = npd_service.create_project(db_session, name="测试单")
    npd_service.move_project(db_session, p, _stage(db_session, "S13").id, force=True)
    inst = npd_service.get_active_instance(db_session, p.id)
    insps = db_session.query(NpdInspectionItem).filter_by(stage_instance_id=inst.id).all()
    assert len(insps) >= 5
    assert any(i.is_required for i in insps)


def test_inspection_gate_blocks_until_pass(db_session):
    _setup(db_session)
    p = npd_service.create_project(db_session, name="测试单")
    npd_service.move_project(db_session, p, _stage(db_session, "S13").id, force=True)
    inst = npd_service.get_active_instance(db_session, p.id)
    # 先把 S13 必做任务完成, 隔离出验收门
    for t in db_session.query(NpdTask).filter_by(stage_instance_id=inst.id, is_required=True).all():
        npd_service.toggle_task(db_session, t, True)
    s15 = _stage(db_session, "S15")
    with pytest.raises(ValueError):
        npd_service.move_project(db_session, p, s15.id)   # 验收未过 → 拦
    # 通过所有必检验收项
    for it in db_session.query(NpdInspectionItem).filter_by(
            stage_instance_id=inst.id, is_required=True).all():
        npd_service.save_inspection_item(db_session, it, result="pass")
    npd_service.move_project(db_session, p, s15.id)        # 现在放行
    assert p.current_stage_id == s15.id


def test_numeric_auto_judge(db_session):
    _setup(db_session)
    p = npd_service.create_project(db_session, name="测试单")
    npd_service.move_project(db_session, p, _stage(db_session, "S13").id, force=True)
    inst = npd_service.get_active_instance(db_session, p.id)
    length_item = db_session.query(NpdInspectionItem).filter_by(
        stage_instance_id=inst.id, item_name="长").one()
    # 设公差 1397~1403, 实测 1400 → pass
    npd_service.save_inspection_item(
        db_session, length_item, reading="1400",
        min_val=Decimal("1397"), max_val=Decimal("1403"))
    assert length_item.result == "pass"
    # 实测 1410 超上限 → fail (min/max 已持久化)
    npd_service.save_inspection_item(db_session, length_item, reading="1410")
    assert length_item.result == "fail"
