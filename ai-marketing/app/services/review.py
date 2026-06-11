"""③.5 人工审核工位。对应 03.5-review-station.md。

三张体检报告 + 必改3节点 + 左高亮右改写候选。目标单篇≤30秒。
阻塞校验在 service 层强制执行（approve 与 UI 同一套规则，无后门）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import ContentEvent, Draft
from .llm_router import get_router

AI_LIKENESS_BLOCK = 70  # ≥70 阻塞


def _blockers(draft: Draft) -> list[str]:
    """过审阻塞项：UI 展示与 approve 强制校验共用同一来源。"""
    blockers = []
    hits = draft.compliance or {}
    if hits.get("S"):
        blockers.append(f"S级敏感词: {hits['S']}（不可发）")
    if draft.ai_likeness >= AI_LIKENESS_BLOCK:
        blockers.append(f"AI感评分 {draft.ai_likeness} ≥ {AI_LIKENESS_BLOCK}（过审阻塞）")
    return blockers


def review_report(db: Session, draft_id: int) -> dict:
    """生成审核工位数据：三张体检报告 + 必改3节点 + 改写候选。"""
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise ValueError("草稿不存在")

    hits = draft.compliance or {}
    blockers = _blockers(draft)

    return {
        "draft_id": draft.id,
        "title": draft.title,
        "body": draft.body,
        "tags": draft.tags,
        "reports": {
            "ai_likeness": {"score": draft.ai_likeness, "block": draft.ai_likeness >= AI_LIKENESS_BLOCK},
            "banned_words": hits,
            "info_density": draft.info_density,
        },
        "fact_check": draft.fact_check,
        "must_fix": draft.must_fix,
        "highlights": _highlights(draft),
        "rewrite_candidates": _rewrites(draft) if (hits.get("A") or hits.get("B")) else [],
        "blockers": blockers,
        "can_approve": not blockers,
        "status": draft.status,
    }


def _highlights(draft: Draft) -> list[dict]:
    """左侧问题高亮：标出 A/B 级命中词。"""
    spans = []
    hits = draft.compliance or {}
    for level in ("A", "B"):
        for w in hits.get(level, []):
            spans.append({"word": w, "level": level})
    return spans


def _rewrites(draft: Draft) -> list[str]:
    """右侧三个改写候选（软化营销话术）。"""
    router = get_router()
    if router.provider == "mock":
        return [
            draft.body.replace("最", "比较").replace("强烈推荐", "挺值得看看"),
            "我自己用下来感觉还不错，仅供参考～",
            "具体好不好用因人而异，分享下我的真实体验。",
        ]
    out = router.complete(
        "generator.style_injection",
        f"把下面这段软化、去掉绝对化营销词，给3个版本：\n{draft.body}",
    )
    return [out]


def approve(db: Session, draft_id: int, note: str = "") -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise ValueError("草稿不存在")
    blockers = _blockers(draft)
    if blockers:
        raise ValueError("存在过审阻塞项：" + "；".join(blockers))
    draft.status = "approved"
    draft.review_note = note
    db.add(ContentEvent(content_id=draft.id, event_type="human_approved", payload={"note": note}))
    db.commit()
    return draft


def reject(db: Session, draft_id: int, reason: str) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise ValueError("草稿不存在")
    draft.status = "rejected"
    draft.review_note = reason
    db.add(ContentEvent(content_id=draft.id, event_type="human_rejected", payload={"reason": reason}))
    db.commit()
    return draft
