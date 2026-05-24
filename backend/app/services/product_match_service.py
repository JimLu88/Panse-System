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
