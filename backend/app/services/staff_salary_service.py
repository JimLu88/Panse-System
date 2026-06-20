"""人员/工资档案服务 (G)。

- 在职判定: active_from <= 月末 且 (active_to is None 或 active_to >= 月初)。
- monthly_total: 当月在职人员 monthly_cost 之和, 供 order_financials 外包成本预估用
  (替代写死 ¥10000/月)。
- CRUD: list_all / create / update / delete。

调用方: app/api/staff_salary.py, app/services/order_financials.py。
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import StaffSalary


def _to_dec(v) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    try:
        return Decimal(str(v).replace(",", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError("月工资格式不正确")


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    return first, last


def list_active(db: Session, year: int, month: int) -> list[StaffSalary]:
    """该月在职人员: active_from <= 月末 且 (active_to is None 或 active_to >= 月初)。"""
    first, last = _month_bounds(year, month)
    rows = db.execute(
        select(StaffSalary).where(StaffSalary.active_from <= last)
    ).scalars().all()
    return [
        s for s in rows
        if s.active_to is None or s.active_to >= first
    ]


def monthly_total(db: Session, year: int, month: int) -> Decimal:
    """Σ 当月在职人员 monthly_cost。无在职人员则返回 0。"""
    total = Decimal("0")
    for s in list_active(db, year, month):
        total += _to_dec(s.monthly_cost)
    return total


def list_all(db: Session) -> list[StaffSalary]:
    return db.execute(
        select(StaffSalary).order_by(
            StaffSalary.active_to.is_(None).desc(),  # 在职(至今)排前
            StaffSalary.active_from.desc(),
            StaffSalary.id,
        )
    ).scalars().all()


def _parse_date(v, *, required: bool, field: str) -> date | None:
    if v is None or v == "":
        if required:
            raise ValueError(f"{field}必填")
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ValueError(f"{field}格式应为 YYYY-MM-DD")


def create(
    db: Session,
    *,
    name: str,
    monthly_cost,
    active_from,
    role: str | None = None,
    active_to=None,
    remark: str | None = None,
) -> StaffSalary:
    if not (name or "").strip():
        raise ValueError("姓名必填")
    s = StaffSalary(
        name=name.strip(),
        monthly_cost=_to_dec(monthly_cost),
        role=(role or None),
        active_from=_parse_date(active_from, required=True, field="起始日期"),
        active_to=_parse_date(active_to, required=False, field="结束日期"),
        remark=(remark or None),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def update(
    db: Session,
    staff_id: int,
    *,
    name=None,
    monthly_cost=None,
    role=None,
    active_from=None,
    active_to=None,
    remark=None,
) -> StaffSalary | None:
    s = db.get(StaffSalary, staff_id)
    if s is None:
        return None
    if name is not None:
        if not name.strip():
            raise ValueError("姓名不能为空")
        s.name = name.strip()
    if monthly_cost is not None:
        s.monthly_cost = _to_dec(monthly_cost)
    if role is not None:
        s.role = role or None
    if active_from is not None:
        s.active_from = _parse_date(active_from, required=True, field="起始日期")
    if active_to is not None:
        # 显式传空字符串 = 清空(改回至今)
        s.active_to = _parse_date(active_to, required=False, field="结束日期")
    if remark is not None:
        s.remark = remark or None
    db.commit()
    db.refresh(s)
    return s


def delete(db: Session, staff_id: int) -> bool:
    s = db.get(StaffSalary, staff_id)
    if s is None:
        return False
    db.delete(s)
    db.commit()
    return True
