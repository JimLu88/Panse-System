"""人员/工资管理 CRUD (G) — /api/staff-salaries。

外包成本口径挂钩: 月度外包预估 = Σ 当月在职人员 monthly_cost (替代写死 ¥10000)。
GET monthly-total?year&month 返回某月在职合计 (前端顶部展示)。
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.finance import StaffSalary
from app.services import staff_salary_service

router = APIRouter(prefix="/api/staff-salaries", tags=["staff-salaries"])


def _serialize(s: StaffSalary) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "monthly_cost": float(s.monthly_cost or 0),
        "role": s.role,
        "active_from": s.active_from.isoformat() if s.active_from else None,
        "active_to": s.active_to.isoformat() if s.active_to else None,
        "remark": s.remark,
    }


@router.get("")
def list_staff(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    rows = staff_salary_service.list_all(db)
    today = date.today()
    total = staff_salary_service.monthly_total(db, today.year, today.month)
    return {
        "rows": [_serialize(s) for s in rows],
        "count": len(rows),
        "current_month_total": float(total),
        "current_year": today.year,
        "current_month": today.month,
    }


@router.post("")
def create_staff(
    name: str = Body(..., embed=True),
    monthly_cost=Body(0, embed=True),
    active_from=Body(..., embed=True),
    role: str | None = Body(None, embed=True),
    active_to=Body(None, embed=True),
    remark: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        s = staff_salary_service.create(
            db, name=name, monthly_cost=monthly_cost, active_from=active_from,
            role=role, active_to=active_to, remark=remark,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _serialize(s)


@router.put("/{staff_id}")
def update_staff(
    staff_id: int,
    name: str | None = Body(None, embed=True),
    monthly_cost=Body(None, embed=True),
    role: str | None = Body(None, embed=True),
    active_from=Body(None, embed=True),
    active_to=Body(None, embed=True),
    remark: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        s = staff_salary_service.update(
            db, staff_id, name=name, monthly_cost=monthly_cost, role=role,
            active_from=active_from, active_to=active_to, remark=remark,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if s is None:
        raise HTTPException(status_code=404, detail="人员不存在")
    return _serialize(s)


@router.delete("/{staff_id}")
def delete_staff(
    staff_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    if not staff_salary_service.delete(db, staff_id):
        raise HTTPException(status_code=404, detail="人员不存在")
    return {"deleted": staff_id}


@router.get("/monthly-total")
def monthly_total(
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    total = staff_salary_service.monthly_total(db, year, month)
    active = staff_salary_service.list_active(db, year, month)
    return {
        "year": year,
        "month": month,
        "total": float(total),
        "active_count": len(active),
        "active": [_serialize(s) for s in active],
    }
