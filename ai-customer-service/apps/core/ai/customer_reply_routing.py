"""
路由式客服工作流：记忆上下文 → 意图路由 → 场景 A/B/C 分支。

场景 B 走「重写 → 话术库相似度门槛 → 仅在有命中时允许 LLM 组句」。
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import Any, Literal

import sqlite3

from apps.core.ai.input_quality_gate import (
    check_buyer_input,
    is_metadata_noise,
    sanitize_context_for_rewrite,
)
from apps.core.ai.kb_retrieval_pipeline import diagnose_kb_miss, kb_retrieval_or_legacy_fallback
from apps.core.strategy.inquiry_auto_reply import resolve_inquiry_auto_reply
from apps.core.intent.classify import classify_buyer_text


def _extract_tone_hint(answer: str) -> str:
    """从话术参考答案里提取句末语气词，供语气锁定指令使用。"""
    if not answer:
        return "（无）"
    # 取最后一句的末尾 8 个字
    tail = re.sub(r"\s+", "", answer)[-8:]
    # 找常见语气词
    particles = re.findall(r"[呢哦啊吧嗯哈呀噢的地得~～！！？?。，,；;：:]+", tail)
    hint = "".join(particles) if particles else tail
    return hint or "（无）"
from apps.core.ai.llm_client import generate_reply_segments, litellm_completion_text
from apps.core.ai.rag_kb import format_rag_block
from apps.core.configs.base_settings import BaseSettings
from apps.core.logging.image_library import search_images_for_question
from apps.core.logging.panse_hitl_jsonl import append_panse_hitl_record
from apps.core.runtime_paths import configs_dir


SOFT_OUTBOUND_REVIEW_TERMS = (
    "改尺寸",
    "定做",
    "定制",
    "退换",
    "色差",
    "瑕疵",
    "延期",
    "货期",
    "加急",
    "材质",
    "做工",
)

HIGH_RISK_KEYWORDS = (
    "改尺寸",
    "退货",
    "退款",
    "换货",
    "便宜点",
    "少点",
    "砍价",
    "加急",
    "赶工",
    "投诉",
    "差评",
    "工商局",
    "律师函",
    "违约",
    "赔偿",
)


def detect_high_risk_keywords(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    for k in HIGH_RISK_KEYWORDS:
        if k in t:
            return k
    return None


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        return m.group(1).strip()
    return s


def _parse_router_json(raw: str) -> dict[str, Any]:
    s = _strip_json_fence(raw)
    obj = json.loads(s)
    if not isinstance(obj, dict):
        raise ValueError("router 非 JSON 对象")
    return obj


def _parse_rewrite_json(raw: str) -> str:
    s = _strip_json_fence(raw)
    obj = json.loads(s)
    if not isinstance(obj, dict):
        raise ValueError("rewrite 非 JSON 对象")
    rw = obj.get("rewritten")
    if isinstance(rw, str) and rw.strip():
        return rw.strip()
    raise ValueError("rewrite 缺少 rewritten")


def route_intent_llm(
    *,
    settings: BaseSettings,
    context_block: str,
    buyer_text: str,
) -> tuple[Literal["A", "B", "C"], str | None, str]:
    """
    返回 (scene, chit_chat_id, reason)。
    chit_chat_id 仅 scene=A 时有意义：thanks|ack|greeting|farewell|polite_short|emoji|other
    """
    model = (settings.model_front_desk or "").strip()
    if not model:
        raise RuntimeError("未配置前台模型 model_front_desk")

    system = (
        "你是电商客服「意图路由器」。只能根据对话上文与客户最新消息做分类，"
        "禁止编造事实、禁止直接回复客户。\n"
        "只输出一个 JSON 对象（不要 Markdown 围栏外的文字），字段：\n"
        '{"scene":"A"|"B"|"C","chit_chat_id":字符串或null,"reason":"≤24字"}\n'
        "定义：\n"
        "A=纯社交/致谢/收到确认/告别/在吗/无具体业务信息诉求的短安抚；\n"
        "B=产品、材质、尺寸、价格、怎么卖、对不对、发图、对比、交期等业务信息；\n"
        "C=改单、退换货、议价、加急、投诉威胁等需人工接管的高风险。\n"
        "chit_chat_id 仅在 scene=A 时填写，取值必须是之一："
        "thanks,ack,greeting,farewell,polite_short,emoji,other；否则 null。"
    )
    user = (
        "【对话上文】\n"
        + context_block
        + "\n\n【客户最新消息】\n"
        + buyer_text.strip()
    )
    raw = litellm_completion_text(
        settings=settings,
        model=model,
        system=system,
        user=user,
        max_tokens=256,
        temperature=0.0,
    )
    obj = _parse_router_json(raw)
    scene = str(obj.get("scene") or "B").upper()
    if scene not in ("A", "B", "C"):
        scene = "B"
    cid = obj.get("chit_chat_id")
    chit_id = str(cid).strip().lower() if isinstance(cid, str) and cid.strip() else None
    if scene != "A":
        chit_id = None
    reason = str(obj.get("reason") or "")[:48]
    return scene, chit_id, reason  # type: ignore[return-value]


def rewrite_query_only_llm(
    *,
    settings: BaseSettings,
    context_block: str,
    buyer_text: str,
) -> str:
    """
    严格「只重写、不回答」：模型输出 JSON {"rewritten":"..."}。
    """
    model = (settings.model_front_desk or "").strip()
    if not model:
        raise RuntimeError("未配置前台模型 model_front_desk")

    system = (
        "你是「查询重写器」。任务：结合对话上文，把客户短句改写成一条完整、可检索的标准业务问句，"
        "用于在固定话术表里做关键词/相似度匹配。\n"
        "硬性禁止：回答客户、推销、承诺价格或交期、输出多条、输出 Markdown、输出除 JSON 外的任何字符。\n"
        "只输出 JSON：{\"rewritten\":\"...\"}\n"
        "rewritten 必须是一句完整中文问句或陈述，可包含上文指代的具体对象名；"
        "若无法推断则把原句去多余空格后写入 rewritten。"
    )
    ctx = sanitize_context_for_rewrite(context_block)
    user = "【对话上文】\n" + ctx + "\n\n【客户短句】\n" + buyer_text.strip()
    raw = litellm_completion_text(
        settings=settings,
        model=model,
        system=system,
        user=user,
        max_tokens=320,
        temperature=0.0,
    )
    return _parse_rewrite_json(raw)


def load_chit_chat_replies(chit_chat_id: str | None) -> list[str]:
    path = configs_dir() / "panse_chit_chat.json"
    key = (chit_chat_id or "other").strip().lower() or "other"
    if not path.is_file():
        return ["嗯嗯～"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cats = (data.get("categories") or {}) if isinstance(data, dict) else {}
        block = cats.get(key) or cats.get("other") or {}
        replies = block.get("replies") if isinstance(block, dict) else None
        if isinstance(replies, list) and replies:
            out = [str(x).strip() for x in replies if str(x).strip()]
            return out or ["嗯嗯～"]
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return ["嗯嗯～"]


@dataclass(slots=True)
class RoutedReplyPlan:
    """供 Brain 入队发送或转 Jim。"""

    takeover: bool
    takeover_reason: str
    segments: list[str]
    intent_label: str
    kb_node: str
    strict_rag_context: str
    confidence_for_policy: float
    hitl_context: str = ""
    hitl_query: str = ""
    hitl_pos: tuple[str, ...] = ()
    hitl_neg: tuple[str, ...] = ()
    image_send_items: tuple[tuple[str, str], ...] = ()
    sensitivity_tier: str = "none"
    kb_diagnosis_lines: tuple[str, ...] = ()


STRICT_RAG_MIN_SCORE = 0.70


def build_routed_reply_plan(
    *,
    settings: BaseSettings,
    conn: sqlite3.Connection,
    brand_id: str,
    shop_id: str,
    buyer_text: str,
    context_block: str,
    few_shot: str,
    phrase_blacklist: str,
    product_block: str,
    campaign_block: str,
    gallery_block: str,
    discount_round_hint: str,
    closing_etiquette: str,
    extra_instructions: str,
    discount_round_index: int = 1,
) -> RoutedReplyPlan:
    """
    在已排除广告噪声、人工 Hold 等前置条件后调用。
    """
    risk_kw = detect_high_risk_keywords(buyer_text)
    if risk_kw:
        return RoutedReplyPlan(
            takeover=True,
            takeover_reason=f"高风险词命中：{risk_kw}",
            segments=[],
            intent_label="场景C:高风险",
            kb_node="",
            strict_rag_context="（未检索）",
            confidence_for_policy=0.0,
            image_send_items=(),
        )

    try:
        scene, chit_id, r_reason = route_intent_llm(
            settings=settings, context_block=context_block, buyer_text=buyer_text
        )
    except Exception:
        scene, chit_id, r_reason = "B", None, "路由解析失败，降级严格检索"

    if scene == "C":
        return RoutedReplyPlan(
            takeover=True,
            takeover_reason=f"模型路由为高风险(C)：{r_reason}",
            segments=[],
            intent_label="场景C:模型路由",
            kb_node="",
            strict_rag_context="（未检索）",
            confidence_for_policy=0.0,
            image_send_items=(),
        )

    if scene == "A":
        replies = load_chit_chat_replies(chit_id)
        seg = random.choice(replies)
        label = f"场景A:闲聊({chit_id or 'other'})"
        return RoutedReplyPlan(
            takeover=False,
            takeover_reason="",
            segments=[seg],
            intent_label=label,
            kb_node=f"话术池:{chit_id or 'other'}",
            strict_rag_context="（场景A未走知识库）",
            confidence_for_policy=0.95,
            image_send_items=(),
        )

    # --- 场景 B：输入门控 → 重写 → 相似度门槛 → LLM 仅基于命中条 ---
    gate = check_buyer_input(buyer_text)
    if gate.action == "quick_reply":
        return RoutedReplyPlan(
            takeover=False,
            takeover_reason="",
            segments=[gate.reply],
            intent_label=f"场景B:输入门控({gate.rule_name})",
            kb_node="input_quality_gate",
            strict_rag_context="（门控短路，未检索）",
            confidence_for_policy=0.88,
            image_send_items=(),
        )
    if gate.action == "discard_log":
        return RoutedReplyPlan(
            takeover=False,
            takeover_reason="",
            segments=[],
            intent_label=f"场景B:丢弃噪声({gate.rule_name})",
            kb_node="input_quality_gate",
            strict_rag_context="（噪声丢弃，未检索）",
            confidence_for_policy=0.0,
            image_send_items=(),
        )

    safe_ctx = sanitize_context_for_rewrite(context_block)
    try:
        rewritten = rewrite_query_only_llm(
            settings=settings, context_block=safe_ctx, buyer_text=buyer_text
        )
    except Exception:
        rewritten = buyer_text.strip()
    if is_metadata_noise(rewritten):
        rewritten = buyer_text.strip()

    hit, kb_src = kb_retrieval_or_legacy_fallback(
        settings=settings,
        conn=conn,
        brand_id=brand_id,
        shop_id=shop_id,
        rewritten_query=rewritten,
        context_block=context_block,
        buyer_text=buyer_text,
        legacy_min_score=STRICT_RAG_MIN_SCORE,
    )
    if hit is None:
        # 分级降级 Level 1：询价/拍下关键词 → 通用模板，避免直接 strategy_takeover
        intent_kw = classify_buyer_text(buyer_text)
        fallback_seg = resolve_inquiry_auto_reply(intent_kw)
        if fallback_seg and (intent_kw.price_quote or intent_kw.order_placed):
            return RoutedReplyPlan(
                takeover=False,
                takeover_reason="",
                segments=[fallback_seg],
                intent_label="场景B:意图模板降级",
                kb_node="inquiry_auto_reply",
                strict_rag_context="（未命中 KB，意图模板）",
                confidence_for_policy=0.72,
                image_send_items=(),
            )

        kb_diag = tuple(
            diagnose_kb_miss(
                settings=settings,
                conn=conn,
                brand_id=brand_id,
                shop_id=shop_id,
                rewritten_query=rewritten,
                legacy_min_score=STRICT_RAG_MIN_SCORE,
            )
        )
        try:
            from apps.core.logging.pending_qa import append_pending_qa

            append_pending_qa(
                query=rewritten,
                noise=is_metadata_noise(rewritten) or is_metadata_noise(buyer_text),
                reason="kb_miss",
                session_id="",
            )
        except Exception:
            pass
        return RoutedReplyPlan(
            takeover=True,
            takeover_reason=(
                "话术库未命中（向量/满血RAG/字符串相似度均未达门槛），已转人工兜底；"
                f"重写问句：{rewritten[:120]}"
            ),
            segments=[],
            intent_label="场景B:话术库未命中",
            kb_node="",
            strict_rag_context="（无合格命中，禁止编造）",
            confidence_for_policy=0.0,
            hitl_context=context_block,
            hitl_query=rewritten,
            hitl_pos=(),
            hitl_neg=(),
            image_send_items=(),
            kb_diagnosis_lines=kb_diag,
        )

    question, answer = hit.question, hit.answer
    score = float(hit.rerank_score)
    if 0.0 <= score <= 1.0:
        score_txt = f"{score:.0%}"
    else:
        score_txt = f"{score:.3f}"
    kb_node = f"[rerank={score_txt}|{kb_src}] {question[:80]}"
    snips = [(question, answer)]
    rag = format_rag_block(snips)

    try:
        append_panse_hitl_record(
            query=rewritten[:2000],
            pos=[answer],
            neg=list(hit.alternatives)[:8],
            meta={
                "event": "rag_hit",
                "kb_source": kb_src,
                "rerank_score": hit.rerank_score,
                "rrf_score": hit.rrf_score,
                "kb_id": hit.kb_id,
                "context_excerpt": (context_block or "")[:4000],
            },
        )
    except Exception:
        pass

    # 语气锁定：提取参考答案的句末语气词，指示 LLM 复用相同风格
    _tone_hint = _extract_tone_hint(answer)
    _adh = max(50, min(100, getattr(settings, "kb_adherence_pct", 90)))
    if _adh >= 90:
        _adh_rule = (
            f"你的回复必须 {_adh}% 贴近「知识库摘录」中的参考答案原文，"
            f"尤其是句末语气词（参考原文风格：{_tone_hint}）必须完全一致，不得替换。"
            "只能在自然表达上做最小调整（如去掉重复词、适应上下文语境），"
            "禁止自由发挥超出参考答案范围，禁止添加原文没有的信息，禁止使用知识库中未出现的称呼词。"
        )
    elif _adh >= 75:
        _adh_rule = (
            f"你的回复须 {_adh}% 贴近「知识库摘录」中的参考答案，"
            f"保持句末语气词风格（参考：{_tone_hint}），"
            "可做适度口语化调整，但不得脱离知识库事实、不得添加原文没有的信息，不得使用知识库中未出现的称呼词。"
        )
    else:
        _adh_rule = (
            f"你的回复须 {_adh}% 参考「知识库摘录」中的答案，"
            "可灵活组句但不得编造知识库未涵盖的事实，不得使用知识库中未出现的称呼词。"
        )
    _tone_instruction = f"\n【语气锁定·{_adh}%贴近原文】" + _adh_rule
    llm = generate_reply_segments(
        settings=settings,
        customer_text=buyer_text,
        conversation_context=context_block,
        rag_context=rag,
        few_shot=few_shot,
        extra_instructions=(
            extra_instructions
            + "\n【严格模式】你只能复述或轻微口语化「知识库摘录」中的事实；"
            "摘录未出现的信息一律不说，不确定则拆成更短的一句并降低 confidence。"
            + _tone_instruction
        ),
        phrase_blacklist=phrase_blacklist,
        product_block=product_block,
        campaign_block=campaign_block,
        gallery_block=gallery_block,
        discount_round_hint=discount_round_hint,
        closing_etiquette=closing_etiquette,
    )
    try:
        img_hits = search_images_for_question(
            conn,
            brand_id=brand_id,
            shop_id=shop_id,
            query=rewritten,
            top_k=3,
            min_score=0.56,
        )
        image_send_items = tuple((str(h.path.resolve()), h.image_id) for h in img_hits)
    except Exception:
        image_send_items = ()
    soft_hit = any(k in buyer_text for k in SOFT_OUTBOUND_REVIEW_TERMS)
    sens = (
        soft_hit
        or int(discount_round_index) >= 2
        or len(image_send_items) > 0
    )
    label = f"场景B:RAG({score_txt}|{kb_src})"
    return RoutedReplyPlan(
        takeover=False,
        takeover_reason="",
        segments=list(llm.segments),
        intent_label=label,
        kb_node=kb_node,
        strict_rag_context=rag,
        confidence_for_policy=float(llm.confidence),
        hitl_context=context_block,
        hitl_query=rewritten,
        hitl_pos=(answer,),
        hitl_neg=tuple(hit.alternatives)[:8],
        image_send_items=image_send_items,
        sensitivity_tier="sensitive" if sens else "none",
    )


def open_panse_chat_log_in_excel() -> None:
    """Windows：用默认程序（一般为 Excel）打开客户对话 CSV。"""
    import os
    import sys

    from apps.core.runtime_paths import default_panse_customer_chat_log_csv

    p = default_panse_customer_chat_log_csv()
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.is_file():
        p.write_text(
            "\ufeff时间戳,客户ID/昵称,发送方(客户/AI/人工),原始消息,AI识别出的意图标签,匹配到的话术节点\n",
            encoding="utf-8-sig",
        )
    if sys.platform.startswith("win"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        import subprocess

        subprocess.Popen(["xdg-open", str(p)])  # noqa: S603
