"""采集业务：竞品爆文挖掘 / 低粉爆文嗅探 / 评论词云 / 数据自动回采。

对标 MediaCrawler(采集) + XiaoFeiShu(爆文/流量嗅探)。全部只读。
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import HotNote, Metric, PublishEvent
from . import analytics, data_source

# 低粉爆文阈值：粉丝 < 2000 但点赞 > 5000 → 内容赢，最可复制
LOW_FAN_MAX = 2000
LOW_FAN_MIN_LIKES = 5000

# 评论词云停用词 + 尾部语气词（用于把"米够用吗"切成"够用"）
_STOP = set("的了吗呀啊吧呢和与或这那有没我你他她它们个是不在求好看怎么多少")
_TRAIL = "吗呢吧啊呀的了么哇嘛"
_LEAD = "这那有没求好太很都也还就"


def mine_hot_notes(db: Session, category: str = "") -> dict:
    """抓竞品爆文入库，标注低粉爆文。返回 {total, low_fan, source}。"""
    notes, source = data_source.fetch_hot_notes(category)
    existing = set(db.scalars(select(HotNote.title)))
    low = 0
    added = 0
    for n in notes:
        if n["title"] in existing:
            continue
        is_low = n.get("fans", 0) < LOW_FAN_MAX and n.get("likes", 0) >= LOW_FAN_MIN_LIKES
        low += int(is_low)
        db.add(HotNote(
            platform=n.get("platform", "xhs"), title=n["title"], author=n.get("author", ""),
            author_followers=n.get("fans", 0), likes=n.get("likes", 0),
            collects=n.get("collects", 0), comments_count=n.get("comments", 0),
            category=category, cover_style=n.get("cover", ""), structure=n.get("structure", ""),
            is_low_fan_hit=is_low, sample_comments=n.get("comments_sample", []),
            url=n.get("url", ""),
        ))
        added += 1
    db.commit()
    return {"added": added, "low_fan": low, "source": source}


def list_hot_notes(db: Session, low_fan_only: bool = False) -> list[dict]:
    stmt = select(HotNote).order_by(HotNote.likes.desc())
    if low_fan_only:
        stmt = stmt.where(HotNote.is_low_fan_hit.is_(True))
    return [{"id": h.id, "title": h.title, "author": h.author,
             "author_followers": h.author_followers, "likes": h.likes,
             "collects": h.collects, "comments_count": h.comments_count,
             "cover_style": h.cover_style, "structure": h.structure,
             "is_low_fan_hit": h.is_low_fan_hit, "url": h.url}
            for h in db.scalars(stmt)]


def comment_cloud(db: Session, top: int = 20) -> list[dict]:
    """汇总爆文评论样本 → 词云（用户真实问题/痛点，反推选题）。"""
    counter: Counter[str] = Counter()
    for h in db.scalars(select(HotNote)):
        for c in (h.sample_comments or []):
            for raw in re.findall(r"[一-龥]{2,}", c):
                token = raw.strip(_TRAIL).lstrip(_LEAD).strip(_TRAIL)
                if 2 <= len(token) <= 6 and token not in _STOP:
                    counter[token] += 1
    return [{"word": w, "count": c} for w, c in counter.most_common(top)]


def import_crawled(db: Session, payload: dict) -> dict:
    """#真实数据导入：无需起 HTTP 服务，直接把采集器导出的 JSON 灌进来。

    payload 形如 {"hot_notes":[...], "mentions":[...]}，字段同 data_source 契约。
    供"先用爬虫导出文件、再上传"的轻量接入路径。
    """
    from ..models import BrandMention
    hot = payload.get("hot_notes") or []
    mentions = payload.get("mentions") or []
    seen = set(db.scalars(select(HotNote.title)))
    h_added = 0
    for n in hot:
        if n.get("title") in seen:
            continue
        is_low = n.get("fans", 0) < LOW_FAN_MAX and n.get("likes", 0) >= LOW_FAN_MIN_LIKES
        db.add(HotNote(
            platform=n.get("platform", "xhs"), title=n.get("title", ""),
            author=n.get("author", ""), author_followers=n.get("fans", 0),
            likes=n.get("likes", 0), collects=n.get("collects", 0),
            comments_count=n.get("comments", 0), category=n.get("category", ""),
            cover_style=n.get("cover", ""), structure=n.get("structure", ""),
            is_low_fan_hit=is_low, sample_comments=n.get("comments_sample", []),
            url=n.get("url", ""),
        ))
        h_added += 1
    m_added = 0
    seen_m = set(db.scalars(select(BrandMention.note_title)))
    for m in mentions:
        if m.get("title") in seen_m:
            continue
        db.add(BrandMention(
            platform=m.get("platform", "xhs"), mention_type=m.get("type", "brand"),
            keyword=m.get("keyword", ""), note_title=m.get("title", ""),
            snippet=m.get("snippet", ""), sentiment=m.get("sentiment", "neutral"),
            url=m.get("url", ""), status="new",
        ))
        m_added += 1
    db.commit()
    return {"hot_notes_added": h_added, "mentions_added": m_added}


def auto_collect_metrics(db: Session) -> dict:
    """#16 数据自动回采：给已发布但没数据的笔记拉取指标（接爬虫则真实，否则演示）。"""
    metric_pairs = set(db.execute(select(Metric.content_id, Metric.account_id)).all())
    success = db.scalars(select(PublishEvent).where(PublishEvent.result == "success")).all()
    collected = 0
    source = "mock"
    for ev in success:
        if (ev.content_id, ev.account_id) in metric_pairs:
            continue
        data, source = data_source.fetch_note_metrics(f"{ev.content_id}_{ev.account_id}")
        if not data:
            continue
        # 真实感比例采集器通常给不全，缺省按估算（评论提问率等真实采集可带）
        analytics.record_metric(
            db, ev.content_id, ev.account_id,
            views=data.get("views", 0), likes=data.get("likes", 0),
            comments=data.get("comments", 0), collects=data.get("collects", 0),
            question_rate=data.get("question_rate", 0.3),
            interaction_rate=data.get("interaction_rate", 0.2),
            long_comment_ratio=data.get("long_comment_ratio", 0.3),
        )
        collected += 1
    return {"collected": collected, "source": source}
