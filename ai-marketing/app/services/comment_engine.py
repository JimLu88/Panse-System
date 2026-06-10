"""⑧ 评论引流站。对应 08-comment-engine.md。

发现上升期笔记 → 匹配产品线 → AI 草拟评论 → 人工点发（永不全自动）。
家具场景优先装修日记类笔记（用户处于决策期）。
"""
from __future__ import annotations

import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import PRODUCT_KEYWORDS
from ..models import Account, CommentOpportunity
from . import compliance
from .llm_router import get_router

# 模拟上升期笔记流（真实场景来自 ① 热点雷达 / 挂载项 A 爬虫）
_MOCK_NOTES = [
    ("新房装修日记|餐厅终于搞定了", "decor_diary"),
    ("求推荐！小户型餐桌怎么选", "decor_diary"),
    ("晒晒我的新中式客厅", "decor_diary"),
    ("分享几个家居好物", "general"),
    ("租房改造|花了2000块", "decor_diary"),
]

DAILY_LIMIT = 5  # 单号每日评论上限（设计稿待校准初值）


def scan_opportunities(db: Session, count: int = 5) -> list[CommentOpportunity]:
    """扫描上升期笔记，匹配产品线，草拟评论，挑执行号。"""
    router = get_router()
    # 只用正式期 + 绿牌号承接评论（与养号引擎联动）
    eligible = list(db.scalars(
        select(Account).where(Account.stage == "active", Account.health_flag == "green")
    ))

    out: list[CommentOpportunity] = []
    for title, kind in random.sample(_MOCK_NOTES, k=min(count, len(_MOCK_NOTES))):
        category, score = _match_category(title)
        growth = round(random.uniform(0.3, 0.9), 2)
        # 装修日记类加权（决策期用户）
        if kind == "decor_diary":
            score = min(score + 0.2, 1.0)

        comment_kind = "seed" if random.random() < 0.7 else "guide"  # 种水:引导 ≈ 7:3
        draft = router.complete("comment.draft", f"在『{title}』下写一条{comment_kind}评论")
        if comment_kind == "guide":
            draft += "（可以搜「畔色家居」看看～）"

        hits = compliance.scan_banned(draft)
        acc = random.choice(eligible).id if eligible else None

        opp = CommentOpportunity(
            note_title=title,
            note_kind=kind,
            growth_rate=growth,
            match_category=category,
            match_score=round(score, 2),
            comment_kind=comment_kind,
            draft_comment=draft,
            compliance=hits,
            suggested_account_id=acc,
            status="pending",
        )
        db.add(opp)
        out.append(opp)
    db.commit()
    # 按匹配分 × 增速排序，优先级高的在前
    out.sort(key=lambda o: o.match_score * o.growth_rate, reverse=True)
    return out


def _match_category(title: str) -> tuple[str, float]:
    best, best_score = "", 0.0
    for cat, kws in PRODUCT_KEYWORDS.items():
        for kw in [cat, *kws]:
            if kw in title or any(c in title for c in cat):
                if 0.5 > best_score:
                    best, best_score = cat, 0.5
    # 泛家居关键词兜底
    if not best and any(w in title for w in ("装修", "家居", "客厅", "餐厅", "新房", "改造")):
        best, best_score = "餐桌", 0.4
    return best, best_score


def mark_posted(db: Session, opp_id: int) -> CommentOpportunity:
    """人工点发后回写。"""
    opp = db.get(CommentOpportunity, opp_id)
    if opp is None:
        raise ValueError("机会不存在")
    if opp.compliance.get("S"):
        raise ValueError("S级敏感词，不可发")
    opp.status = "posted"
    db.commit()
    return opp


def skip(db: Session, opp_id: int) -> CommentOpportunity:
    opp = db.get(CommentOpportunity, opp_id)
    if opp is None:
        raise ValueError("机会不存在")
    opp.status = "skipped"
    db.commit()
    return opp
