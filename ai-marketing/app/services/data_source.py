"""数据源适配层：真实采集（挂载项A，对标 MediaCrawler/Spider_XHS）可插拔。

接入契约（爬虫侧需提供，全部只读）：
  GET {CRAWLER_BASE_URL}/api/notes/rising?category=home      → 上升期笔记
  GET {CRAWLER_BASE_URL}/api/notes/hot?category=餐桌          → 竞品爆文(含拆解+评论样本)
  GET {CRAWLER_BASE_URL}/api/notes/{post_id}/comments        → 某笔记评论(自有笔记评论管理)
  GET {CRAWLER_BASE_URL}/api/notes/{post_id}/metrics         → 某笔记数据(自动回采)
  GET {CRAWLER_BASE_URL}/api/mentions?keywords=畔色,竞品      → 品牌/竞品提及

只读不发：爬虫只提供信号，发布/评论动作永远在人手里——风险隔离。
未配置 CRAWLER_BASE_URL 时全部回退演示数据，业务照常可跑。
"""
from __future__ import annotations

import logging
import random

import httpx

from ..config import get_settings

log = logging.getLogger("marketing.datasource")

# ---------------- 上升期笔记（评论引流用）----------------
_MOCK_RISING = [
    ("新房装修日记|餐厅终于搞定了", "decor_diary"),
    ("求推荐！小户型餐桌怎么选", "decor_diary"),
    ("晒晒我的新中式客厅", "decor_diary"),
    ("分享几个家居好物", "general"),
    ("租房改造|花了2000块", "decor_diary"),
]

# ---------------- 竞品爆文（拆解 + 评论样本）----------------
_MOCK_HOT = [
    {"title": "实木餐桌避雷！这3种木头千万别买", "author": "家居小张", "fans": 800,
     "likes": 12000, "collects": 5600, "comments": 430, "cover": "大字报+对比图",
     "structure": "钩子(避雷)→3个反例→正确做法→个人案例",
     "comments_sample": ["榉木和白蜡木哪个好？", "1.4米够用吗", "求链接", "岩板会不会冷冰冰", "实木真的不开裂吗"]},
    {"title": "花5000买的岩板餐桌，用半年真实测评", "author": "改造ing", "fans": 95000,
     "likes": 8900, "collects": 3200, "comments": 280, "cover": "实拍+手写标题",
     "structure": "悬念(贵不贵)→开箱→使用细节→翻车点→总结",
     "comments_sample": ["岩板会裂吗", "多大尺寸", "什么牌子", "好打理吗", "甲醛大不大"]},
    {"title": "小户型餐桌这样选，多出5㎡", "author": "收纳菌", "fans": 1200,
     "likes": 15600, "collects": 8900, "comments": 510, "cover": "前后对比+尺寸标注",
     "structure": "痛点(空间小)→选品逻辑→尺寸公式→实景→清单",
     "comments_sample": ["折叠款推荐吗", "1.2米坐得下4人吗", "链接呢", "圆桌还是方桌", "求尺寸"]},
]

# ---------------- 自有笔记评论（评论管理用）----------------
_MOCK_INBOUND = [
    "这个多少钱呀", "1.4米的有现货吗", "榉木和橡木哪个更耐用", "颜色会不会色差",
    "好看！求链接", "甲醛检测报告有吗", "工期要多久", "踩雷了，到货色差很大",
    "质量真的好，用了一年", "能定制尺寸吗",
]

# ---------------- 品牌/竞品提及（舆情用）----------------
_MOCK_MENTIONS = [
    {"type": "brand", "title": "求问畔色家居的餐桌怎么样", "snippet": "有人买过畔色的实木餐桌吗", "sentiment": "neutral"},
    {"type": "brand", "title": "畔色餐桌开箱", "snippet": "畔色的质感真不错，纹理好看", "sentiment": "pos"},
    {"type": "competitor", "title": "XX家具踩雷", "snippet": "买了XX的餐桌，有更好的牌子推荐吗", "sentiment": "neg"},
]


def _crawler_get(path: str, params: dict | None = None):
    """调真实爬虫；失败返回 None（调用方回退演示数据）。"""
    base = get_settings().crawler_base_url
    if not base:
        return None
    try:
        resp = httpx.get(f"{base.rstrip('/')}{path}", params=params or {}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("爬虫不可用 %s，回退演示数据: %s", path, e)
        return None


def fetch_rising_notes() -> tuple[list[dict], str]:
    """上升期笔记（评论引流）。返回 (列表, 来源 mock/crawler)。"""
    data = _crawler_get("/api/notes/rising", {"category": "home"})
    if isinstance(data, list) and data:
        return [{"title": n.get("title", ""), "url": n.get("url", ""),
                 "kind": n.get("kind", "general"),
                 "growth": float(n.get("growth", 0.5))} for n in data], "crawler"
    return [{"title": t, "url": "", "kind": k, "growth": None} for t, k in _MOCK_RISING], "mock"


def fetch_hot_notes(category: str = "") -> tuple[list[dict], str]:
    """竞品爆文（含拆解 + 评论样本）。"""
    data = _crawler_get("/api/notes/hot", {"category": category})
    if isinstance(data, list) and data:
        return data, "crawler"
    return [dict(n) for n in _MOCK_HOT], "mock"


def fetch_inbound_comments(post_id: str | int) -> tuple[list[str], str]:
    """某笔记下的评论（自有笔记评论管理）。"""
    data = _crawler_get(f"/api/notes/{post_id}/comments")
    if isinstance(data, list) and data:
        return [c if isinstance(c, str) else c.get("text", "") for c in data], "crawler"
    # 演示：随机取若干条
    return random.sample(_MOCK_INBOUND, k=random.randint(2, 5)), "mock"


def fetch_note_metrics(post_id: str | int) -> tuple[dict | None, str]:
    """某笔记的数据（自动回采）。"""
    data = _crawler_get(f"/api/notes/{post_id}/metrics")
    if isinstance(data, dict) and data:
        return data, "crawler"
    # 演示：生成一组合理随机数据
    views = random.randint(300, 8000)
    return {
        "views": views,
        "likes": int(views * random.uniform(0.02, 0.1)),
        "comments": int(views * random.uniform(0.005, 0.03)),
        "collects": int(views * random.uniform(0.02, 0.08)),
    }, "mock"


def fetch_mentions(keywords: list[str]) -> tuple[list[dict], str]:
    """品牌/竞品提及（舆情）。"""
    data = _crawler_get("/api/mentions", {"keywords": ",".join(keywords)})
    if isinstance(data, list) and data:
        return data, "crawler"
    return [dict(m) for m in _MOCK_MENTIONS], "mock"


def status() -> dict:
    base = get_settings().crawler_base_url
    return {
        "mode": "crawler" if base else "mock",
        "crawler_url": base or None,
        "hint": ("已接入真实采集数据源（只读）" if base
                 else "当前为内置演示数据。接入采集器（如 MediaCrawler/Spider_XHS 类，只读不发）后自动切真实数据：竞品爆文/自有评论/数据回采/品牌舆情"),
    }
