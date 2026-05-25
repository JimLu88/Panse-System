"""产品模糊匹配服务 — 供截图录单 (A2) 和微定制 AI (④) 复用。

匹配策略 (按优先级):
  1. sku_code 精确匹配
  2. PricingSku.sku 标记 token 全包含
  3. Product.name token 部分交集评分
返回 confidence (0.0–1.0) 和最佳候选.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.product import Product


def _tokens(text: str) -> set[str]:
    return set(re.split(r"[\s\-_/,，、]+", text.strip().lower())) - {""}


def match(
    db: Session,
    product_name: str,
    sku_text: str = "",
) -> dict:
    """返回 {product_code, product_name, sku_code, sku, confidence}."""
    combined = f"{product_name} {sku_text}".strip()
    if not combined:
        return _no_match()

    # 1. sku_code 精确
    if sku_text:
        row = db.query(PricingSku).filter(
            PricingSku.sku_code == sku_text.strip()
        ).first()
        if row:
            return _hit(row, db, 1.0)

    query_tokens = _tokens(combined)

    # 2. PricingSku.sku token 匹配
    best_sku: Optional[PricingSku] = None
    best_score = 0.0
    for row in db.query(PricingSku).all():
        if not row.sku:
            continue
        sku_tokens = _tokens(row.sku)
        if not sku_tokens:
            continue
        overlap = len(query_tokens & sku_tokens)
        score = overlap / max(len(query_tokens), len(sku_tokens))
        if score > best_score:
            best_score = score
            best_sku = row

    if best_sku and best_score >= 0.4:
        return _hit(best_sku, db, round(min(best_score, 0.99), 2))

    # 3. Product.name token 匹配
    best_prod: Optional[Product] = None
    best_prod_score = 0.0
    for prod in db.query(Product).all():
        prod_tokens = _tokens(prod.name)
        if not prod_tokens:
            continue
        overlap = len(query_tokens & prod_tokens)
        score = overlap / max(len(query_tokens), len(prod_tokens))
        if score > best_prod_score:
            best_prod_score = score
            best_prod = prod

    if best_prod and best_prod_score >= 0.3:
        return {
            "product_code": best_prod.code,
            "product_name": best_prod.name,
            "sku_code": None,
            "sku": None,
            "confidence": round(best_prod_score * 0.7, 2),
        }

    return _no_match()


def _hit(row: PricingSku, db: Session, confidence: float) -> dict:
    prod = db.query(Product).filter(Product.code == row.product_code).first()
    return {
        "product_code": row.product_code,
        "product_name": prod.name if prod else row.product_code,
        "sku_code": row.sku_code,
        "sku": row.sku,
        "confidence": confidence,
    }


def _no_match() -> dict:
    return {
        "product_code": None,
        "product_name": None,
        "sku_code": None,
        "sku": None,
        "confidence": 0.0,
    }


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), len(b))


def _char_set(text: str) -> set[str]:
    """字符集 (去掉分隔符/标点), 中文按字比对用."""
    return set(re.sub(r"[\s\-_/,，、.。()（）]+", "", (text or "").lower())) - {""}


def _similarity(query: str, target: str) -> float:
    """中文友好的相似度: token 重合 与 字符 Jaccard 取较大者。

    纯 token 法在中文里整词成一个 token (如 '榉木床' vs '榉木无边床' 重合=0),
    叠加字符 Jaccard 后能给出合理百分比 (≈0.6)。
    """
    tok = _overlap(_tokens(query), _tokens(target))
    qc, tc = _char_set(query), _char_set(target)
    char = len(qc & tc) / len(qc | tc) if (qc and tc) else 0.0
    return max(tok, char)


def match_ranked(
    db: Session,
    product_name: str,
    sku_text: str = "",
    *,
    limit: int = 10,
    max_skus: int = 10,
) -> list[dict]:
    """两级匹配度排序: 返回 Top-N 产品 (一级=产品名匹配度), 每个产品下挂
    其 SKU 列表 (二级=SKU 匹配度), 都按匹配度从高到低排序, 供前端人工挑选。

    返回元素:
      {product_code, product_name, product_confidence,
       skus: [{sku_code, sku, size_category, confidence}, ...]}
    """
    combined = f"{product_name} {sku_text}".strip()
    if not combined:
        return []

    # 一次性载入 SKU, 按产品分组
    skus_by_product: dict[str, list[PricingSku]] = {}
    for s in db.query(PricingSku).all():
        skus_by_product.setdefault(s.product_code, []).append(s)

    results: list[dict] = []
    for prod in db.query(Product).all():
        name_score = _similarity(combined, prod.name or "")

        sku_items: list[dict] = []
        best_sku_score = 0.0
        for s in skus_by_product.get(prod.code, []):
            # sku_code 精确命中 → 满分
            if sku_text and s.sku_code and s.sku_code.strip() == sku_text.strip():
                sku_score = 1.0
            else:
                sku_score = _similarity(combined, s.sku or "")
            best_sku_score = max(best_sku_score, sku_score)
            sku_items.append({
                "sku_code": s.sku_code,
                "sku": s.sku,
                "size_category": s.size_category,
                "confidence": round(min(sku_score, 1.0), 2),
            })

        # 综合相关度: 产品名 or 旗下任一 SKU 命中即可上榜
        relevance = max(name_score, best_sku_score)
        if relevance <= 0:
            continue
        sku_items.sort(key=lambda x: x["confidence"], reverse=True)
        results.append({
            "product_code": prod.code,
            "product_name": prod.name,
            "product_confidence": round(min(name_score, 1.0), 2),
            "_relevance": relevance,
            "skus": sku_items[:max_skus],
        })

    results.sort(key=lambda x: x["_relevance"], reverse=True)
    for r in results:
        r.pop("_relevance", None)
    return results[:limit]
