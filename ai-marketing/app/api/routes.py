"""API 路由汇总。薄路由，业务在 services 层。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas import (
    AccountProfileIn,
    ComplianceCheckIn,
    DraftGenIn,
    HealthIn,
    LeadIn,
    LeadStatusIn,
    LeadWonIn,
    MeetingIn,
    MetricIn,
    ReviewActionIn,
    ScheduleIn,
    TopicGenIn,
    ZhihuUpdateIn,
)
from ..services import (
    account_service,
    analytics,
    comment_engine,
    data_source,
    dispatcher,
    generator,
    lead_inbox,
    nurture,
    ops_checklist,
    ops_content,
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
    # 普通人友好：给"条数"则自动换算比例（提问数/长评数/互回数 ÷ 总评论数）
    q_rate, i_rate, l_ratio = body.question_rate, body.interaction_rate, body.long_comment_ratio
    if body.comments > 0:
        if body.question_comments is not None:
            q_rate = min(body.question_comments / body.comments, 1.0)
        if body.reply_comments is not None:
            i_rate = min(body.reply_comments / body.comments, 1.0)
        if body.long_comments is not None:
            l_ratio = min(body.long_comments / body.comments, 1.0)
    m = analytics.record_metric(db, body.content_id, body.account_id, views=body.views,
                                likes=body.likes, comments=body.comments, collects=body.collects,
                                question_rate=q_rate,
                                interaction_rate=i_rate,
                                long_comment_ratio=l_ratio)
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


@router.get("/watchdog")
def watchdog_status(db: Session = Depends(get_db)):
    """看门狗状态 + 最近20次体检记录。"""
    from ..services import watchdog
    if watchdog.STATE["last_check_at"] is None:
        watchdog.check_once()
    return watchdog.status(db)


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


# ---------------- 今日待办（首页） ----------------
@router.get("/home")
def home_dashboard(db: Session = Depends(get_db)):
    """普通人首页：今天该做什么，一屏看完。"""
    import datetime as dt
    from sqlalchemy import func, select
    from ..models import (Account, CommentOpportunity, Draft, Lead, Metric,
                          NurtureTask, PublishEvent)

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    today = dt.date.today().isoformat()

    to_review = db.scalar(select(func.count()).select_from(Draft)
                          .where(Draft.status == "drafted")) or 0
    pending_pub = db.scalars(select(PublishEvent)
                             .where(PublishEvent.result == "pending")).all()
    due_pub = sum(1 for e in pending_pub if e.scheduled_at <= now)
    comments_pending = db.scalar(select(func.count()).select_from(CommentOpportunity)
                                 .where(CommentOpportunity.status == "pending")) or 0
    nurture_left = db.scalar(select(func.count()).select_from(NurtureTask)
                             .where(NurtureTask.period_key == today,
                                    NurtureTask.done.is_(False))) or 0
    overdue = sum(1 for l in lead_inbox.list_leads(db) if l["overdue_48h"])

    # 数据待录：已发出但还没录指标的发布事件
    metric_pairs = set(db.execute(select(Metric.content_id, Metric.account_id)).all())
    success = db.scalars(select(PublishEvent)
                         .where(PublishEvent.result == "success")).all()
    data_missing = sum(1 for e in success
                       if (e.content_id, e.account_id) not in metric_pairs)

    ops = ops_checklist.today(db)
    return {
        "to_review": to_review,
        "to_publish": len(pending_pub),
        "due_publish": due_pub,
        "comments_pending": comments_pending,
        "nurture_left": nurture_left,
        "overdue_leads": overdue,
        "data_missing": data_missing,
        "ops_done": ops["done"],
        "ops_total": ops["total"],
    }


# ---------------- 运营台账 ----------------
@router.get("/ops/today")
def ops_today(db: Session = Depends(get_db)):
    return ops_checklist.today(db)


@router.post("/ops/task/{task_id}/toggle")
def ops_toggle(task_id: int, db: Session = Depends(get_db)):
    t = _err(ops_checklist.toggle, db, task_id)
    return {"id": t.id, "done": t.done}


# ---------------- 知乎占坑 ----------------
@router.get("/zhihu")
def zhihu_list(db: Session = Depends(get_db)):
    return ops_content.list_zhihu(db)


@router.post("/zhihu/{qid}")
def zhihu_update(qid: int, body: ZhihuUpdateIn, db: Session = Depends(get_db)):
    z = _err(ops_content.update_zhihu, db, qid, status=body.status,
             answer_url=body.answer_url, note=body.note)
    return {"id": z.id, "status": z.status}


# ---------------- 复盘会 ----------------
@router.get("/review-meetings")
def meetings_list(db: Session = Depends(get_db)):
    return ops_content.list_meetings(db)


@router.post("/review-meetings")
def meetings_save(body: MeetingIn, db: Session = Depends(get_db)):
    m = ops_content.save_meeting(db, hot_case=body.hot_case,
                                 flop_case=body.flop_case, conclusion=body.conclusion)
    return {"id": m.id, "week_key": m.week_key}


# ---------------- 合规自查 ----------------
@router.post("/compliance/check")
def compliance_check(body: ComplianceCheckIn):
    """粘贴文案 → 违禁词分级扫描（广告法红线自查工具）。"""
    from ..services import compliance
    hits = compliance.scan_banned(body.text)
    return {
        "hits": hits,
        "blocked": bool(hits["S"]),
        "verdict": ("S级命中，禁止发布" if hits["S"]
                    else "A级命中，必须改写后发布" if hits["A"]
                    else "B级提醒，建议软化" if hits["B"] else "未发现违禁词"),
    }


@router.get("/compliance/words")
def compliance_words():
    """红线清单展示（培训页用）。"""
    from ..config import BANNED_WORDS
    return BANNED_WORDS


# ---------------- 账号档案管理 ----------------
@router.patch("/accounts/{account_id}/profile")
def account_profile(account_id: int, body: AccountProfileIn, db: Session = Depends(get_db)):
    from ..models import Account
    a = db.get(Account, account_id)
    if a is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    if body.real_person is not None:
        a.real_person = body.real_person
    if body.device_note is not None:
        a.device_note = body.device_note
    if body.sim_note is not None:
        a.sim_note = body.sim_note
    if body.official_setup is not None:
        a.official_setup = body.official_setup
    db.commit()
    return {"id": a.id, "real_person": a.real_person, "device_note": a.device_note,
            "sim_note": a.sim_note, "official_setup": a.official_setup}


# ---------------- 引导数据 ----------------
@router.get("/promo-calendar")
def promo_calendar():
    """大促节点 + 种草开始日（提前45天）。"""
    import datetime as dt
    from ..config import PROMO_CALENDAR, PROMO_LEAD_DAYS
    today = dt.date.today()
    out = []
    for p in PROMO_CALENDAR:
        d = dt.date(today.year, p["month"], p["day"])
        if d < today:
            d = dt.date(today.year + 1, p["month"], p["day"])
        seed_start = d - dt.timedelta(days=PROMO_LEAD_DAYS)
        out.append({"name": p["name"], "date": d.isoformat(),
                    "seed_start": seed_start.isoformat(),
                    "days_to_seed": (seed_start - today).days,
                    "should_seed_now": seed_start <= today < d})
    out.sort(key=lambda x: x["date"])
    return out[:4]


@router.get("/faq-scripts")
def faq_scripts():
    """私信话术库（含老客返图邀约），一键复制。"""
    from ..config import FAQ_SCRIPTS
    return FAQ_SCRIPTS


@router.get("/datasource/status")
def datasource_status():
    return data_source.status()
