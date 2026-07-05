# -*- coding: utf-8 -*-
"""ChatBI 路由层 (Plan4 v2 §4.3) —— 纯规则, 零 LLM 依赖 (PC 离线仍工作)。

关键词打分匹配 20 模板 + 时间解析。命中模板 → 走模板(口径已审); 未命中 → 交上层
半生成/直出/拒答。这一层不碰 LLM, 保证 AI 引擎离线时模板问数照常。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.chatbi import templates as T
from app.chatbi.time_parser import TimeRange, parse_time

MATCH_THRESHOLD = 1


@dataclass(frozen=True)
class Route:
    kind: str                       # template / fallback
    template: "T.Template | None"
    time_range: "TimeRange | None"
    score: int


def match_template(question: str) -> tuple["T.Template | None", int]:
    """关键词打分, 取最高分模板 (含次数 + 长词加权)。"""
    q = (question or "").lower()
    best: "T.Template | None" = None
    best_score = 0
    for t in T.TEMPLATES:
        score = 0
        for kw in t.keywords:
            if kw.lower() in q:
                score += 1 + (1 if len(kw) >= 3 else 0)   # 长关键词更具体, 加权
        if score > best_score:
            best_score, best = score, t
    return best, best_score


def route(question: str, *, today: date | None = None,
          promo_windows: dict | None = None) -> Route:
    tr = parse_time(question, today=today, promo_windows=promo_windows)
    t, score = match_template(question)
    if t is not None and score >= MATCH_THRESHOLD:
        return Route("template", t, tr, score)
    return Route("fallback", None, tr, 0)
