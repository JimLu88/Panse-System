"""⑥ 分发调度中心：ASSIST driver + 反共振错峰。对应 06-dispatcher.md。

ASSIST = 系统排程 + 人工点发（国内默认，不碰自动化）。
反共振 = 同选题多账号强制错峰（递增间隔，不回绕）+ 秒级随机抖动 + 标签打散。
草稿状态机：approved → scheduled → published（全部事件发完才算 published）。
"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, ContentEvent, Draft, PublishEvent


def schedule(db: Session, content_id: int, account_ids: list[int]) -> list[PublishEvent]:
    """把一篇已审稿排到多个账号，强制错峰 + 标签打散。"""
    draft = db.get(Draft, content_id)
    if draft is None:
        raise ValueError("草稿不存在")
    if draft.status == "scheduled":
        raise ValueError("该稿已在发布队列，勿重复排程")
    if draft.status == "published":
        raise ValueError("该稿已发布完成")
    if draft.status != "approved":
        raise ValueError("草稿未通过审核，不能排发布")

    base_tags = list(draft.tags or [])
    events: list[PublishEvent] = []
    now = dt.datetime.now(dt.timezone.utc)
    offset = 0  # 递增错峰：第 i 个号在前一个号基础上 +20~45 分钟，永不回绕撞峰

    for i, acc_id in enumerate(account_ids):
        account = db.get(Account, acc_id)
        if account is None:
            continue
        # 健康红牌 / 非正式期账号不排发布
        if account.health_flag == "red":
            db.add(ContentEvent(content_id=content_id, event_type="dispatch_skipped",
                                payload={"account_id": acc_id, "reason": "health_red"}))
            continue
        if account.stage == "nurturing":
            db.add(ContentEvent(content_id=content_id, event_type="dispatch_skipped",
                                payload={"account_id": acc_id, "reason": "stage_nurturing"}))
            continue
        # #5 新号违规预检：试发期账号更脆弱，A级敏感词也不放行（正式期仅卡S级）
        if account.stage == "trial" and (draft.compliance or {}).get("A"):
            db.add(ContentEvent(content_id=content_id, event_type="dispatch_skipped",
                                payload={"account_id": acc_id, "reason": "trial_strict_compliance",
                                         "hits": draft.compliance.get("A")}))
            continue

        if events:  # 第一个号 T+0，之后递增
            offset += random.randint(20, 45)
        jitter_seconds = random.randint(0, 60)  # 秒级抖动（设计稿"+随机0–60秒"）
        # 标签交叉打散：每个号取不同子集
        variant = random.sample(base_tags, k=min(3, len(base_tags))) if base_tags else []
        ev = PublishEvent(
            content_id=content_id,
            account_id=acc_id,
            platform=account.platform,
            scheduled_at=now + dt.timedelta(minutes=offset, seconds=jitter_seconds),
            driver_used="assist",
            result="pending",
            offset_minutes=offset,
            tag_variant=variant,
        )
        db.add(ev)
        events.append(ev)

    if events:
        draft.status = "scheduled"
    db.add(ContentEvent(content_id=content_id, event_type="dispatch_scheduled",
                        payload={"count": len(events)}))
    db.commit()
    return events


def queue(db: Session) -> list[dict]:
    """发布队列视图（工作台用）：事件 + 稿件标题 + 账号昵称 + 是否已录数据。"""
    from ..models import Metric
    rows = db.execute(
        select(PublishEvent, Draft.title, Account.nickname)
        .join(Draft, Draft.id == PublishEvent.content_id)
        .join(Account, Account.id == PublishEvent.account_id)
        .order_by(PublishEvent.result.desc(), PublishEvent.scheduled_at)
    ).all()
    metric_pairs = set(db.execute(select(Metric.content_id, Metric.account_id)).all())
    return [{
        "event_id": ev.id,
        "content_id": ev.content_id,
        "account_id": ev.account_id,
        "title": title,
        "account": nickname,
        "scheduled_at": ev.scheduled_at.isoformat(),
        "offset_minutes": ev.offset_minutes,
        "tags": ev.tag_variant,
        "result": ev.result,
        "has_metric": (ev.content_id, ev.account_id) in metric_pairs,
    } for ev, title, nickname in rows]


def calendar(db: Session, days: int = 7) -> dict:
    """#12 内容日历：账号 × 日期 网格视图。"""
    from ..models import Account
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    dates = [(today + dt.timedelta(days=i)).isoformat() for i in range(-2, days)]
    accounts = list(db.scalars(select(Account).order_by(Account.id)))

    rows = db.execute(
        select(PublishEvent, Draft.title).join(Draft, Draft.id == PublishEvent.content_id)
    ).all()
    grid: dict[int, dict[str, list]] = {a.id: {d: [] for d in dates} for a in accounts}
    for ev, title in rows:
        d = ev.scheduled_at.date().isoformat()
        if ev.account_id in grid and d in grid[ev.account_id]:
            grid[ev.account_id][d].append({"title": title, "result": ev.result,
                                           "event_id": ev.id})
    return {
        "dates": dates,
        "accounts": [{"id": a.id, "nickname": a.nickname, "stage": a.stage,
                      "cells": grid[a.id]} for a in accounts],
    }


def best_time(db: Session, account_id: int) -> dict:
    """#13 最佳发布时间：按该号历史互动数据算最佳发布小时。"""
    from ..models import Metric
    rows = db.execute(
        select(PublishEvent.published_at, Metric.realness_score, Metric.views)
        .join(Metric, (Metric.content_id == PublishEvent.content_id)
              & (Metric.account_id == PublishEvent.account_id))
        .where(PublishEvent.account_id == account_id,
               PublishEvent.published_at.isnot(None))
    ).all()
    hour_score: dict[int, float] = {}
    hour_n: dict[int, int] = {}
    for published_at, realness, views in rows:
        h = published_at.hour
        hour_score[h] = hour_score.get(h, 0) + (realness or 0) * (views or 1)
        hour_n[h] = hour_n.get(h, 0) + 1
    if not hour_score:
        # 无数据时给家居类经验时段
        return {"best_hours": [7, 12, 21], "based_on": "经验默认（暂无数据）"}
    ranked = sorted(hour_score.items(), key=lambda kv: kv[1] / hour_n[kv[0]], reverse=True)
    return {"best_hours": [h for h, _ in ranked[:3]],
            "based_on": f"{sum(hour_n.values())} 条历史数据"}


def assist_card(db: Session, event_id: int) -> dict:
    """ASSIST 发布卡片：给运营复制粘贴用（标题/正文/标签分段）。"""
    ev = db.get(PublishEvent, event_id)
    if ev is None:
        raise ValueError("发布事件不存在")
    draft = db.get(Draft, ev.content_id)
    return {
        "event_id": ev.id,
        "account_id": ev.account_id,
        "scheduled_at": ev.scheduled_at.isoformat(),
        "offset_minutes": ev.offset_minutes,
        "clipboard": {  # 对应 Alt+1/2/3 剪贴板管道
            "1_title": draft.title,
            "2_body": draft.body,
            "3_tags": " ".join(f"#{t}" for t in ev.tag_variant),
        },
        "reminder": "确认今日未超发布上限后人工发出（10秒倒计时防手滑）",
    }


def mark_published(db: Session, event_id: int) -> PublishEvent:
    """运营点发后回写；该稿全部事件发完才把草稿置为 published。"""
    ev = db.get(PublishEvent, event_id)
    if ev is None:
        raise ValueError("发布事件不存在")
    ev.result = "success"
    ev.published_at = dt.datetime.now(dt.timezone.utc)
    db.add(ContentEvent(content_id=ev.content_id, event_type="published",
                        payload={"account_id": ev.account_id}))
    db.flush()
    remaining = db.scalar(
        select(func.count()).select_from(PublishEvent).where(
            PublishEvent.content_id == ev.content_id, PublishEvent.result != "success")
    ) or 0
    if remaining == 0:
        draft = db.get(Draft, ev.content_id)
        if draft:
            draft.status = "published"
    db.commit()
    return ev
