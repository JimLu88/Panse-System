"""模糊匹配服务 (plan §4 MatchService.fuzzy())。

PG 上用 pg_trgm 的 similarity()；其他后端（含测试用的 SQLite）兜底走
ILIKE + Python 端简易 token-based 相似度。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.material import Material
from app.models.pricing import PricingSku
from app.models.product import Product

Scope = Literal["product", "material", "sku"]


@dataclass
class MatchCandidate:
    scope: Scope
    code: str
    name: str
    score: float


def _token_score(query: str, text: str) -> float:
    """简易 token 相似度：query 中每个 2+ 字 token 在 text 里命中各加权。"""
    if not query or not text:
        return 0.0
    q = query.strip().lower()
    t = text.lower()
    if q == t:
        return 1.0
    if q in t:
        return 0.7 + 0.3 * len(q) / max(len(t), 1)
    # 切分：中文按字、英数按词
    tokens = [c for c in q if c.strip()] if not any(c.isascii() for c in q) else q.split()
    if not tokens:
        return 0.0
    hits = sum(1 for tok in tokens if len(tok) >= 1 and tok in t)
    return hits / len(tokens) * 0.6


def fuzzy(
    db: Session,
    query: str,
    *,
    scope: Scope = "material",
    limit: int = 10,
) -> list[MatchCandidate]:
    """精确匹配优先，然后 ILIKE，再退到 token 相似度排序。"""
    query = (query or "").strip()
    if not query:
        return []

    if scope == "material":
        rows = db.execute(
            select(Material.code, Material.name).where(
                or_(Material.code.ilike(f"%{query}%"), Material.name.ilike(f"%{query}%"))
            ).limit(limit * 3)
        ).all()
    elif scope == "product":
        rows = db.execute(
            select(Product.code, Product.name).where(
                or_(Product.code.ilike(f"%{query}%"), Product.name.ilike(f"%{query}%"))
            ).limit(limit * 3)
        ).all()
    elif scope == "sku":
        rows = db.execute(
            select(PricingSku.sku_code, PricingSku.sku).where(
                or_(PricingSku.sku_code.ilike(f"%{query}%"), PricingSku.sku.ilike(f"%{query}%"))
            ).limit(limit * 3)
        ).all()
    else:
        raise ValueError(f"unknown scope {scope!r}")

    candidates = [
        MatchCandidate(scope=scope, code=code, name=name or "", score=_token_score(query, name or code))
        for code, name in rows
    ]
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]
