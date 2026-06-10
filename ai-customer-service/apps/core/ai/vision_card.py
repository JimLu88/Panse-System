"""
产品卡片：OCR 启发式检测 + Vision LLM 抽取 JSON + 本地 products 表加权匹配。
禁止编造：Vision 仅提取截图内可见信息；匹配失败则交由上层走人工或正常 RAG。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from io import BytesIO
from typing import Any

import numpy as np
from PIL import Image

from apps.core.ai.llm_client import litellm_completion_vision_image
from apps.core.configs.base_settings import BaseSettings
from apps.core.ocr.models import OCRSpan


def heuristic_product_card_like(spans: list[OCRSpan], *, roi_height: int) -> bool:
    """根据 OCR 文本块判断聊天区是否像「商品卡片」区域（启发式）。"""
    if not spans or roi_height <= 0:
        return False
    blob = "".join((s.text or "") for s in spans)
    blob_nospace = blob.replace(" ", "").replace("\n", "")
    score = 0
    if any(x in blob_nospace for x in ("￥", "¥")):
        score += 1
    if any(x in blob_nospace for x in ("包邮", "已售", "月销", "评价", "店铺")):
        score += 1
    if "查看" in blob_nospace and "宝贝" in blob_nospace:
        score += 2
    if re.search(r"\d+\.?\d*\s*[万千百]?\s*人付款", blob_nospace):
        score += 1
    if re.search(r"[¥￥]\s*\d", blob_nospace):
        score += 1
    return score >= 2


def buyer_message_is_substantive(text: str) -> bool:
    """是否像「具体问题」而非仅卡片上的价格/标题碎片。"""
    t = (text or "").strip()
    if len(t) >= 12:
        return True
    markers = (
        "什么",
        "怎么",
        "多少",
        "尺寸",
        "规格",
        "颜色",
        "材质",
        "发货",
        "几天",
        "吗",
        "?",
        "？",
        "安装",
        "现货",
        "定制",
        "图片",
        "链接",
        "对比",
        "是否",
        "能不能",
        "有没有",
        "长宽高",
        "厚",
        "宽",
    )
    return any(m in t for m in markers) and len(t) >= 3


def _strip_json_fence(raw: str) -> str:
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        return m.group(1).strip()
    return s


def _rgb_to_png_bytes(img: np.ndarray) -> tuple[str, bytes]:
    if img.ndim != 3 or img.shape[2] < 3:
        raise ValueError("需要 RGB ndarray")
    rgb = img[:, :, :3].copy()
    im = Image.fromarray(rgb)
    im.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    bio = BytesIO()
    im.save(bio, format="PNG", optimize=True)
    return "image/png", bio.getvalue()


def extract_card_json_from_rgb(settings: BaseSettings, img: np.ndarray) -> dict[str, Any] | None:
    """
    Vision LLM 抽取卡片 JSON。
    成功返回 dict，至少含 truncated_title / price / visual_features 字符串键。
    """
    model = (settings.model_front_desk or "").strip()
    if not model:
        return None
    mime, raw = _rgb_to_png_bytes(img)
    system = (
        "你是家具/家装电商「商品卡片」截图解析器。只能依据图像中已出现的可见文字与明显视觉结构做摘录，"
        "禁止推测库存、禁止编造未出现的数字或承诺。\n"
        "只输出一个 JSON 对象（不要 Markdown 围栏外的任何字符），字段必须为：\n"
        '{"truncated_title":"字符串","price":"字符串","visual_features":"字符串"}\n'
        "truncated_title：商品标题可见片段（可截断）；price：价格区原文如 ¥1234；"
        "visual_features：材质/颜色/门型等可见关键词，用顿号分隔，没有则空字符串。"
    )
    user = "请解析当前聊天截图中的商品卡片（若有），严格按 JSON 格式输出。"
    try:
        out = litellm_completion_vision_image(
            settings=settings,
            model=model,
            system=system,
            user_text=user,
            image_mime=mime,
            image_bytes=raw,
            max_tokens=512,
            temperature=0.0,
        )
    except Exception:
        return None
    try:
        obj = json.loads(_strip_json_fence(out))
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    tt = obj.get("truncated_title")
    pr = obj.get("price")
    vf = obj.get("visual_features")
    if not isinstance(tt, str):
        tt = str(tt or "")
    if not isinstance(pr, str):
        pr = str(pr or "")
    if not isinstance(vf, str):
        vf = str(vf or "")
    return {"truncated_title": tt.strip(), "price": pr.strip(), "visual_features": vf.strip()}


@dataclass(slots=True)
class ProductCardMatchResult:
    ambiguous: bool
    product_id: str | None
    product_code: str
    product_name: str
    best_score: float
    runner_up_score: float
    candidates: tuple[str, ...]


def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def match_product_by_card(
    conn: sqlite3.Connection,
    *,
    brand_id: str,
    shop_id: str,
    card_json: dict[str, Any],
) -> ProductCardMatchResult:
    """标题前缀 + 文案/材质 + 价格数字弱匹配；分差过小视为歧义。"""
    title = str(card_json.get("truncated_title") or "").strip()
    price_raw = str(card_json.get("price") or "").strip()
    visual = str(card_json.get("visual_features") or "").strip()
    if len(title) < 2:
        return ProductCardMatchResult(
            ambiguous=False,
            product_id=None,
            product_code="",
            product_name="",
            best_score=0.0,
            runner_up_score=0.0,
            candidates=(),
        )
    key = title[:22].strip()
    like = f"%{key}%"
    cur = conn.execute(
        """
        SELECT product_id, product_code, name,
               COALESCE(copywriting,''), COALESCE(main_material,''),
               COALESCE(sub_material,''), COALESCE(size_details,'')
        FROM products
        WHERE brand_id = ? AND shop_id = ?
          AND (name LIKE ? OR product_code LIKE ? OR COALESCE(copywriting,'') LIKE ?)
        ORDER BY updated_at DESC
        LIMIT 80
        """,
        (brand_id, shop_id, like, like, like),
    )
    rows = cur.fetchall()
    if not rows and len(key) > 8:
        short = key[:8]
        like2 = f"%{short}%"
        cur = conn.execute(
            """
            SELECT product_id, product_code, name,
                   COALESCE(copywriting,''), COALESCE(main_material,''),
                   COALESCE(sub_material,''), COALESCE(size_details,'')
            FROM products
            WHERE brand_id = ? AND shop_id = ?
              AND (name LIKE ? OR product_code LIKE ? OR COALESCE(copywriting,'') LIKE ?)
            ORDER BY updated_at DESC
            LIMIT 80
            """,
            (brand_id, shop_id, like2, like2, like2),
        )
        rows = cur.fetchall()
    if not rows:
        return ProductCardMatchResult(
            ambiguous=False,
            product_id=None,
            product_code="",
            product_name="",
            best_score=0.0,
            runner_up_score=0.0,
            candidates=(),
        )

    price_digits = _digits(price_raw)
    scored: list[tuple[float, str, str, str]] = []
    blob_vf = visual.lower()
    pieces = [p.strip() for p in re.split(r"[、,，\s]+", visual) if len(p.strip()) >= 2]

    for r in rows:
        pid, code, name, cw, mm, sm, sz = (
            str(r[0]),
            str(r[1]),
            str(r[2]),
            str(r[3] or ""),
            str(r[4] or ""),
            str(r[5] or ""),
            str(r[6] or ""),
        )
        comb = f"{name} {cw} {mm} {sm} {sz}".lower()
        s_title = SequenceMatcher(None, title.lower(), name.lower()).ratio() * 0.52
        s_vis = 0.0
        if blob_vf:
            hit = sum(1 for p in pieces if p.lower() in comb)
            s_vis = min(0.28, 0.07 * hit)
        s_price = 0.0
        if price_digits and len(price_digits) >= 3 and price_digits in _digits(comb):
            s_price = 0.12
        total = min(1.0, s_title + s_vis + s_price)
        scored.append((total, pid, code, name))

    scored.sort(key=lambda x: -x[0])
    best_s, best_pid, best_code, best_name = scored[0]
    second_s = scored[1][0] if len(scored) > 1 else 0.0
    names = tuple(f"{x[2]}:{x[3][:40]}" for x in scored[:6])

    if best_s < 0.36:
        return ProductCardMatchResult(
            ambiguous=False,
            product_id=None,
            product_code="",
            product_name="",
            best_score=best_s,
            runner_up_score=second_s,
            candidates=names,
        )

    ambiguous = len(scored) > 1 and (best_s - second_s) < 0.065 and second_s >= 0.34
    if ambiguous:
        return ProductCardMatchResult(
            ambiguous=True,
            product_id=None,
            product_code="",
            product_name="",
            best_score=best_s,
            runner_up_score=second_s,
            candidates=names,
        )
    return ProductCardMatchResult(
        ambiguous=False,
        product_id=best_pid,
        product_code=best_code,
        product_name=best_name,
        best_score=best_s,
        runner_up_score=second_s,
        candidates=names,
    )


def format_card_context_for_prompt(
    *,
    card_json: dict[str, Any],
    match: ProductCardMatchResult,
) -> str:
    """注入路由/重写前的补充说明块。"""
    lines = [
        "【系统预识别：客户发来的商品卡片】",
        f"- 卡片标题摘录：{card_json.get('truncated_title', '')}",
        f"- 卡片价格原文：{card_json.get('price', '')}",
        f"- 卡片视觉关键词：{card_json.get('visual_features', '')}",
    ]
    if match.product_id and match.product_name:
        lines.append(
            f"- 本地库唯一匹配：{match.product_name}（编码 {match.product_code}，匹配分 {match.best_score:.2f}）"
        )
    return "\n".join(lines)


@dataclass(slots=True)
class PendingCardContext:
    """跨轮缓存：欢迎语后静默识别，待客户下一条文字再合并。"""

    card_json: dict[str, Any]
    match: ProductCardMatchResult
    created_mono: float = 0.0
    TTL_SECONDS: float = 120.0

    def __post_init__(self) -> None:
        if self.created_mono <= 0:
            object.__setattr__(self, "created_mono", time.monotonic())

    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_mono) > self.TTL_SECONDS
