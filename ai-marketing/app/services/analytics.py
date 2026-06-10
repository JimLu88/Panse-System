"""⑦ 数据回收与分析：真实感归因。对应 07-analytics.md。

真实感 = 0.4×提问评论率 + 0.3×用户间互动率 + 0.3×长评论占比。
冷启动权重：新号0.2 / 千粉1.0 / 万粉2.5，避免新号偶然数据带偏选题。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, Metric, PublishEvent


def realness(question_rate: float, interaction_rate: float, long_comment_ratio: float) -> float:
    return round(0.4 * question_rate + 0.3 * interaction_rate + 0.3 * long_comment_ratio, 3)


def weight_for(follower_count: int) -> float:
    if follower_count < 1000:
        return 0.2
    if follower_count < 10000:
        return 1.0
    return 2.5


def record_metric(db: Session, content_id: int, account_id: int, *, views: int, likes: int,
                  comments: int, collects: int, question_rate: float = 0.0,
                  interaction_rate: float = 0.0, long_comment_ratio: float = 0.0) -> Metric:
    account = db.get(Account, account_id)
    score = realness(question_rate, interaction_rate, long_comment_ratio)
    m = Metric(
        content_id=content_id, account_id=account_id, checkpoint="T+24h",
        views=views, likes=likes, comments=comments, collects=collects,
        realness_score=score,
        weight_factor=weight_for(account.follower_count if account else 0),
    )
    db.add(m)
    db.commit()
    return m


def overview(db: Session) -> dict:
    """大盘：发布数 / 平均真实感 / 低真实感待复盘数。"""
    published = db.scalar(select(func.count()).select_from(PublishEvent).where(
        PublishEvent.result == "success")) or 0
    avg_real = db.scalar(select(func.avg(Metric.realness_score))) or 0.0
    low = db.scalar(select(func.count()).select_from(Metric).where(
        Metric.realness_score < 0.6)) or 0
    return {
        "published": published,
        "avg_realness": round(float(avg_real), 3),
        "low_realness_to_review": low,
        "note": "真实感 < 0.6 推送人工复盘（假爆款不反哺选题）",
    }
