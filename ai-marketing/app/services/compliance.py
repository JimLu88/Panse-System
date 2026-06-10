"""敏感词分级 + AI 感评分（合规预检）。

对应 config-rule-center.md（S 熔断 / A 强制人工 / B 预警）与 03.5 体检报告。
"""
from __future__ import annotations

import re

from ..config import BANNED_WORDS


def scan_banned(text: str) -> dict[str, list[str]]:
    """扫敏感词，返回各级命中。"""
    hits = {"S": [], "A": [], "B": []}
    for level, words in BANNED_WORDS.items():
        for w in words:
            if w in text:
                hits[level].append(w)
    return hits


def compliance_blocked(hits: dict[str, list[str]]) -> bool:
    """S 级命中 → 直接拒绝。"""
    return bool(hits.get("S"))


def ai_likeness_score(text: str) -> int:
    """AI 感评分 0-100（越低越像人）。MVP 用启发式规则。

    真实场景应接 GPTZero / 自训模型；这里用可解释的代理指标。
    """
    score = 50
    # 口语/真实感锚降低 AI 感
    personal = len(re.findall(r"我家|我|上次|之前|刚|真的", text))
    score -= min(personal * 4, 24)
    # emoji 增加真实感
    emoji = len(re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", text))
    score -= min(emoji * 3, 15)
    # 过度排比/书面词增加 AI 感
    if re.search(r"首先|其次|综上所述|总而言之", text):
        score += 20
    return max(0, min(100, score))


def info_density(text: str) -> float:
    """信息密度评分 0-10（实体/数字密度的代理）。"""
    nums = len(re.findall(r"\d+(?:\.\d+)?\s*(?:米|cm|公分|元|年|个|款)", text))
    entities = len(re.findall(r"榉木|橡木|岩板|实木|环保板|贴皮|折叠|餐桌|餐椅|茶几|柜", text))
    length = max(len(text), 1)
    raw = (nums * 2 + entities) / length * 200
    return round(min(raw, 10.0), 1)
