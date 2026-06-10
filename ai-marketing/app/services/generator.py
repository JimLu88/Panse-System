"""③ 内容生成引擎：四层流水线。对应 03-generator.md。

逻辑重构 → 事实核查 → 风格注入 → 合规预检，产出含必改3节点的草稿。
账号性格档案注入 prompt（矩阵号同质化的根本解）。
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from ..models import Account, ContentEvent, Draft, Topic
from . import compliance
from .llm_router import get_router


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
    raw = router.complete("generator.style_injection", prompt, json_mode=True)
    data = _safe_json(raw)
    units = data.get("narrative_units", [])
    body = "\n".join(u.get("content", "") for u in units)
    title = data.get("title", topic.title)[:20]
    tags = data.get("tags", topic.keywords)

    # 第②层 事实核查（mock：家具内容多为主观体验，标注待人工核实数值类声明）
    fact = {"passed": [], "pending_human": []}

    # 第④层 合规预检
    full_text = f"{title}\n{body}"
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
                        payload={"ai_likeness": ai_like, "info_density": density}))
    db.commit()
    return draft


def _safe_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
