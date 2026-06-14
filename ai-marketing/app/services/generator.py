"""③ 内容生成引擎：四层流水线。对应 03-generator.md。

逻辑重构 → 事实核查（规则版：数值类声明标待人工核实）→ 风格注入 → 合规预检。
LLM 输出解析失败自动重试一次，仍失败则拒绝入库（不存空稿）。
"""
from __future__ import annotations

import json
import re

from sqlalchemy.orm import Session

from ..models import Account, ContentEvent, Draft, Topic
from . import compliance
from .llm_router import get_router

# 数值类事实声明（尺寸/价格/时长/比例）→ 全部标"待人工核实"进审核高亮
_CLAIM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:米|cm|公分|mm|元|块|年|个月|天|%|折)")


def extract_claims(text: str) -> list[str]:
    """规则版事实核查：抽取数值类声明。"""
    return _CLAIM_RE.findall(text)


def generate_draft(db: Session, topic_id: int, account_id: int | None = None) -> Draft:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise ValueError("选题不存在")
    account = db.get(Account, account_id) if account_id else None
    router = get_router()

    persona_hint = ""
    if account and account.voice_persona:
        vp = account.voice_persona
        persona_hint = (
            f"\n账号性格：口头禅{vp.get('catchphrases', [])}，"
            f"偏好emoji{vp.get('emoji_preference', [])}，"
            f"第一人称比例{vp.get('first_person_ratio', 0.6)}。"
        )

    # 第①层 逻辑重构 + 第③层 风格注入（mock 合并产出结构化叙事单元）
    prompt = (
        f"为家具品牌写一篇小红书笔记。选题：{topic.title}。"
        f"关键词：{topic.keywords}。{persona_hint}"
        "用闺蜜聊天口吻，含真实使用细节，输出 JSON：title/narrative_units/tags。"
    )
    # 解析失败重试一次（质量回退的最小实现）；仍失败拒绝入库
    units: list = []
    data: dict = {}
    for _attempt in range(2):
        raw = router.complete("generator.style_injection", prompt, json_mode=True)
        data = _safe_json(raw)
        units = data.get("narrative_units") or []
        if units:
            break
    if not units:
        raise ValueError("LLM 输出无法解析为结构化草稿（已重试 1 次），未入库")

    body = "\n".join(u.get("content", "") for u in units)
    title = (data.get("title") or topic.title)[:20]
    tags = data.get("tags") or topic.keywords

    # 第②层 事实核查（规则版）：数值类声明全部标待人工核实
    full_text = f"{title}\n{body}"
    claims = extract_claims(full_text)
    fact = {"passed": [], "pending_human": claims}

    # 第④层 合规预检
    hits = compliance.scan_banned(full_text)
    ai_like = compliance.ai_likeness_score(full_text)
    density = compliance.info_density(full_text)

    # 必改3节点（首图文字 / 第二段营销话术 / 个人化细节密度）
    personal = sum(full_text.count(w) for w in ("我家", "我", "上次", "之前"))
    must_fix = {
        "cover_text_density": 0.4,
        "para2_marketing": "需软化" if hits["A"] or hits["B"] else "ok",
        "personal_anchor_count": personal,
    }

    draft = Draft(
        topic_id=topic.id,
        account_id=account.id if account else None,
        title=title,
        body=body,
        tags=tags,
        narrative_units=units,
        fact_check=fact,
        compliance=hits,
        ai_likeness=ai_like,
        info_density=density,
        must_fix=must_fix,
        lineage={
            "prompt_template": "tpl_mvp_v1",
            "style_dna_id": topic.recommended_style,
            "generated_by_model": router.provider,
            "account_id": account.id if account else None,
        },
        status="drafted",
    )
    db.add(draft)
    db.flush()
    db.add(ContentEvent(content_id=draft.id, event_type="draft_generated",
                        payload={"ai_likeness": ai_like, "info_density": density,
                                 "claims_pending": len(claims)}))
    db.commit()
    return draft


def generate_title_variants(db: Session, draft_id: int) -> list[str]:
    """#9 标题 A/B：用钩子库为草稿产出多个标题候选，发不同号测点击。"""
    import random as _r

    from ..config import TITLE_HOOKS
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise ValueError("草稿不存在")
    topic = db.get(Topic, draft.topic_id)
    kw = (topic.keywords[0] if topic and topic.keywords else "实木家具")
    variants = list({h.format(kw=kw)[:20] for h in _r.sample(TITLE_HOOKS, k=min(4, len(TITLE_HOOKS)))})
    draft.title_variants = variants
    db.commit()
    return variants


def generate_video_script(db: Session, topic_id: int, account_id: int | None = None) -> Draft:
    """#11 口播脚本/分镜：产出 content_type=video 的草稿，进同一审核流程。"""
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise ValueError("选题不存在")
    router = get_router()
    kw = topic.keywords[0] if topic.keywords else topic.title
    script = router.complete("generator.video_script", f"为家具写小红书口播脚本：{kw}")
    hits = compliance.scan_banned(script)
    draft = Draft(
        topic_id=topic.id, account_id=account_id, title=("【视频】" + topic.title)[:20],
        body=script, tags=topic.keywords, narrative_units=[],
        content_type="video", fact_check={"passed": [], "pending_human": extract_claims(script)},
        compliance=hits, ai_likeness=compliance.ai_likeness_score(script),
        info_density=compliance.info_density(script),
        must_fix={"cover_text_density": 0.0, "para2_marketing": "ok",
                  "personal_anchor_count": script.count("我")},
        lineage={"prompt_template": "video_v1", "generated_by_model": router.provider},
        status="drafted",
    )
    db.add(draft)
    db.flush()
    db.add(ContentEvent(content_id=draft.id, event_type="draft_generated",
                        payload={"content_type": "video"}))
    db.commit()
    return draft


def _safe_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
