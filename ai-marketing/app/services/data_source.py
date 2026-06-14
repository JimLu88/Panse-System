"""数据源适配层：真实爬虫（挂载项A）可插拔，未配置则用内置演示数据。

接入契约（AI 数据爬虫侧需提供）：
  GET {CRAWLER_BASE_URL}/api/notes/rising?category=home
  → [{"title": str, "url": str, "kind": "decor_diary"|"general", "growth": float}, ...]

只读不发：爬虫只提供选题与评论机会信号，发布动作永远在人手里——风险隔离。
"""
from __future__ import annotations

import logging

import httpx

from ..config import get_settings

log = logging.getLogger("marketing.datasource")

# 内置演示数据（未接爬虫时）
_MOCK_NOTES = [
    ("新房装修日记|餐厅终于搞定了", "decor_diary"),
    ("求推荐！小户型餐桌怎么选", "decor_diary"),
    ("晒晒我的新中式客厅", "decor_diary"),
    ("分享几个家居好物", "general"),
    ("租房改造|花了2000块", "decor_diary"),
]


def fetch_rising_notes() -> tuple[list[dict], str]:
    """返回 (上升期笔记列表, 数据源标识 mock/crawler)。爬虫失败自动回退演示数据。"""
    base = get_settings().crawler_base_url
    if base:
        try:
            resp = httpx.get(f"{base.rstrip('/')}/api/notes/rising",
                             params={"category": "home"}, timeout=15)
            resp.raise_for_status()
            notes = resp.json()
            if isinstance(notes, list) and notes:
                return [{"title": n.get("title", ""), "url": n.get("url", ""),
                         "kind": n.get("kind", "general"),
                         "growth": float(n.get("growth", 0.5))} for n in notes], "crawler"
        except (httpx.HTTPError, ValueError) as e:
            log.warning("爬虫数据源不可用，回退演示数据: %s", e)
    return [{"title": t, "url": "", "kind": k, "growth": None} for t, k in _MOCK_NOTES], "mock"


def status() -> dict:
    base = get_settings().crawler_base_url
    return {
        "mode": "crawler" if base else "mock",
        "crawler_url": base or None,
        "hint": ("已接入真实爬虫数据源" if base
                 else "当前为内置演示数据。接入「AI 数据爬虫」后自动切换真实热榜/上升笔记（只读不发，零封号风险）"),
    }
