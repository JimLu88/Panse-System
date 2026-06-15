"""客户库 + RFM 分层 + 复购触达。第一方数据闭环（家具复购靠老客转介绍）。

从成交线索(Lead won)聚合客户；接 ERP 后可补订单金额/复购。RFM 分层给触达建议。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Customer, Lead


def sync_from_leads(db: Session) -> dict:
    """把成交线索同步进客户库（按联系方式去重，累计单数）。"""
    won = db.scalars(select(Lead).where(Lead.status == "won")).all()
    added = 0
    for lead in won:
        key = lead.contact or f"lead#{lead.id}"
        cust = db.scalar(select(Customer).where(Customer.contact == key))
        if cust is None:
            cust = Customer(contact=key, source=lead.source_type or "xhs",
                            first_order_at=lead.created_at, last_order_at=lead.created_at,
                            order_count=1, total_amount=0.0)
            db.add(cust)
            db.flush()  # 立即可查，避免同名线索重复建（autoflush=False）
            added += 1
        else:
            cust.order_count += 1
            cust.last_order_at = lead.created_at
    db.commit()
    _recompute_rfm(db)
    return {"added": added, "total": db.scalar(select(func.count()).select_from(Customer))}


def _recompute_rfm(db: Session) -> None:
    """简化 RFM：按最近购买时间(R)+购买次数(F)分层。"""
    now = dt.datetime.now(dt.timezone.utc)
    for c in db.scalars(select(Customer)):
        last = c.last_order_at or c.created_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        days = (now - last).days
        if c.order_count >= 2 and days <= 180:
            c.rfm_tier = "vip" if c.order_count >= 3 else "repeat"
        elif days > 365:
            c.rfm_tier = "sleeping"
        elif c.order_count >= 2:
            c.rfm_tier = "repeat"
        else:
            c.rfm_tier = "normal"
    db.commit()


_REACH = {
    "vip": "VIP老客：邀约新品内测+专属优惠，重点维护转介绍",
    "repeat": "复购客：推配套单品(餐桌→餐椅/餐边柜)做连带",
    "sleeping": "沉睡客：保养回访+焕新福利唤醒",
    "normal": "普通客：满意度回访+引导晒单返图",
    "new": "新客：确认收货+保养说明+晒单激励",
}


def list_customers(db: Session) -> list[dict]:
    out = []
    for c in db.scalars(select(Customer).order_by(Customer.order_count.desc())):
        out.append({"id": c.id, "contact": c.contact, "source": c.source,
                    "order_count": c.order_count, "rfm_tier": c.rfm_tier,
                    "reach_suggestion": _REACH.get(c.rfm_tier, "")})
    return out


def summary(db: Session) -> dict:
    rows = db.execute(select(Customer.rfm_tier, func.count())
                      .group_by(Customer.rfm_tier)).all()
    return {"by_tier": dict(rows),
            "total": db.scalar(select(func.count()).select_from(Customer)) or 0}
