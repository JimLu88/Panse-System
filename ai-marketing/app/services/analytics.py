"""⑦ 数据回收与分析：真实感归因 + 反哺选题。对应 07-analytics.md。

真实感 = 0.4×提问评论率 + 0.3×用户间互动率 + 0.3×长评论占比。
冷启动权重：新号0.2 / 千粉1.0 / 万粉2.5——参与所有聚合（避免新号偶然数据带偏）。
category_boost 把高真实感品类回流给 ① 选题引擎，闭环最后一环。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Account, Draft, Metric, PublishEvent, Topic


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
    """大盘：发布数 / 加权平均真实感（冷启动权重参与聚合）/ 低真实感待复盘数。"""
    published = db.scalar(select(func.count()).select_from(PublishEvent).where(
        PublishEvent.result == "success")) or 0
    weighted = db.execute(
        select(func.sum(Metric.realness_score * Metric.weight_factor),
               func.sum(Metric.weight_factor))
    ).one()
    w_sum, w_total = (weighted[0] or 0.0), (weighted[1] or 0.0)
    avg_real = round(float(w_sum / w_total), 3) if w_total else 0.0
    low = db.scalar(select(func.count()).select_from(Metric).where(
        Metric.realness_score < 0.6)) or 0
    return {
        "published": published,
        "avg_realness_weighted": avg_real,
        "low_realness_to_review": low,
        "note": "真实感为冷启动加权均值；<0.6 推送人工复盘（假爆款不反哺选题）",
    }


def category_boost(db: Session) -> dict[str, float]:
    """⑦→① 反哺：各品类的加权真实感，>0.6 的部分作为选题加权。

    返回 {品类: boost}，boost = max(0, 加权真实感 − 0.6)。
    """
    rows = db.execute(
        select(Topic.category,
               func.sum(Metric.realness_score * Metric.weight_factor),
               func.sum(Metric.weight_factor))
        .join(Draft, Draft.id == Metric.content_id)
        .join(Topic, Topic.id == Draft.topic_id)
        .group_by(Topic.category)
    ).all()
    out = {}
    for cat, w_sum, w_total in rows:
        if w_total:
            avg = float(w_sum / w_total)
            out[cat] = round(max(0.0, avg - 0.6), 3)
    return out
