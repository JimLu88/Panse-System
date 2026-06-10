"""多 LLM 路由（简化版）。对应 02-cross-cutting/llm-router.md。

业务代码只声明"任务类型"，由路由表决定调哪档模型。
默认 provider=mock：内置家具风格文案生成器，免 key 即可端到端跑通。
配 anthropic / openai 则走真实模型。
"""
from __future__ import annotations

import json
import random
from typing import Any

import httpx

from ..config import get_settings

# 任务 → 档位（声明式路由，对应设计稿 routes 表）
ROUTES = {
    "generator.logic_restructure": "top",
    "generator.style_injection": "top",
    "generator.fact_check": "cheap",
    "generator.compliance_scan": "cheap",
    "comment.draft": "top",
    "collector.style_extract": "cheap",
}


class LLMRouter:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.provider = self.settings.llm_provider

    def complete(self, task: str, prompt: str, *, system: str = "", json_mode: bool = False) -> str:
        """按任务路由到模型并返回文本。mock 时走内置生成器。"""
        if self.provider == "mock":
            return _MockLLM.complete(task, prompt, json_mode=json_mode)
        if self.provider == "anthropic":
            return self._anthropic(prompt, system)
        if self.provider == "openai":
            return self._openai(prompt, system)
        raise ValueError(f"未知 LLM_PROVIDER: {self.provider}")

    def _anthropic(self, prompt: str, system: str) -> str:
        s = self.settings
        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": s.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": s.anthropic_model,
                "max_tokens": 1500,
                "system": system or "你是家具品牌的小红书内容创作助手。",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]

    def _openai(self, prompt: str, system: str) -> str:
        s = self.settings
        base = s.openai_base_url or "https://api.openai.com/v1"
        resp = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {s.openai_api_key}"},
            json={
                "model": s.openai_model,
                "messages": [
                    {"role": "system", "content": system or "你是家具品牌的小红书内容创作助手。"},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


# ----------------- 内置 mock：家具风格文案生成器 -----------------
_HOOKS = [
    "搬进新家才发现，餐桌选错真的会天天后悔😭",
    "小户型的姐妹听我一句，餐桌别买大的！",
    "纠结了一个月的实木餐桌，终于到货啦～",
    "装修踩过的坑，今天全告诉你🙋",
]
_PAINS = [
    "之前那张贴皮的用了半年就开胶，边角还磕白了。",
    "客厅就那么大，桌子一摆转身都难。",
    "网上图片好看，到手色差大到想退货。",
]
_SOLUTIONS = [
    "后来换了榉木的，纹理是真的好看，手感也扎实。",
    "选了 1.2 米的折叠款，平时收起来一点不占地方。",
    "认准实木结构 + 环保板材，闻着没有刺鼻味道。",
]
_CASES = [
    "我家用了快一年，每天擦一擦还是很新。",
    "上次朋友来都问我在哪买的。",
]
_CTAS = [
    "有需要的可以扣 1，我整理了避坑清单～",
    "尺寸怎么选评论区告诉你家户型，帮你看看。",
]
_TAGS = ["实木家具", "小户型装修", "餐桌推荐", "家居好物", "装修避坑", "新家分享"]


class _MockLLM:
    @staticmethod
    def complete(task: str, prompt: str, *, json_mode: bool) -> str:
        if task == "generator.logic_restructure" or task == "generator.style_injection":
            data = {
                "title": random.choice(_HOOKS)[:20],
                "narrative_units": [
                    {"type": "hook", "content": random.choice(_HOOKS)},
                    {"type": "pain_point", "content": random.choice(_PAINS)},
                    {"type": "solution", "content": random.choice(_SOLUTIONS)},
                    {"type": "case", "content": random.choice(_CASES)},
                    {"type": "cta", "content": random.choice(_CTAS)},
                ],
                "tags": random.sample(_TAGS, k=5),
            }
            return json.dumps(data, ensure_ascii=False)
        if task == "comment.draft":
            seeds = [
                "我家也是榉木的，用了一年多还是很扎实，确实耐造👍",
                "小户型真的建议选窄一点的，我家 1.2 米刚刚好～",
                "纹理好看是真的，平时擦一擦保养下能用很久。",
            ]
            return random.choice(seeds)
        if task == "generator.fact_check":
            return json.dumps({"passed": [], "pending_human": []}, ensure_ascii=False)
        return "（mock 输出）"


_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
