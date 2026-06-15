"""A/B 实验 + 全漏斗归因。

A/B：封面/标题/钩子/时间多臂老虎机(UCB1)自动择优。
全漏斗：内容→搜索暗号→进店→成交，按暗号串联(复用 Lead.attribution_code)。
"""
from __future__ import annotations

import math

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Experiment, Lead, PublishEvent


def create_experiment(db: Session, name: str, factor: str, arm_names: list[str]) -> Experiment:
    e = Experiment(name=name, factor=factor,
                   arms=[{"name": n, "impressions": 0, "reward": 0.0} for n in arm_names],
                   status="running")
    db.add(e)
    db.commit()
    return e


def record_result(db: Session, exp_id: int, arm_name: str, reward: float) -> Experiment:
    """记录某臂一次曝光与回报(如真实感/点击率/成交)。"""
    e = db.get(Experiment, exp_id)
    if e is None:
        raise ValueError("实验不存在")
    arms = e.arms
    for a in arms:
        if a["name"] == arm_name:
            a["impressions"] += 1
            a["reward"] += reward
            break
    e.arms = arms
    db.commit()
    return e


def recommend_arm(db: Session, exp_id: int) -> dict:
    """UCB1 给出下一个该试的臂（平衡探索/利用）。"""
    e = db.get(Experiment, exp_id)
    if e is None:
        raise ValueError("实验不存在")
    total = sum(a["impressions"] for a in e.arms) or 1
    best, best_score = None, -1.0
    for a in e.arms:
        n = a["impressions"]
        if n == 0:
            return {"recommend": a["name"], "reason": "未试过，优先探索"}
        avg = a["reward"] / n
        ucb = avg + math.sqrt(2 * math.log(total) / n)
        if ucb > best_score:
            best, best_score = a["name"], ucb
    return {"recommend": best, "reason": f"UCB1 得分最高 {best_score:.3f}"}


def conclude(db: Session, exp_id: int) -> Experiment:
    e = db.get(Experiment, exp_id)
    if e is None:
        raise ValueError("实验不存在")
    ranked = sorted(e.arms, key=lambda a: (a["reward"] / a["impressions"]) if a["impressions"] else 0,
                    reverse=True)
    e.winner = ranked[0]["name"] if ranked else ""
    e.status = "done"
    db.commit()
    return e


def list_experiments(db: Session) -> list[dict]:
    out = []
    for e in db.scalars(select(Experiment).order_by(Experiment.id.desc())):
        arms = [{**a, "ctr": round(a["reward"] / a["impressions"], 3) if a["impressions"] else 0}
                for a in e.arms]
        out.append({"id": e.id, "name": e.name, "factor": e.factor, "arms": arms,
                    "status": e.status, "winner": e.winner})
    return out


def funnel(db: Session) -> dict:
    """全漏斗：发布 → 评论引流(暗号) → 线索 → 成交。"""
    published = db.scalar(select(func.count()).select_from(PublishEvent)
                          .where(PublishEvent.result == "success")) or 0
    # 有归因暗号的线索 = 内容/评论引流来的
    attributed = db.scalar(select(func.count()).select_from(Lead)
                           .where(Lead.attribution_code != "")) or 0
    leads = db.scalar(select(func.count()).select_from(Lead)) or 0
    won = db.scalar(select(func.count()).select_from(Lead).where(Lead.status == "won")) or 0
    return {
        "stages": [
            {"name": "发布笔记", "count": published},
            {"name": "引流线索(带暗号)", "count": attributed},
            {"name": "全部线索", "count": leads},
            {"name": "成交", "count": won},
        ],
        "conversion": {
            "线索成交率": round(won / leads, 3) if leads else 0,
        },
    }
