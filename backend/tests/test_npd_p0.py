"""NPD 板块 P0: 阶段 seed(量产组默认隐藏)+ 立项 + 阶段流转。"""
from __future__ import annotations

from app.models.npd import NpdProject, NpdStage, NpdStageInstance
from app.services import npd_service, settings_service


def test_seed_idempotent_and_mass_production_hidden(db_session):
    n1 = npd_service.seed_stages(db_session)
    assert n1 == 29  # 24 阶段 + 5 门
    n2 = npd_service.seed_stages(db_session)
    assert n2 == 0  # 幂等

    # 默认(生产线关)→ 不含量产组(S16 PVT / S19 量产)
    default_codes = {s.code for s in npd_service.list_stages(db_session)}
    assert "S16" not in default_codes and "S19" not in default_codes
    assert "S01" in default_codes and "G3" in default_codes
    assert len(default_codes) == 27

    # 打开生产线开关 → 含量产组
    settings_service.set_value(db_session, npd_service.KEY_MASS_PRODUCTION, "1")
    on_codes = {s.code for s in npd_service.list_stages(db_session)}
    assert "S16" in on_codes and "S19" in on_codes
    assert len(on_codes) == 29


def test_create_project_lands_on_default_stage(db_session):
    npd_service.seed_stages(db_session)
    proj = npd_service.create_project(db_session, name="岩板餐桌-樱桃木", category="餐桌",
                                      target_margin_rate=None)
    assert proj.code == "NPD0001"
    default = db_session.query(NpdStage).filter_by(is_default=True).one()
    assert proj.current_stage_id == default.id
    # 起始阶段开了一个 active 实例
    inst = db_session.query(NpdStageInstance).filter_by(project_id=proj.id, status="active").one()
    assert inst.stage_id == default.id and inst.deadline is not None


def test_move_project_advances_and_tracks(db_session):
    npd_service.seed_stages(db_session)
    proj = npd_service.create_project(db_session, name="测试单")
    target = db_session.query(NpdStage).filter_by(code="S04").one()
    npd_service.move_project(db_session, proj, target.id, actor="tester")
    assert proj.current_stage_id == target.id
    assert proj.percent_done > 0
    # 旧实例关闭、新实例 active
    actives = db_session.query(NpdStageInstance).filter_by(project_id=proj.id, status="active").all()
    assert len(actives) == 1 and actives[0].stage_id == target.id
    done = db_session.query(NpdStageInstance).filter_by(project_id=proj.id, status="done").all()
    assert len(done) == 1


def test_move_to_final_marks_done(db_session):
    npd_service.seed_stages(db_session)
    proj = npd_service.create_project(db_session, name="收尾单")
    final = db_session.query(NpdStage).filter_by(is_final=True).one()
    npd_service.move_project(db_session, proj, final.id)
    assert proj.state == "done"
    assert proj.percent_done == 100
