"""会计期间 (Phase 8, Tier 1 #3, 借鉴 SAP/NetSuite).

业务: 月底关账后, 该月任何 Order / FactoryOrder / 流水都不能改 (除 admin 重新打开).

调用方在写之前要检查: ensure_writable(db, target_date).

公开 API:
    open_period(db, year, month, actor)
    close_period(db, year, month, actor)
    lock_period(db, year, month, actor)       — 年审后锁死, 防 admin 误重开
    reopen_period(db, year, month, actor)      — 把 closed 重开为 open (lock 状态不行)
    is_writable(db, target_date) -> bool
    ensure_writable(db, target_date)           — 不通过抛 PermissionError
    list_periods(db, limit)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.accounting_period import AccountingPeriod


class PeriodLocked(PermissionError):
    """业务: 试图修改已关闭的会计期间."""


def _get(db: Session, year: int, month: int) -> Optional[AccountingPeriod]:
    return db.execute(
        select(AccountingPeriod).where(
            AccountingPeriod.year == year,
            AccountingPeriod.month == month,
        )
    ).scalar_one_or_none()


def open_period(db: Session, year: int, month: int, *,
                actor: str = "admin") -> AccountingPeriod:
    p = _get(db, year, month)
    if p is None:
        p = AccountingPeriod(year=year, month=month, status="open")
        db.add(p)
        db.flush()
    else:
        if p.status == "locked":
            raise PeriodLocked(f"{year}-{month:02d} 已锁死, 不可重开")
        p.status = "open"
        p.closed_at = None
        p.closed_by = None
    return p


def close_period(db: Session, year: int, month: int, *,
                 actor: str = "admin") -> AccountingPeriod:
    p = _get(db, year, month)
    if p is None:
        p = AccountingPeriod(year=year, month=month, status="closed",
                              closed_at=datetime.now(timezone.utc), closed_by=actor)
        db.add(p)
        db.flush()
        return p
    if p.status == "locked":
        raise PeriodLocked(f"{year}-{month:02d} 已锁死")
    p.status = "closed"
    p.closed_at = datetime.now(timezone.utc)
    p.closed_by = actor
    return p


def lock_period(db: Session, year: int, month: int, *,
                actor: str = "admin") -> AccountingPeriod:
    """业务: 年审/审计后锁死, 任何 admin 都不能再改."""
    p = _get(db, year, month)
    if p is None:
        p = AccountingPeriod(year=year, month=month, status="locked",
                              closed_at=datetime.now(timezone.utc), closed_by=actor,
                              remark="锁死")
        db.add(p)
    else:
        p.status = "locked"
        p.closed_at = p.closed_at or datetime.now(timezone.utc)
        p.closed_by = p.closed_by or actor
    db.flush()
    return p


def reopen_period(db: Session, year: int, month: int, *,
                  actor: str = "admin") -> AccountingPeriod:
    p = _get(db, year, month)
    if p is None:
        return open_period(db, year, month, actor=actor)
    if p.status == "locked":
        raise PeriodLocked(f"{year}-{month:02d} 已锁死, 不可重开")
    p.status = "open"
    p.closed_at = None
    p.closed_by = None
    return p


def is_writable(db: Session, target_date: date) -> bool:
    if target_date is None:
        return True
    p = _get(db, target_date.year, target_date.month)
    if p is None:
        return True   # 默认未关账时可写
    return p.status == "open"


def ensure_writable(db: Session, target_date: Optional[date]) -> None:
    if target_date is None:
        return
    if not is_writable(db, target_date):
        raise PeriodLocked(
            f"会计期间 {target_date.year}-{target_date.month:02d} 已关闭, "
            f"不能修改这个月份的数据"
        )


def list_periods(db: Session, limit: int = 36) -> list[AccountingPeriod]:
    return list(db.execute(
        select(AccountingPeriod).order_by(
            AccountingPeriod.year.desc(), AccountingPeriod.month.desc(),
        ).limit(limit)
    ).scalars())
