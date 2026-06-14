"""⑧ 评论引流站。对应 08-comment-engine.md。

发现上升期笔记 → 匹配产品线 → AI 草拟评论 → 人工点发（永不全自动）。
频控硬约束：单号每日上限 DAILY_LIMIT、同一笔记矩阵内最多 1 个号评论。
"""
from __future__ import annotations

import datetime as dt
import random

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import PRODUCT_KEYWORDS
from ..models import Account, CommentOpportunity
from . import compliance, data_source
from .llm_router import get_router

DAILY_LIMIT = 5  # 单号每日评论上限（设计稿待校准初值）


def scan_opportunities(db: Session, count: int = 5) -> list[CommentOpportunity]:
    """扫描上升期笔记，匹配产品线，草拟评论，挑执行号。已有机会的笔记跳过（去重）。

    笔记来源走 data_source（接了爬虫用真实上升笔记，否则内置演示数据）。
    """
    router = get_router()
    # 只用正式期 + 绿牌号承接评论（与养号引擎联动）
    eligible = list(db.scalars(
        select(Account).where(Account.stage == "active", Account.health_flag == "green")
    ))
    # 去重：同一笔记已有 pending/posted 机会则不再生成
    seen_titles = set(db.scalars(
        select(CommentOpportunity.note_title).where(
            CommentOpportunity.status.in_(["pending", "posted"]))
    ))

    notes, _src = data_source.fetch_rising_notes()
    out: list[CommentOpportunity] = []
    for note in random.sample(notes, k=min(count, len(notes))):
        title, kind = note["title"], note["kind"]
        if title in seen_titles:
            continue
        category, score = _match_category(title)
        growth = note["growth"] if note["growth"] is not None else round(random.uniform(0.3, 0.9), 2)
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
            note_url=note.get("url", ""),
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
    """关键词精确匹配产品线；泛家居词兜底。"""
    for cat, kws in PRODUCT_KEYWORDS.items():
        for kw in (cat, *kws):
            if kw in title:
                return cat, 0.6
    if any(w in title for w in ("装修", "家居", "客厅", "餐厅", "新房", "改造")):
        return "餐桌", 0.4
    return "", 0.0


def _posted_today(db: Session, account_id: int) -> int:
    start = dt.datetime.now(dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count()).select_from(CommentOpportunity).where(
            CommentOpportunity.posted_by_account_id == account_id,
            CommentOpportunity.posted_at >= start.replace(tzinfo=None),
        )
    ) or 0


def mark_posted(db: Session, opp_id: int, account_id: int | None = None) -> CommentOpportunity:
    """人工点发后回写。强制：单号每日限额 + 同笔记矩阵内互斥。"""
    opp = db.get(CommentOpportunity, opp_id)
    if opp is None:
        raise ValueError("机会不存在")
    if opp.status == "posted":
        raise ValueError("该机会已发出")
    if opp.compliance.get("S"):
        raise ValueError("S级敏感词，不可发")

    acc_id = account_id or opp.suggested_account_id
    if acc_id is None:
        raise ValueError("无执行账号（无正式期绿牌号可用）")

    # 同一笔记矩阵内最多 1 个号出现（设计稿硬规则）
    dup = db.scalar(
        select(func.count()).select_from(CommentOpportunity).where(
            CommentOpportunity.note_title == opp.note_title,
            CommentOpportunity.status == "posted",
        )
    ) or 0
    if dup:
        raise ValueError("该笔记下矩阵已有账号评论过，跳过（反共振）")

    # 单号每日上限
    if _posted_today(db, acc_id) >= DAILY_LIMIT:
        raise ValueError(f"账号 #{acc_id} 今日评论已达上限 {DAILY_LIMIT} 条")

    opp.status = "posted"
    opp.posted_at = dt.datetime.now(dt.timezone.utc)
    opp.posted_by_account_id = acc_id
    db.commit()
    return opp


def skip(db: Session, opp_id: int) -> CommentOpportunity:
    opp = db.get(CommentOpportunity, opp_id)
    if opp is None:
        raise ValueError("机会不存在")
    opp.status = "skipped"
    db.commit()
    return opp
