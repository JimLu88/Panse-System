"""自有笔记评论管理（#17/#18/#20）+ 品牌舆情（#19）。

抓我们已发布笔记下的评论 → 意图分类 → 套话术给回复草稿 → 人工发 / 一键转线索。
对标 XiaoFeiShu 精准评论+舆情、MediaCrawler 评论(含二级)采集。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import FAQ_SCRIPTS
from ..models import BrandMention, Draft, InboundComment, PublishEvent
from . import data_source, lead_inbox

# 意图关键词 → 分类
_INTENT_RULES = [
    ("price", ["多少钱", "价格", "贵", "优惠", "几位数", "预算"]),
    ("size", ["尺寸", "多大", "米", "厘米", "cm", "几人", "坐得下", "户型"]),
    ("material", ["材质", "什么木", "榉木", "橡木", "白蜡木", "岩板", "实木", "甲醛", "环保"]),
    ("complaint", ["色差", "踩雷", "差评", "退", "坏", "裂", "翻车", "不行"]),
    ("praise", ["好看", "喜欢", "质感", "好评", "满意", "种草", "真好"]),
]
_FAQ_BY_KEY = {f["key"]: f for f in FAQ_SCRIPTS}
# 意图 → 话术 key
_INTENT_TO_FAQ = {"price": "价格", "size": "尺寸", "material": "材质",
                  "complaint": "售后", "praise": "返图"}


def classify_intent(text: str) -> str:
    for intent, kws in _INTENT_RULES:
        if any(k in text for k in kws):
            return intent
    return "other"


def _suggest_reply(intent: str) -> str:
    faq = _FAQ_BY_KEY.get(_INTENT_TO_FAQ.get(intent, ""))
    return faq["text"] if faq else "您好～感谢关注，有任何问题都可以问我哦😊"


def fetch_for_published(db: Session) -> dict:
    """给所有已发布笔记抓评论入库（含意图分类 + 回复草稿）。"""
    success = db.scalars(select(PublishEvent).where(PublishEvent.result == "success")).all()
    added = 0
    source = "mock"
    for ev in success:
        comments, source = data_source.fetch_inbound_comments(f"{ev.content_id}_{ev.account_id}")
        existing = set(db.scalars(
            select(InboundComment.text).where(InboundComment.content_id == ev.content_id,
                                              InboundComment.account_id == ev.account_id)
        ))
        for text in comments:
            if text in existing:
                continue
            intent = classify_intent(text)
            db.add(InboundComment(
                content_id=ev.content_id, account_id=ev.account_id, author="网友",
                text=text, intent=intent, reply_draft=_suggest_reply(intent), status="new",
            ))
            added += 1
    db.commit()
    return {"added": added, "source": source}


def list_inbound(db: Session, status: str | None = None) -> list[dict]:
    stmt = select(InboundComment, Draft.title).join(
        Draft, Draft.id == InboundComment.content_id, isouter=True
    ).order_by(InboundComment.id.desc())
    if status:
        stmt = stmt.where(InboundComment.status == status)
    out = []
    for c, title in db.execute(stmt).all():
        out.append({"id": c.id, "note_title": title or f"稿#{c.content_id}",
                    "account_id": c.account_id, "author": c.author, "text": c.text,
                    "intent": c.intent, "reply_draft": c.reply_draft,
                    "status": c.status, "lead_id": c.lead_id, "parent_id": c.parent_id})
    return out


def mark_replied(db: Session, cid: int) -> InboundComment:
    c = db.get(InboundComment, cid)
    if c is None:
        raise ValueError("评论不存在")
    c.status = "replied"
    db.commit()
    return c


def to_lead(db: Session, cid: int) -> dict:
    """问价/问尺寸等高意向评论一键转线索（接 lead_inbox）。"""
    c = db.get(InboundComment, cid)
    if c is None:
        raise ValueError("评论不存在")
    lead = lead_inbox.create_lead(
        db, source_type="comment", question=c.text,
        interest_category={"price": "", "size": "", "material": ""}.get(c.intent, ""),
        source_account_id=c.account_id, source_content_id=c.content_id,
        attribution_code="评论引流",
    )
    c.status = "converted"
    c.lead_id = lead.id
    db.commit()
    return {"comment_id": c.id, "lead_id": lead.id}


def add_reply_thread(db: Session, parent_id: int, text: str) -> InboundComment:
    """#20 楼中楼：记录用户对我们评论的再回复，纳入追踪。"""
    parent = db.get(InboundComment, parent_id)
    if parent is None:
        raise ValueError("父评论不存在")
    intent = classify_intent(text)
    child = InboundComment(
        content_id=parent.content_id, account_id=parent.account_id, author="网友",
        text=text, intent=intent, reply_draft=_suggest_reply(intent),
        status="new", parent_id=parent_id,
    )
    db.add(child)
    db.commit()
    return child


def inbound_summary(db: Session) -> dict:
    by_intent = dict(db.execute(
        select(InboundComment.intent, func.count()).group_by(InboundComment.intent)
    ).all())
    pending = db.scalar(select(func.count()).select_from(InboundComment)
                        .where(InboundComment.status == "new")) or 0
    return {"by_intent": by_intent, "pending": pending}


# ---------------- 舆情 #19 ----------------

def scan_mentions(db: Session, brand="畔色", competitors=None) -> dict:
    competitors = competitors or ["竞品"]
    mentions, source = data_source.fetch_mentions([brand, *competitors])
    existing = set(db.scalars(select(BrandMention.note_title)))
    added = 0
    for m in mentions:
        if m.get("title") in existing:
            continue
        db.add(BrandMention(
            platform=m.get("platform", "xhs"), mention_type=m.get("type", "brand"),
            keyword=brand if m.get("type") == "brand" else competitors[0],
            note_title=m.get("title", ""), snippet=m.get("snippet", ""),
            sentiment=m.get("sentiment", "neutral"), url=m.get("url", ""), status="new",
        ))
        added += 1
    db.commit()
    return {"added": added, "source": source}


def list_mentions(db: Session) -> list[dict]:
    rows = db.scalars(select(BrandMention).order_by(BrandMention.id.desc()))
    return [{"id": m.id, "type": m.mention_type, "title": m.note_title,
             "snippet": m.snippet, "sentiment": m.sentiment, "status": m.status,
             "suggest": _mention_action(m)} for m in rows]


def _mention_action(m: BrandMention) -> str:
    if m.mention_type == "competitor":
        return "竞品笔记，可去评论区做引导型评论抢客"
    if m.sentiment == "neg":
        return "负面提及，优先私信/评论安抚处理"
    if m.sentiment == "pos":
        return "正面提及，去感谢+互动放大"
    return "中性咨询，可去解答引流"


def handle_mention(db: Session, mid: int) -> BrandMention:
    m = db.get(BrandMention, mid)
    if m is None:
        raise ValueError("舆情不存在")
    m.status = "handled"
    db.commit()
    return m
