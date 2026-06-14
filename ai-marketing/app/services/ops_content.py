"""知乎长答案占坑 + 每周复盘会记录。对应评审建议4(知乎占坑)/6(复盘会)。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ZHIHU_SEED_QUESTIONS
from ..models import ReviewMeeting, ZhihuQuestion
from .llm_router import get_router


# ---------------- 知乎占坑 ----------------

def ensure_zhihu_seeded(db: Session) -> None:
    if db.scalar(select(ZhihuQuestion).limit(1)) is None:
        for q in ZHIHU_SEED_QUESTIONS:
            db.add(ZhihuQuestion(question=q))
        db.commit()


def generate_zhihu_answer(db: Session, qid: int) -> ZhihuQuestion:
    """为一个知乎问题自动生成答案初稿（AI），状态置 writing。"""
    z = db.get(ZhihuQuestion, qid)
    if z is None:
        raise ValueError("问题不存在")
    router = get_router()
    z.answer_draft = router.complete(
        "zhihu.answer",
        f"用知乎理性分析体回答家具问题，结构化、给避坑清单。问题：{z.question}",
    )
    if z.status == "todo":
        z.status = "writing"
    db.commit()
    return z


def generate_all_zhihu_answers(db: Session) -> int:
    """给所有还没有初稿的问题批量生成。返回生成条数。"""
    ensure_zhihu_seeded(db)
    todo = db.scalars(select(ZhihuQuestion).where(ZhihuQuestion.answer_draft == "")).all()
    for z in todo:
        generate_zhihu_answer(db, z.id)
    return len(todo)


def list_zhihu(db: Session) -> list[dict]:
    ensure_zhihu_seeded(db)
    rows = db.scalars(select(ZhihuQuestion).order_by(ZhihuQuestion.id))
    return [{"id": z.id, "question": z.question, "status": z.status,
             "has_draft": bool(z.answer_draft), "answer_draft": z.answer_draft,
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


def suggest_conclusion(db: Session) -> dict:
    """从数据自动给一句复盘行动建议（AI + 品类 boost 事实）。"""
    from . import analytics
    boost = analytics.category_boost(db)
    top = sorted(boost.items(), key=lambda kv: kv[1], reverse=True)
    ov = analytics.overview(db)
    facts = []
    if top and top[0][1] > 0:
        facts.append(f"「{top[0][0]}」品类真实感最高，建议下周加大投入")
    if ov["low_realness_to_review"]:
        facts.append(f"有 {ov['low_realness_to_review']} 篇真实感偏低需复盘")
    ai = get_router().complete("review.suggest", "根据家具内容数据给一句下周行动建议")
    return {"suggestion": ("；".join(facts) + "。" if facts else "") + ai,
            "facts": facts}
