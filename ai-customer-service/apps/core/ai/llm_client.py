"""
统一 LLM 出口：基于 LiteLLM，按 BaseSettings 中的前台 / 深度模型 ID 与多供应商密钥路由。
前台：廉价格式化 JSON；深度分析：长上下文推理（话术导入、陪伴报表等）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from apps.core.configs.base_settings import BaseSettings
from apps.core.shadow.rules_prompt import load_shadow_evolution_prompt_block


@dataclass(frozen=True, slots=True)
class LLMReply:
    segments: list[str]
    raw_text: str
    confidence: float


def resolve_litellm_api_key(settings: BaseSettings, model: str) -> str:
    """根据 LiteLLM model id 选择对应密钥。"""
    m = (model or "").strip().lower()
    if not m:
        return ""
    if m.startswith("deepseek/"):
        return (settings.deepseek_api_key or "").strip()
    if m.startswith("dashscope/"):
        return (settings.dashscope_api_key or "").strip()
    if m.startswith("openai/"):
        return (settings.openai_api_key or "").strip()
    if m.startswith("anthropic/"):
        return (settings.anthropic_api_key or "").strip()
    if m.startswith("gemini/") or m.startswith("google/"):
        return (settings.gemini_api_key or "").strip()
    # 裸写模型名时的启发式
    if "gpt" in m or "o1" in m or "o3" in m:
        return (settings.openai_api_key or "").strip()
    if "claude" in m:
        return (settings.anthropic_api_key or "").strip()
    if "gemini" in m:
        return (settings.gemini_api_key or "").strip()
    if "qwen" in m:
        return (settings.dashscope_api_key or "").strip()
    if "deepseek" in m:
        return (settings.deepseek_api_key or "").strip()
    return (
        (settings.openai_api_key or settings.anthropic_api_key or "").strip()
    )


def _enable_litellm_prompt_cache_env() -> None:
    """默认开启 LiteLLM 侧可用的缓存提示（环境变量，无需 UI 配置）。"""
    os.environ.setdefault("LITELLM_CACHE", "True")


# 与部分中转 OpenAPI 一致：OpenAI Chat(Gemini 预设 tools) 要求 body 含 tools（如 googleSearch）
_GEMINI_OPENAI_COMPAT_TOOLS = [
    {"type": "function", "function": {"name": "googleSearch"}},
]


def litellm_completion_text(
    *,
    settings: BaseSettings,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 4096,
    temperature: float | None = 0.2,
    deep_analysis: bool = False,
) -> str:
    """单次对话，返回助手文本。model 须与 BaseSettings 中 chosen 模型一致，不由本函数改写。"""
    try:
        import litellm
    except ImportError as e:
        raise RuntimeError("请 pip install litellm") from e

    _enable_litellm_prompt_cache_env()
    api_key = resolve_litellm_api_key(settings, model)
    if not api_key:
        raise RuntimeError(
            f"未配置与模型「{model}」对应的 API 密钥，请在设置中心填写。"
        )

    kwargs: dict = {
        "model": model.strip(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "api_key": api_key,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature

    base = (settings.llm_api_base or "").strip()
    if base:
        kwargs["api_base"] = base

    ml_all = model.strip().lower()
    if getattr(settings, "llm_gemini_attach_search_tool", True) and ml_all.startswith(
        "openai/"
    ) and "gemini" in ml_all and "tools" not in kwargs:
        kwargs["tools"] = list(_GEMINI_OPENAI_COMPAT_TOOLS)

    if deep_analysis:
        ml = model.strip().lower()
        _thinking_markers = (
            "thinking" in ml
            or "sonnet-4" in ml
            or "claude-3-7" in ml
            or "20250219" in ml
        )
        # Claude + 思考：原生 anthropic/ 走 Messages beta；openai/claude 走兼容中转由网关转发
        if _thinking_markers and "claude" in ml:
            eb = dict(kwargs.get("extra_body") or {})
            eb.setdefault(
                "thinking",
                {"type": "enabled", "budget_tokens": 16000},
            )
            kwargs["extra_body"] = eb
            if ml.startswith("anthropic/"):
                eh = dict(kwargs.get("extra_headers") or {})
                eh["anthropic-beta"] = "output-128k-2025-02-19"
                kwargs["extra_headers"] = eh

    resp = litellm.completion(**kwargs)
    choice = resp.choices[0]
    msg = choice.message
    content = getattr(msg, "content", None) or ""
    # Anthropic 思考模式：部分网关仅返回多段 block，顶层 content 为空
    if not str(content).strip():
        blocks = getattr(msg, "content_blocks", None)
        if blocks:
            parts: list[str] = []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    parts.append(str(b["text"]))
            if parts:
                content = "\n".join(parts)

    return str(content)


def _extract_json_array(text: str) -> list[str]:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        t = m.group(1).strip()
    arr = json.loads(t)
    if not isinstance(arr, list):
        raise ValueError("LLM 输出不是 JSON Array")
    out: list[str] = []
    for x in arr:
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def _parse_model_output(raw: str) -> LLMReply:
    t = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
    if m:
        t = m.group(1).strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            segs: list[str] = []
            raw_segs = obj.get("segments")
            if isinstance(raw_segs, list):
                for x in raw_segs:
                    if isinstance(x, str) and x.strip():
                        segs.append(x.strip())
            conf = float(obj.get("confidence", 0.65))
            conf = max(0.0, min(1.0, conf))
            if segs:
                return LLMReply(segments=segs, raw_text=raw, confidence=conf)
    except Exception:
        pass
    try:
        arr = json.loads(t)
        if isinstance(arr, list):
            out = [str(x).strip() for x in arr if isinstance(x, str) and str(x).strip()]
            if out:
                return LLMReply(segments=out, raw_text=raw, confidence=0.66)
    except Exception:
        pass
    try:
        segs = _extract_json_array(raw)
        return LLMReply(segments=segs, raw_text=raw, confidence=0.64)
    except Exception as e:
        raise ValueError(f"无法解析 LLM 输出: {e}") from e


def generate_reply_segments(
    *,
    settings: BaseSettings,
    customer_text: str,
    rag_context: str,
    few_shot: str,
    conversation_context: str = "",
    extra_instructions: str = "",
    phrase_blacklist: str = "",
    product_block: str = "",
    campaign_block: str = "",
    gallery_block: str = "",
    discount_round_hint: str = "",
    closing_etiquette: str = "",
    shadow_evolution_block: str | None = None,
) -> LLMReply:
    """
    前台实时客服：使用「前台模型」。仅依据本地 RAG 摘录格式化为短句 JSON，禁止编造事实。
    """
    model = (settings.model_front_desk or "").strip()
    if not model:
        raise RuntimeError("未配置「前台实时客服模型」（model_front_desk）")

    system = (
        "你是电商客服回复格式化器。你只能根据下方提供的「知识库摘录」与商品/活动块组织答复，"
        "禁止编造库存、价格、链接或知识库未出现的承诺。\n"
        "绝对禁止使用「亲」「亲亲」「亲爱的」等淘宝客服腔称呼词，"
        "只使用知识库原文中出现过的称呼方式。\n"
        "必须只输出一个 JSON 对象（不要 Markdown 围栏外的解释），格式严格为：\n"
        '{"confidence":0.0到1.0的小数,"segments":["发给客户的短句1","短句2",...]}\n'
        "confidence：知识库对客户问题的覆盖把握（未覆盖则偏低）。\n"
        "segments：口语化短消息，顺序即发送顺序。"
    )
    parts = []
    if (conversation_context or "").strip():
        parts.append("【近期对话上文】\n" + conversation_context.strip())
    parts.extend(
        [
            "【客户原话】\n" + customer_text,
            "【知识库摘录】\n" + rag_context,
            "【语气与格式 Few-shot】\n" + few_shot,
        ]
    )
    if product_block.strip():
        parts.append(product_block.strip())
    if campaign_block.strip():
        parts.append(campaign_block.strip())
    if gallery_block.strip():
        parts.append(gallery_block.strip())
    _evo = (
        (shadow_evolution_block or "").strip()
        if shadow_evolution_block is not None
        else load_shadow_evolution_prompt_block().strip()
    )
    if _evo:
        parts.append(_evo)
    if discount_round_hint.strip():
        parts.append(discount_round_hint.strip())
    if phrase_blacklist.strip():
        parts.append("【严禁出现在回复中的客套/空话模板】\n" + phrase_blacklist.strip())
    if closing_etiquette.strip():
        parts.append(closing_etiquette.strip())
    if extra_instructions.strip():
        parts.append("【本轮额外指令】\n" + extra_instructions.strip())
    parts.append(
        '请只输出 JSON 对象，例如 {"confidence":0.82,"segments":["嗯嗯","这款现货有的"]}。'
    )
    user = "\n\n".join(parts)

    raw = litellm_completion_text(
        settings=settings,
        model=model,
        system=system,
        user=user,
        max_tokens=1024,
        temperature=0.0,
    )
    return _parse_model_output(raw)


def generate_reply_segments_claude(
    *,
    api_key: str,
    model: str,
    customer_text: str,
    rag_context: str,
    few_shot: str,
    extra_instructions: str = "",
    phrase_blacklist: str = "",
    product_block: str = "",
    campaign_block: str = "",
    gallery_block: str = "",
    discount_round_hint: str = "",
    closing_etiquette: str = "",
    shadow_evolution_block: str | None = None,
) -> LLMReply:
    """兼容旧调用：视为 Anthropic 前台路径。"""
    st = BaseSettings(
        anthropic_api_key=api_key,
        model_front_desk=(
            model if "/" in model else f"anthropic/{model}"
        ),
    )
    return generate_reply_segments(
        settings=st,
        customer_text=customer_text,
        rag_context=rag_context,
        few_shot=few_shot,
        conversation_context="",
        extra_instructions=extra_instructions,
        phrase_blacklist=phrase_blacklist,
        product_block=product_block,
        campaign_block=campaign_block,
        gallery_block=gallery_block,
        discount_round_hint=discount_round_hint,
        closing_etiquette=closing_etiquette,
        shadow_evolution_block=shadow_evolution_block,
    )


def litellm_completion_vision_image(
    *,
    settings: BaseSettings,
    model: str,
    system: str,
    user_text: str,
    image_mime: str,
    image_bytes: bytes,
    max_tokens: int = 512,
    temperature: float = 0.0,
    deep_analysis: bool = False,
) -> str:
    """单次多模态对话：附带一张图片（data URL），返回助手文本。"""
    import base64

    try:
        import litellm
    except ImportError as e:
        raise RuntimeError("请 pip install litellm") from e

    _enable_litellm_prompt_cache_env()
    api_key = resolve_litellm_api_key(settings, model)
    if not api_key:
        raise RuntimeError(
            f"未配置与模型「{model}」对应的 API 密钥，请在设置中心填写。"
        )
    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    url = f"data:{image_mime};base64,{b64}"
    kwargs: dict = {
        "model": model.strip(),
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            },
        ],
        "api_key": api_key,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    base = (settings.llm_api_base or "").strip()
    if base:
        kwargs["api_base"] = base
    ml_all = model.strip().lower()
    if getattr(settings, "llm_gemini_attach_search_tool", True) and ml_all.startswith(
        "openai/"
    ) and "gemini" in ml_all and "tools" not in kwargs:
        kwargs["tools"] = list(_GEMINI_OPENAI_COMPAT_TOOLS)
    if deep_analysis:
        ml = model.strip().lower()
        _thinking_markers = (
            "thinking" in ml
            or "sonnet-4" in ml
            or "claude-3-7" in ml
            or "20250219" in ml
        )
        if _thinking_markers and "claude" in ml:
            eb = dict(kwargs.get("extra_body") or {})
            eb.setdefault(
                "thinking",
                {"type": "enabled", "budget_tokens": 16000},
            )
            kwargs["extra_body"] = eb
            if ml.startswith("anthropic/"):
                eh = dict(kwargs.get("extra_headers") or {})
                eh["anthropic-beta"] = "output-128k-2025-02-19"
                kwargs["extra_headers"] = eh

    resp = litellm.completion(**kwargs)
    choice = resp.choices[0]
    msg = choice.message
    content = getattr(msg, "content", None) or ""
    if not str(content).strip():
        blocks = getattr(msg, "content_blocks", None)
        if blocks:
            parts: list[str] = []
            for b in blocks:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                    parts.append(str(b["text"]))
            if parts:
                content = "\n".join(parts)
    return str(content)


def deep_analysis_completion(
    *,
    settings: BaseSettings,
    system: str,
    user: str,
    max_tokens: int = 8192,
    temperature: float = 0.3,
) -> str:
    """后台深度任务：陪伴报表、话术 Excel 整理、日志分析等。"""
    model = (settings.model_deep_analysis or "").strip()
    if not model:
        raise RuntimeError("未配置「AI 陪伴与深度分析模型」（model_deep_analysis）")
    return litellm_completion_text(
        settings=settings,
        model=model,
        system=system,
        user=user,
        max_tokens=max_tokens,
        temperature=temperature,
        deep_analysis=True,
    )


def deep_analysis_api_configured(settings: BaseSettings) -> bool:
    """是否已为深度模型配置密钥。"""
    m = (settings.model_deep_analysis or "").strip()
    return bool(m and resolve_litellm_api_key(settings, m))
