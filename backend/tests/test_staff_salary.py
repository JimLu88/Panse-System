"""人员/工资管理 + 外包成本口径挂钩 (G) 测试。

- list_active: 在职判定 (active_from<=月末 且 (active_to is None 或 active_to>=月初))。
- monthly_total: Σ 在职人员 monthly_cost。
- outsourcing_for_range: 每月预估改用工资合计; 无在职人员回落 coef; 实际<工资取工资(地板)。
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.finance import StaffSalary
from app.models.marketing import OutsourcingExpense
from app.services import staff_salary_service
from app.services.order_financials import outsourcing_for_range


def _add(db, name, cost, frm, to=None, role=None):
    s = StaffSalary(
        name=name, monthly_cost=Decimal(str(cost)),
        active_from=frm, active_to=to, role=role,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# -------- list_active --------

def test_list_active_includes_open_ended(db_session):
    # active_from=5月初, active_to=None(至今) → 5月/6月都在职
    _add(db_session, "A", 5000, date(2026, 5, 1))
    assert len(staff_salary_service.list_active(db_session, 2026, 5)) == 1
    assert len(staff_salary_service.list_active(db_session, 2026, 6)) == 1


def test_list_active_before_start_excluded(db_session):
    # 6/15 入职 → 5月不在职(active_from > 5月末), 6月在职
    _add(db_session, "B", 5000, date(2026, 6, 15))
    assert staff_salary_service.list_active(db_session, 2026, 5) == []
    assert len(staff_salary_service.list_active(db_session, 2026, 6)) == 1


def test_list_active_after_leave_excluded(db_session):
    # 5/1~5/20 在职 → 5月在职(active_to>=月初), 6月离职(active_to<6月初)
    _add(db_session, "C", 5000, date(2026, 5, 1), to=date(2026, 5, 20))
    assert len(staff_salary_service.list_active(db_session, 2026, 5)) == 1
    assert staff_salary_service.list_active(db_session, 2026, 6) == []


def test_list_active_boundary_month_end_start(db_session):
    # active_from=月末当天 在职; active_to=月初当天 在职 (边界含等号)
    _add(db_session, "D", 1000, date(2026, 5, 31))           # from=5月末
    _add(db_session, "E", 2000, date(2026, 4, 1), to=date(2026, 5, 1))  # to=5月初
    active = {s.name for s in staff_salary_service.list_active(db_session, 2026, 5)}
    assert active == {"D", "E"}


# -------- monthly_total --------

def test_monthly_total_sums_active(db_session):
    _add(db_session, "A", 5000, date(2026, 5, 1))
    _add(db_session, "B", 3000, date(2026, 5, 1))
    _add(db_session, "C", 9999, date(2026, 7, 1))  # 7月才入职, 5月不计
    assert staff_salary_service.monthly_total(db_session, 2026, 5) == Decimal("8000")


def test_monthly_total_zero_when_none(db_session):
    assert staff_salary_service.monthly_total(db_session, 2026, 5) == Decimal("0")


# -------- CRUD --------

def test_crud_roundtrip(db_session):
    s = staff_salary_service.create(
        db_session, name="张三", monthly_cost="6000", active_from="2026-05-01", role="设计",
    )
    assert s.id is not None
    assert staff_salary_service.monthly_total(db_session, 2026, 5) == Decimal("6000")

    updated = staff_salary_service.update(db_session, s.id, monthly_cost="7000", active_to="2026-05-31")
    assert updated.monthly_cost == Decimal("7000")
    # 5月底离职 → 6月合计 0
    assert staff_salary_service.monthly_total(db_session, 2026, 6) == Decimal("0")

    assert staff_salary_service.delete(db_session, s.id) is True
    assert staff_salary_service.list_all(db_session) == []
    assert staff_salary_service.delete(db_session, s.id) is False


def test_create_requires_name(db_session):
    with pytest.raises(ValueError):
        staff_salary_service.create(db_session, name="  ", monthly_cost="1", active_from="2026-05-01")


# -------- outsourcing_for_range 挂钩工资 --------

def _coef():
    return {"fin_outsourcing_monthly": "10000", "fin_outsourcing_est_since": "2026-05-01"}


def test_outsourcing_uses_salary_total(db_session):
    # 5月在职合计 8000 → 该月外包预估=8000 (非写死10000)
    _add(db_session, "A", 5000, date(2026, 5, 1))
    _add(db_session, "B", 3000, date(2026, 5, 1))
    total, est = outsourcing_for_range(db_session, date(2026, 5, 1), date(2026, 5, 31), _coef())
    assert total == Decimal("8000")
    assert est is True


def test_outsourcing_falls_back_when_no_staff(db_session):
    # 无在职人员 → 回落 coef 10000
    total, est = outsourcing_for_range(db_session, date(2026, 5, 1), date(2026, 5, 31), _coef())
    assert total == Decimal("10000")
    assert est is True


def test_outsourcing_actual_overrides_when_above_salary(db_session):
    # 实际录入 12000 > 工资预估 8000 → 用实际
    _add(db_session, "A", 8000, date(2026, 5, 1))
    db_session.add(OutsourcingExpense(payee="外包甲", payment_date=date(2026, 5, 10), amount=Decimal("12000")))
    db_session.commit()
    total, _ = outsourcing_for_range(db_session, date(2026, 5, 1), date(2026, 5, 31), _coef())
    assert total == Decimal("12000")


def test_outsourcing_salary_floor_when_actual_below(db_session):
    # 实际只录了一部分 5000 < 工资预估 8000 → 取工资预估(地板, 修漏算)
    _add(db_session, "A", 8000, date(2026, 5, 1))
    db_session.add(OutsourcingExpense(payee="外包甲", payment_date=date(2026, 5, 10), amount=Decimal("5000")))
    db_session.commit()
    total, est = outsourcing_for_range(db_session, date(2026, 5, 1), date(2026, 5, 31), _coef())
    assert total == Decimal("8000")
    assert est is True


def test_outsourcing_active_staff_counts_even_before_est_since(db_session):
    # 在职人员从1月起 → 即使查4月(est_since 5月之前)也按工资计入(active_from 本身即时间门)
    _add(db_session, "A", 8000, date(2026, 1, 1))
    total, est = outsourcing_for_range(db_session, date(2026, 4, 1), date(2026, 4, 30), _coef())
    assert total == Decimal("8000")
    assert est is True


def test_outsourcing_active_staff_floor_over_actual_before_est_since(db_session):
    # 4月工资10000 > 实际外包5000, est_since 之前也取工资(地板) — 复现并修复用户踩的坑
    _add(db_session, "A", 5000, date(2026, 1, 1))
    _add(db_session, "B", 5000, date(2026, 4, 1))
    db_session.add(OutsourcingExpense(payee="外包甲", payment_date=date(2026, 4, 10), amount=Decimal("5000")))
    db_session.commit()
    total, est = outsourcing_for_range(db_session, date(2026, 4, 1), date(2026, 4, 30), _coef())
    assert total == Decimal("10000")
    assert est is True


def test_outsourcing_no_staff_before_est_since_skipped(db_session):
    # 无在职人员 且 无实际 且 在 est_since(5月)之前 → 不凭空加成本
    total, _ = outsourcing_for_range(db_session, date(2026, 4, 1), date(2026, 4, 30), _coef())
    assert total == Decimal("0")
