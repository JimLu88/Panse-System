"""① 选题与热点引擎。对应 01-topic-engine.md（含评审补充的长尾搜索词）。

MVP：规则版热度 + 长尾搜索词选题 + 72h 标题去重 + 安全发布窗口 + ⑦反哺加权。
真实场景接平台热榜 API / 挂载项 A 爬虫。
"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import PRODUCT_KEYWORDS
from ..models import ContentEvent, Topic
from . import analytics

# 长尾搜索词模板（常青选题：半衰期以年计，对家具转化价值高）
_LONGTAIL = [
    "{kw}怎么选不踩坑",
    "小户型{kw}推荐清单",
    "{kw}保养指南",
    "{kw}真实测评：用半年后",
    "预算一万买什么{kw}",
]
# 时效热点模板
_TREND = [
    "{kw}今年流行什么款式",
    "新中式{kw}爆款盘点",
    "{kw}|装修博主都在推",
]

_DEDUP_HOURS = 72


def generate_topics(db: Session, category: str, count: int = 3) -> list[Topic]:
    keywords = PRODUCT_KEYWORDS.get(category, [category])
    # ⑦→① 反哺：该品类历史真实感高 → 热度加权
    boost = analytics.category_boost(db).get(category, 0.0)

    # 72h 内已用过的标题不重复出
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=_DEDUP_HOURS)
    recent_titles = set(db.scalars(
        select(Topic.title).where(Topic.created_at >= since.replace(tzinfo=None))
    ))

    topics: list[Topic] = []
    now = dt.datetime.now(dt.timezone.utc)
    for _ in range(count):
        title = None
        is_evergreen = random.random() < 0.6  # 家具偏常青
        for _retry in range(8):  # 去重重抽
            kw = random.choice(keywords)
            template = random.choice(_LONGTAIL if is_evergreen else _TREND)
            candidate = template.format(kw=kw)
            if candidate not in recent_titles:
                title = candidate
                break
        if title is None:
            continue  # 模板都用过了，本条跳过
        recent_titles.add(title)

        heat = (random.randint(40, 75) if is_evergreen else random.randint(60, 95))
        heat = min(100, heat + int(boost * 20))  # 反哺加权
        status = "safe" if is_evergreen else random.choice(["peak", "safe", "decay"])
        window_days = 365 if is_evergreen else 2  # 安全发布窗口：常青一年 / 时效48h
        t = Topic(
            title=title,
            category=category,
            platform_targets=["xhs"],
            heat_score=heat,
            heat_status=status,
            topic_kind="evergreen" if is_evergreen else "trend",
            keywords=[kw],
            safe_window={"start": now.isoformat(),
                         "end": (now + dt.timedelta(days=window_days)).isoformat()},
            recommended_style="diary",
        )
        db.add(t)
        db.flush()
        db.add(ContentEvent(content_id=t.id, event_type="topic_chosen",
                            payload={"title": title, "kind": t.topic_kind,
                                     "feedback_boost": boost}))
        topics.append(t)
    db.commit()
    return topics
