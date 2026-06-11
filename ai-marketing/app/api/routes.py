"""API 路由汇总。薄路由，业务在 services 层。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import (
    DraftGenIn,
    HealthIn,
    LeadIn,
    LeadStatusIn,
    LeadWonIn,
    MetricIn,
    ReviewActionIn,
    ScheduleIn,
    TopicGenIn,
)
from ..services import (
    account_service,
    analytics,
    comment_engine,
    dispatcher,
    generator,
    lead_inbox,
    nurture,
    review,
    topic_engine,
)

router = APIRouter(prefix="/api")


def _err(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# ---------------- ① 选题 ----------------
@router.post("/topics/generate")
def topics_generate(body: TopicGenIn, db: Session = Depends(get_db)):
    topics = topic_engine.generate_topics(db, body.category, body.count)
    return [{"id": t.id, "title": t.title, "kind": t.topic_kind,
             "heat_score": t.heat_score, "heat_status": t.heat_status} for t in topics]


@router.get("/topics")
def topics_list(db: Session = Depends(get_db)):
    from ..models import Topic
    from sqlalchemy import select
    return [{"id": t.id, "title": t.title, "category": t.category, "kind": t.topic_kind,
             "heat_score": t.heat_score} for t in db.scalars(select(Topic).order_by(Topic.id.desc()))]


# ---------------- ③ 生成 ----------------
@router.post("/drafts/generate")
def drafts_generate(body: DraftGenIn, db: Session = Depends(get_db)):
    d = _err(generator.generate_draft, db, body.topic_id, body.account_id)
    return {"id": d.id, "title": d.title, "ai_likeness": d.ai_likeness,
            "info_density": d.info_density, "compliance": d.compliance, "status": d.status}


@router.get("/drafts")
def drafts_list(db: Session = Depends(get_db)):
    from ..models import Draft
    from sqlalchemy import select
    return [{"id": d.id, "title": d.title, "status": d.status, "ai_likeness": d.ai_likeness}
            for d in db.scalars(select(Draft).order_by(Draft.id.desc()))]


# ---------------- ③.5 审核工位 ----------------
@router.get("/review/{draft_id}")
def review_get(draft_id: int, db: Session = Depends(get_db)):
    return _err(review.review_report, db, draft_id)


@router.post("/review/{draft_id}/approve")
def review_approve(draft_id: int, body: ReviewActionIn | None = None, db: Session = Depends(get_db)):
    d = _err(review.approve, db, draft_id, body.note if body else "")
    return {"id": d.id, "status": d.status}


@router.post("/review/{draft_id}/reject")
def review_reject(draft_id: int, body: ReviewActionIn, db: Session = Depends(get_db)):
    d = _err(review.reject, db, draft_id, body.reason)
    return {"id": d.id, "status": d.status}


# ---------------- ⑤ 账号 ----------------
@router.get("/accounts")
def accounts_list(db: Session = Depends(get_db)):
    return account_service.health_dashboard(db)


@router.post("/accounts/{account_id}/health")
def account_health(account_id: int, body: HealthIn, db: Session = Depends(get_db)):
    a = _err(account_service.update_health, db, account_id,
             post_alive_rate=body.post_alive_rate, real_comment_rate=body.real_comment_rate)
    return {"id": a.id, "health_score": a.health_score, "health_flag": a.health_flag}


# ---------------- ⑥ 分发 ----------------
@router.post("/dispatch/schedule")
def dispatch_schedule(body: ScheduleIn, db: Session = Depends(get_db)):
    events = _err(dispatcher.schedule, db, body.content_id, body.account_ids)
    return [{"event_id": e.id, "account_id": e.account_id,
             "offset_minutes": e.offset_minutes, "tags": e.tag_variant} for e in events]


@router.get("/dispatch/queue")
def dispatch_queue(db: Session = Depends(get_db)):
    return dispatcher.queue(db)


@router.get("/dispatch/{event_id}/card")
def dispatch_card(event_id: int, db: Session = Depends(get_db)):
    return _err(dispatcher.assist_card, db, event_id)


@router.post("/dispatch/{event_id}/published")
def dispatch_published(event_id: int, db: Session = Depends(get_db)):
    e = _err(dispatcher.mark_published, db, event_id)
    return {"event_id": e.id, "result": e.result}


# ---------------- ⑦ 数据回收 ----------------
@router.post("/metrics")
def metrics_record(body: MetricIn, db: Session = Depends(get_db)):
    m = analytics.record_metric(db, body.content_id, body.account_id, views=body.views,
                                likes=body.likes, comments=body.comments, collects=body.collects,
                                question_rate=body.question_rate,
                                interaction_rate=body.interaction_rate,
                                long_comment_ratio=body.long_comment_ratio)
    return {"id": m.id, "realness_score": m.realness_score, "weight_factor": m.weight_factor}


@router.get("/analytics/overview")
def analytics_overview(db: Session = Depends(get_db)):
    return analytics.overview(db)


@router.get("/analytics/category-boost")
def analytics_boost(db: Session = Depends(get_db)):
    """⑦→① 反哺：各品类真实感加权（>0 的品类选题会被加权）。"""
    return analytics.category_boost(db)


@router.get("/content/{content_id}/events")
def content_events(content_id: int, db: Session = Depends(get_db)):
    """事件流时间线（事件溯源可见化）。"""
    from sqlalchemy import select
    from ..models import ContentEvent
    rows = db.scalars(
        select(ContentEvent).where(ContentEvent.content_id == content_id,
                                   ContentEvent.event_type != "topic_chosen")
        .order_by(ContentEvent.id)
    )
    return [{"event_type": e.event_type, "payload": e.payload,
             "at": e.created_at.isoformat()} for e in rows]


@router.get("/digest")
def digest():
    """调度器摘要：超期线索 / 到点未发事件。"""
    from ..services import scheduler
    if scheduler.DIGEST["generated_at"] is None:
        scheduler.run_once()
    return scheduler.DIGEST


# ---------------- ⑧ 评论引流 ----------------
@router.post("/comments/scan")
def comments_scan(db: Session = Depends(get_db)):
    opps = comment_engine.scan_opportunities(db)
    return [{"id": o.id, "note_title": o.note_title, "note_kind": o.note_kind,
             "growth_rate": o.growth_rate, "match_category": o.match_category,
             "match_score": o.match_score, "comment_kind": o.comment_kind,
             "draft_comment": o.draft_comment, "suggested_account_id": o.suggested_account_id,
             "status": o.status} for o in opps]


@router.get("/comments")
def comments_list(db: Session = Depends(get_db)):
    from ..models import CommentOpportunity
    from sqlalchemy import select
    return [{"id": o.id, "note_title": o.note_title, "draft_comment": o.draft_comment,
             "comment_kind": o.comment_kind, "status": o.status,
             "suggested_account_id": o.suggested_account_id}
            for o in db.scalars(select(CommentOpportunity).order_by(CommentOpportunity.id.desc()))]


@router.post("/comments/{opp_id}/post")
def comments_post(opp_id: int, account_id: int | None = None, db: Session = Depends(get_db)):
    o = _err(comment_engine.mark_posted, db, opp_id, account_id)
    return {"id": o.id, "status": o.status, "posted_by": o.posted_by_account_id}


@router.post("/comments/{opp_id}/skip")
def comments_skip(opp_id: int, db: Session = Depends(get_db)):
    o = _err(comment_engine.skip, db, opp_id)
    return {"id": o.id, "status": o.status}


# ---------------- ⑨ 养号 ----------------
@router.get("/nurture/{account_id}/today")
def nurture_today(account_id: int, db: Session = Depends(get_db)):
    return _err(nurture.today_tasks, db, account_id)


@router.post("/nurture/task/{task_id}/check")
def nurture_check(task_id: int, db: Session = Depends(get_db)):
    t = _err(nurture.check_task, db, task_id)
    return {"id": t.id, "done": t.done}


@router.post("/nurture/{account_id}/promote")
def nurture_promote(account_id: int, db: Session = Depends(get_db)):
    return _err(nurture.try_promote, db, account_id)


# ---------------- ⑩ 线索 ----------------
@router.post("/leads")
def leads_create(body: LeadIn, db: Session = Depends(get_db)):
    lead = lead_inbox.create_lead(db, source_type=body.source_type, contact=body.contact,
                                  question=body.question, interest_category=body.interest_category,
                                  attribution_code=body.attribution_code,
                                  source_account_id=body.source_account_id,
                                  source_content_id=body.source_content_id)
    return {"id": lead.id, "status": lead.status}


@router.get("/leads")
def leads_list(status: str | None = None, db: Session = Depends(get_db)):
    return lead_inbox.list_leads(db, status)


@router.post("/leads/{lead_id}/status")
def leads_status(lead_id: int, body: LeadStatusIn, db: Session = Depends(get_db)):
    lead = _err(lead_inbox.update_status, db, lead_id, body.status)
    return {"id": lead.id, "status": lead.status}


@router.post("/leads/{lead_id}/won")
def leads_won(lead_id: int, body: LeadWonIn, db: Session = Depends(get_db)):
    lead = _err(lead_inbox.mark_won, db, lead_id, body.erp_order_no)
    return {"id": lead.id, "status": lead.status, "erp_order_no": lead.erp_order_no}


@router.get("/leads/export")
def leads_export(db: Session = Depends(get_db)):
    """线索 CSV 导出（ERP 回写接口未就绪时的对账降级方案）。"""
    import csv
    import io
    from fastapi.responses import PlainTextResponse
    rows = lead_inbox.list_leads(db)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["id", "source_type", "attribution_code",
                                             "contact", "question", "interest_category",
                                             "status", "erp_order_no", "created_at"],
                            extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return PlainTextResponse(buf.getvalue(), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=leads.csv"})
