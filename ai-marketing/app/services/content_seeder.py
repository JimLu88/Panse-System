"""内容预热：一键批量生成各品类选题 + 草稿，让系统首次打开不空。

复用 ① 选题引擎 + ③ 生成引擎，对每个产品分类生成若干选题并各出一篇草稿，
草稿进③审核工位，运营开箱即可审。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import PRODUCT_KEYWORDS
from ..models import Account
from . import generator, topic_engine


def seed_batch(db: Session, per_category: int = 1) -> dict:
    """对每个品类生成 per_category 个选题，每个选题用一个正式期账号出一篇草稿。

    返回 {topics, drafts, skipped}。账号轮流分配，保证内容带不同性格档案。
    """
    actives = list(db.scalars(
        select(Account).where(Account.stage == "active")
    ))
    if not actives:
        actives = list(db.scalars(select(Account)))

    topic_count = 0
    draft_count = 0
    ai = 0
    for category in PRODUCT_KEYWORDS:
        topics = topic_engine.generate_topics(db, category, per_category)
        topic_count += len(topics)
        for t in topics:
            account = actives[ai % len(actives)] if actives else None
            ai += 1
            try:
                generator.generate_draft(db, t.id, account.id if account else None)
                draft_count += 1
            except ValueError:
                pass  # LLM 解析失败的稿不入库，跳过
    return {"topics": topic_count, "drafts": draft_count}
