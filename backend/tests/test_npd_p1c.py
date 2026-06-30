"""NPD P1c: 成本门 G3 verdict + 工艺问题成本上浮 + G3 过门拦截。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.npd import NpdStage, NpdTask
from app.services import npd_service


def _setup(db):
    npd_service.seed_stages(db)
    npd_service.seed_task_templates(db)
    npd_service.seed_inspection_templates(db)


def _stage(db, code):
    return db.query(NpdStage).filter_by(code=code).one()


def _proj(db):
    return npd_service.create_project(
        db, name="测试单", target_price=Decimal("1000"), target_margin_rate=Decimal("0.30"))


def test_cost_gate_pass_fail(db_session):
    _setup(db_session)
    p = _proj(db_session)
    g = npd_service.save_cost_gate(db_session, p, est_mass_cost=Decimal("600"))
    assert g.verdict == "pass"          # 毛利 0.40 ≥ 0.30
    g = npd_service.save_cost_gate(db_session, p, est_mass_cost=Decimal("800"))
    assert g.verdict == "fail"          # 毛利 0.20 < 0.30


def test_cost_gate_prototype_plus_open_craft(db_session):
    _setup(db_session)
    p = _proj(db_session)
    npd_service.add_craft_issue(db_session, p.id, title="封边工艺改进", cost_impact=Decimal("250"))
    g = npd_service.save_cost_gate(db_session, p, prototype_cost=Decimal("600"))
    assert g.est_mass_cost == Decimal("850")   # 600 + 250 工艺上浮
    assert g.verdict == "fail"                 # 毛利 0.15 < 0.30


def test_g3_blocks_until_cost_gate_pass(db_session):
    _setup(db_session)
    p = _proj(db_session)
    g3 = _stage(db_session, "G3")
    s12 = _stage(db_session, "S12")
    npd_service.move_project(db_session, p, g3.id, force=True)
    inst = npd_service.get_active_instance(db_session, p.id)
    for t in db_session.query(NpdTask).filter_by(stage_instance_id=inst.id, is_required=True).all():
        npd_service.toggle_task(db_session, t, True)
    # 成本门未通过 → 不能放量
    with pytest.raises(ValueError):
        npd_service.move_project(db_session, p, s12.id)
    npd_service.save_cost_gate(db_session, p, est_mass_cost=Decimal("600"))  # pass
    npd_service.move_project(db_session, p, s12.id)
    assert p.current_stage_id == s12.id
