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
        if account.stage != "active":
            db.add(ContentEvent(content_id=content_id, event_type="dispatch_skipped",
                                payload={"account_id": acc_id, "reason": f"stage_{account.stage}"}))
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
    """发布队列视图（工作台用）：事件 + 稿件标题 + 账号昵称。"""
    rows = db.execute(
        select(PublishEvent, Draft.title, Account.nickname)
        .join(Draft, Draft.id == PublishEvent.content_id)
        .join(Account, Account.id == PublishEvent.account_id)
        .order_by(PublishEvent.result.desc(), PublishEvent.scheduled_at)
    ).all()
    return [{
        "event_id": ev.id,
        "content_id": ev.content_id,
        "title": title,
        "account": nickname,
        "scheduled_at": ev.scheduled_at.isoformat(),
        "offset_minutes": ev.offset_minutes,
        "tags": ev.tag_variant,
        "result": ev.result,
    } for ev, title, nickname in rows]


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
