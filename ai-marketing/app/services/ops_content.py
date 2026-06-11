"""知乎长答案占坑 + 每周复盘会记录。对应评审建议4(知乎占坑)/6(复盘会)。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ZHIHU_SEED_QUESTIONS
from ..models import ReviewMeeting, ZhihuQuestion


# ---------------- 知乎占坑 ----------------

def ensure_zhihu_seeded(db: Session) -> None:
    if db.scalar(select(ZhihuQuestion).limit(1)) is None:
        for q in ZHIHU_SEED_QUESTIONS:
            db.add(ZhihuQuestion(question=q))
        db.commit()


def list_zhihu(db: Session) -> list[dict]:
    ensure_zhihu_seeded(db)
    rows = db.scalars(select(ZhihuQuestion).order_by(ZhihuQuestion.id))
    return [{"id": z.id, "question": z.question, "status": z.status,
             "answer_url": z.answer_url, "note": z.note} for z in rows]


def update_zhihu(db: Session, qid: int, *, status: str | None = None,
                 answer_url: str | None = None, note: str | None = None) -> ZhihuQuestion:
    z = db.get(ZhihuQuestion, qid)
    if z is None:
        raise ValueError("问题不存在")
    if status is not None:
        z.status = status
    if answer_url is not None:
        z.answer_url = answer_url
    if note is not None:
        z.note = note
    db.commit()
    return z


# ---------------- 复盘会 ----------------

def _week_key(day: dt.date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def list_meetings(db: Session) -> list[dict]:
    rows = db.scalars(select(ReviewMeeting).order_by(ReviewMeeting.id.desc()))
    return [{"id": m.id, "week_key": m.week_key, "hot_case": m.hot_case,
             "flop_case": m.flop_case, "conclusion": m.conclusion,
             "created_at": m.created_at.isoformat()} for m in rows]


def save_meeting(db: Session, *, hot_case: str, flop_case: str, conclusion: str) -> ReviewMeeting:
    week = _week_key(dt.date.today())
    m = db.scalar(select(ReviewMeeting).where(ReviewMeeting.week_key == week))
    if m is None:
        m = ReviewMeeting(week_key=week)
        db.add(m)
    m.hot_case, m.flop_case, m.conclusion = hot_case, flop_case, conclusion
    db.commit()
    return m
