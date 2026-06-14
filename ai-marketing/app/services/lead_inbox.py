"""⑩ 线索/私信收件箱。对应 10-lead-inbox.md。

承接问询 + 来源归因(暗号) + 48h跟进 + 成交回写 ERP 订单号。
"""
from __future__ import annotations

import datetime as dt

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Lead

SLA_HOURS = 48


def create_lead(db: Session, *, source_type: str, contact: str = "", question: str = "",
                interest_category: str = "", attribution_code: str = "",
                source_account_id: int | None = None,
                source_content_id: int | None = None) -> Lead:
    lead = Lead(
        source_type=source_type,
        contact=contact,
        question=question,
        interest_category=interest_category,
        attribution_code=attribution_code,
        source_account_id=source_account_id,
        source_content_id=source_content_id,
        status="new",
    )
    db.add(lead)
    db.commit()
    return lead


def list_leads(db: Session, status: str | None = None) -> list[dict]:
    stmt = select(Lead).order_by(Lead.created_at.desc())
    if status:
        stmt = stmt.where(Lead.status == status)
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for lead in db.scalars(stmt):
        last = lead.last_touch_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=dt.timezone.utc)
        overdue = lead.status in ("new", "responded") and (now - last).total_seconds() > SLA_HOURS * 3600
        out.append({
            "id": lead.id,
            "source_type": lead.source_type,
            "attribution_code": lead.attribution_code,
            "contact": lead.contact,
            "question": lead.question,
            "interest_category": lead.interest_category,
            "status": lead.status,
            "erp_order_no": lead.erp_order_no,
            "overdue_48h": overdue,
            "created_at": lead.created_at.isoformat(),
        })
    return out


def update_status(db: Session, lead_id: int, status: str) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise ValueError("线索不存在")
    lead.status = status
    lead.last_touch_at = dt.datetime.now(dt.timezone.utc)
    db.commit()
    return lead


def mark_won(db: Session, lead_id: int, erp_order_no: str) -> Lead:
    """成交回写 ERP 订单号。打通"内容→订单"归因。

    若配了 ERP_BASE_URL，尝试回写一条营销来源标记（best-effort，失败不阻断）。
    """
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise ValueError("线索不存在")
    lead.status = "won"
    lead.erp_order_no = erp_order_no
    lead.last_touch_at = dt.datetime.now(dt.timezone.utc)
    db.commit()

    from . import runtime_config
    erp_base = runtime_config.get("erp_base_url")
    s = get_settings()
    if erp_base and erp_order_no:
        try:
            httpx.post(
                f"{erp_base.rstrip('/')}/api/marketing/lead-attribution",
                headers={"Authorization": f"Bearer {s.erp_token}"} if s.erp_token else {},
                json={"order_no": erp_order_no, "source": "xhs",
                      "attribution_code": lead.attribution_code, "lead_id": lead.id},
                timeout=10,
            )
        except httpx.HTTPError:
            pass  # ERP 不可达不影响线索成交登记
    return lead
