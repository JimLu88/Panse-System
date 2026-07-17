"""活动系统云端 LLM (DeepSeek / 通义千问) — 设置读取 + provider 构造 + 兜底实现。

用途 (2026-07-17): 活动生命周期里两个「规则失手时」的可选兜底:
- 活动发现: WA 抓回 raw 文本但规则解析不出日期 → LLM 抽 {title, start, end}
- 活动核对: 失败原因文本 → 一句话归类 (classify_failure_reason)

设置项 (system_settings KV, 后台「管理→AI 集成」可改, 无需重启):
    campaign_ai_provider   none | deepseek | qwen  (默认 none = 关闭, 一切兜底不生效)
    campaign_ai_model      默认 deepseek-chat / qwen-plus
    campaign_ai_api_key    机密 — 走 settings_service 既有加密 (HMAC-SHA256 CTR 流 + tag),
                           绝不明文落库; 读取接口只回「已配置 + 尾4位」

原则:
- 未配置 / key 缺失 / 上游失败 → 本模块函数一律返回 None, 调用方保持原行为 (零行为变化)。
- 全部走 ai_provider.build_provider 的 OpenAI 兼容通道 (不改 ai_provider 行为)。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service
from app.services.ai_provider import AiProvider, AiUnavailable, build_provider

logger = logging.getLogger("panse.campaign_ai")

# provider → OpenAI 兼容 base_url + 默认模型 (spec 2026-07-17: 只开 DeepSeek / 千问两家)
CAMPAIGN_AI_PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "label": "通义千问 Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "default_model": "qwen-plus",
    },
}

PROVIDER_OPTIONS = (
    [{"value": "none", "label": "关闭 (不启用兜底)", "default_model": ""}]
    + [{"value": k, "label": v["label"], "default_model": v["default_model"]}
       for k, v in CAMPAIGN_AI_PROVIDERS.items()]
)

_TAIL_MIN_LEN = 9    # key 太短时连尾4位都不给 (否则近乎回明文)


def _read_provider(db: Session) -> str:
    p = (settings_service.get(db, "campaign_ai_provider") or "none").strip().lower()
    return p if p in CAMPAIGN_AI_PROVIDERS or p == "none" else "none"


def settings_status(db: Session) -> dict:
    """给设置接口用的状态视图 — 绝不含明文 key (只 set 标志 + 尾4位)。"""
    provider = _read_provider(db)
    meta = CAMPAIGN_AI_PROVIDERS.get(provider)
    model = (settings_service.get(db, "campaign_ai_model") or "").strip()
    if not model and meta:
        model = meta["default_model"]
    key = settings_service.get(db, "campaign_ai_api_key") or ""
    return {
        "provider": provider,
        "model": model,
        "api_key_set": bool(key),
        "api_key_tail": key[-4:] if len(key) >= _TAIL_MIN_LEN else "",
    }


def get_campaign_ai(db: Session) -> Optional[AiProvider]:
    """按设置构造 OpenAI 兼容 provider; 未配置 (provider=none / 没 key) → None。"""
    provider = _read_provider(db)
    meta = CAMPAIGN_AI_PROVIDERS.get(provider)
    if meta is None:
        return None
    api_key = settings_service.get(db, "campaign_ai_api_key")
    if not api_key:
        return None
    model = (settings_service.get(db, "campaign_ai_model") or "").strip() or meta["default_model"]
    try:
        return build_provider({
            "provider": "openai",           # DeepSeek / 千问都走 OpenAI 兼容协议
            "api_key": api_key,
            "model": model,
            "base_url": meta["base_url"],
        })
    except AiUnavailable:
        return None


# ── 严格 JSON 解析 (LLM 输出) ────────────────────────────────────────────────

def _strict_json(text: str) -> Optional[dict]:
    """LLM 回复 → dict。允许剥代码围栏 / 掐首尾大括号一次; 仍失败 → None (静默降级)。"""
    s = (text or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    for candidate in (s, s[s.find("{"): s.rfind("}") + 1] if "{" in s and "}" in s else ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            return data
    return None


# ── 兜底 ①: 活动发现 raw 文本 → {title, start, end} ──────────────────────────

_EXTRACT_SYSTEM = (
    "你是电商平台活动信息抽取器。用户给你一段从千牛营销活动列表抓取的原始文本, "
    "请抽取活动标题和起止时间。只输出一个严格 JSON 对象, 不要任何解释、前后缀或代码围栏:\n"
    '{"title": "活动标题", "start": "YYYY-MM-DD HH:MM:SS", "end": "YYYY-MM-DD HH:MM:SS"}\n'
    "抽不出来的字段用 null。年份缺失时按用户给出的今天日期推断 (活动一般在今天之后)。"
)


def extract_campaign_fields(db: Session, raw_text: str) -> Optional[dict]:
    """raw 文本 → {"title": str|None, "start": str|None, "end": str|None}。
    未配置 AI / 调用失败 / 非严格 JSON → None (调用方保持规则解析的原行为)。"""
    text = (raw_text or "").strip()
    if not text:
        return None
    provider = get_campaign_ai(db)
    if provider is None:
        return None
    try:
        resp = provider.chat(
            system=_EXTRACT_SYSTEM,
            user=f"今天是 {date.today().isoformat()}。原始文本:\n{text[:2000]}",
            max_tokens=300,
        )
        data = _strict_json(resp.text)
    except Exception as e:  # noqa: BLE001 — 兜底路径, 任何失败都静默降级
        logger.debug("campaign_ai 抽取失败(静默降级): %s", e)
        return None
    if data is None:
        return None
    out = {}
    for k in ("title", "start", "end"):
        v = data.get(k)
        out[k] = str(v).strip() if isinstance(v, (str, int, float)) and str(v).strip() else None
    if not any(out.values()):
        return None
    return out


# ── 兜底 ②: 失败原因归类 ─────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = (
    "你是电商活动报名失败原因归类器。用户给你一条千牛活动报名/核对的失败原因文本, "
    "归类成一句话原因 (不超过20字, 例如: 低于最低标价线 / 已有进行中活动冲突 / "
    "SKU映射过期 / 新品促销管控价限制 / 其他)。\n"
    '只输出严格 JSON: {"reason": "一句话原因"}, 不要任何解释或代码围栏。'
)


def classify_failure_reason(db: Session, text: str) -> Optional[str]:
    """失败文本 → 一句话归类。未配置 AI / 失败 → None (零行为变化)。"""
    t = (text or "").strip()
    if not t:
        return None
    provider = get_campaign_ai(db)
    if provider is None:
        return None
    try:
        resp = provider.chat(system=_CLASSIFY_SYSTEM, user=t[:1000], max_tokens=100)
        data = _strict_json(resp.text)
    except Exception as e:  # noqa: BLE001
        logger.debug("campaign_ai 归类失败(静默降级): %s", e)
        return None
    if not data:
        return None
    reason = str(data.get("reason") or "").strip()
    return reason or None
