"""数据水位线 (Phase 7).

业务: 用户说"以前的数据脏乱核对不上, 不要让历史数据进入对账/财务公式".
设置一个 system_setting `data_baseline_date = YYYY-MM-DD`,
所有统计 / 对账 / 资产公式 / 财务核对 都跳过 order_date < baseline 的订单 + 自动标 is_historical=True.

API:
    get_baseline_date(db) -> date | None
    set_baseline_date(db, date_str, actor) -> mark 之前所有订单为 is_historical
    is_historical_order(o)                 -> bool (统一过滤口)

调用: sales_analytics / asset_service / 财务核对 都用这里的 baseline filter.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.order import Order
from app.services import settings_service

BASELINE_KEY = "data_baseline_date"


def get_baseline_date(db: Session) -> Optional[date]:
    raw = settings_service.get(db, BASELINE_KEY)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def set_baseline_date(db: Session, baseline: date, *, actor: str = "admin") -> dict:
    """设置 baseline + 把 order_date < baseline 的订单全部标 is_historical.

    返回 {marked: N, baseline}.
    """
    settings_service.set_value(db, BASELINE_KEY, baseline.isoformat())
    # 批量标记
    result = db.execute(
        update(Order).where(
            Order.order_date < baseline,
            Order.is_historical == False,  # noqa: E712
        ).values(is_historical=True)
    )
    return {"marked": result.rowcount, "baseline": baseline.isoformat()}


def clear_baseline(db: Session) -> None:
    """admin 操作: 清除 baseline (恢复全量统计). 不会回滚 is_historical 标记."""
    settings_service.set_value(db, BASELINE_KEY, "")
