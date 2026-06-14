"""周报：把"内容→分发→引流→成交"一周全链路汇总成一页，可推飞书。

闭环的收口——运营/老板每周看这一页就知道矩阵在产出什么、带来了什么。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (CommentOpportunity, Draft, InboundComment, Lead, Metric,
                      PublishEvent, Topic)
from . import analytics


def _week_range() -> tuple[dt.datetime, str]:
    now = dt.datetime.now(dt.timezone.utc)
    start = (now - dt.timedelta(days=7)).replace(tzinfo=None)
    iso = now.isocalendar()
    return start, f"{iso.year}-W{iso.week:02d}"


def build(db: Session) -> dict:
    start, week_key = _week_range()

    published = db.scalar(select(func.count()).select_from(PublishEvent).where(
        PublishEvent.result == "success", PublishEvent.published_at >= start)) or 0
    comments_posted = db.scalar(select(func.count()).select_from(CommentOpportunity).where(
        CommentOpportunity.status == "posted")) or 0
    inbound = db.scalar(select(func.count()).select_from(InboundComment)) or 0
    leads_new = db.scalar(select(func.count()).select_from(Lead).where(
        Lead.created_at >= start)) or 0
    leads_won = db.scalar(select(func.count()).select_from(Lead).where(
        Lead.status == "won")) or 0

    ov = analytics.overview(db)
    boost = analytics.category_boost(db)
    top_cat = sorted(boost.items(), key=lambda kv: kv[1], reverse=True)

    # 本周最佳内容（真实感最高）
    best = db.execute(
        select(Topic.title, Metric.realness_score, Metric.views)
        .join(Draft, Draft.id == Metric.content_id)
        .join(Topic, Topic.id == Draft.topic_id)
        .order_by(Metric.realness_score.desc()).limit(3)
    ).all()

    return {
        "week": week_key,
        "published": published,
        "comments_posted": comments_posted,
        "inbound_comments": inbound,
        "leads_new": leads_new,
        "leads_won": leads_won,
        "avg_realness": ov["avg_realness_weighted"],
        "low_realness_to_review": ov["low_realness_to_review"],
        "top_category": top_cat[0][0] if top_cat and top_cat[0][1] > 0 else None,
        "best_content": [{"title": t, "realness": r, "views": v} for t, r, v in best],
    }


def to_text(report: dict) -> str:
    lines = [
        f"📊 内容矩阵周报 {report['week']}",
        f"发布笔记 {report['published']} · 评论引流 {report['comments_posted']} · 收到评论 {report['inbound_comments']}",
        f"新增线索 {report['leads_new']} · 成交 {report['leads_won']}",
        f"加权真实感 {report['avg_realness']} · 待复盘 {report['low_realness_to_review']} 篇",
    ]
    if report["top_category"]:
        lines.append(f"最值得加投品类：{report['top_category']}")
    if report["best_content"]:
        lines.append("本周最佳：" + "；".join(b["title"] for b in report["best_content"][:2]))
    return "\n".join(lines)


def push(db: Session) -> dict:
    from . import notifier
    report = build(db)
    sent = notifier.send_feishu(to_text(report))
    return {"report": report, "pushed_to_feishu": sent}
