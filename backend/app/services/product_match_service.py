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


_MATERIAL_TERMS = (
    "黑胡桃", "樱桃木", "白蜡木", "红橡木", "白橡木", "榉木", "胡桃木",
    "橡木", "松木", "实木",
)
_EXPLICIT_MATERIAL_TERMS = tuple(
    term for term in _MATERIAL_TERMS if term != "实木"
)
_CATEGORY_TERMS = (
    "餐边柜", "床头柜", "电视柜", "玄关柜", "浴室柜", "餐桌", "圆桌",
    "书桌", "茶几", "双人床", "单人床", "沙发", "岛台", "床", "柜", "桌",
)
_FEATURE_TERMS = (
    "岩板", "洞石", "玻璃", "静音", "软包", "悬浮", "伸缩", "升降", "旋转",
)


def _core_text(text: str) -> str:
    """Remove size/action noise while retaining product identity words."""
    value = (text or "").lower()
    value = re.sub(
        r"\d+(?:\.\d+)?\s*(?:毫米|厘米|公分|mm|cm|米|m)?",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"(?:改成|改为|改到|修改|调整|做成|做到|宽度|长度|高度|尺寸|多少|什么价格|价格|报价|算价)",
        " ",
        value,
    )
    return re.sub(r"[\s\-_/,，、.。;；:：()（）*×xX]+", "", value)


def _concept_terms(text: str) -> tuple[str, ...]:
    core = _core_text(text)
    return tuple(
        term
        for term in (*_MATERIAL_TERMS, *_CATEGORY_TERMS, *_FEATURE_TERMS)
        if term in core
    )


def has_explicit_product_identity(text: str) -> bool:
    """True when text names both a material and a furniture category."""
    core = _core_text(text)
    return (
        any(term in core for term in _EXPLICIT_MATERIAL_TERMS)
        and any(term in core for term in _CATEGORY_TERMS)
    )


def _product_similarity(query: str, name: str, category: str = "") -> float:
    """Score a product itself; SKU combo words may not replace product identity.

    Character Jaccard penalises a precise short query such as ``榉木餐桌`` when
    the catalog name contains an extra finish word (``榉木岩板餐桌``).  For an
    explicit material+category query, directional concept coverage is the
    stronger signal: all requested concepts present means an exact product
    identity match even when the target has harmless extra descriptors.
    """
    core = _core_text(query)
    target = _core_text(f"{name or ''}{category or ''}")
    if not core or not target:
        return 0.0
    base = _similarity(core, target)
    query_chars, target_chars = set(core), set(target)
    directional = (
        len(query_chars & target_chars) / len(query_chars)
        if query_chars else 0.0
    )
    concepts = _concept_terms(core)
    coverage = (
        sum(1 for term in concepts if term in target) / len(concepts)
        if concepts else 0.0
    )
    if has_explicit_product_identity(core) and coverage == 1.0:
        return 1.0
    if core in target:
        return 1.0
    return min(1.0, max(base, directional * 0.90, coverage * 0.95))


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

    # 2. Product identity first.  A combo SKU under another product may contain
    #    the requested words (for example a rotating cabinet SKU includes an
    #    attached "榉木餐桌"); it must not outrank the product whose own name and
    #    category explicitly match the request.
    best_prod: Optional[Product] = None
    best_prod_score = 0.0
    for prod in db.query(Product).all():
        if not prod.name:
            continue
        score = _product_similarity(combined, prod.name, prod.category or "")
        if score > best_prod_score:
            best_prod_score = score
            best_prod = prod

    if best_prod and best_prod_score >= 0.75:
        return {
            "product_code": best_prod.code,
            "product_name": best_prod.name,
            "sku_code": None,
            "sku": None,
            "confidence": round(min(best_prod_score, 1.0), 2),
        }

    # 3. PricingSku.sku 匹配 (中文友好: 字符 Jaccard, 见 _similarity)。
    #    旧纯 token 法对整词中文如「樱桃木玻璃柜」恒得 0 → 匹配不到玻璃底座/玻璃门等 SKU 变体,
    #    只能回落 AI 就近选一个普通柜。改用 _similarity 后玻璃变体能正确浮出 (2026-07-04)。
    best_sku: Optional[PricingSku] = None
    best_score = 0.0
    for row in db.query(PricingSku).all():
        if not row.sku:
            continue
        score = _similarity(combined, row.sku)
        if score > best_score:
            best_score = score
            best_sku = row

    if best_sku and best_score >= 0.4:
        return _hit(best_sku, db, round(min(best_score, 0.99), 2))

    # 4. Lower-confidence product fallback.
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
        name_score = _product_similarity(
            combined, prod.name or "", prod.category or "",
        )

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

        # Product identity is authoritative.  SKU text remains useful for
        # finishes/variants but is discounted so a combo SKU cannot make an
        # unrelated parent product outrank an explicit product-name match.
        relevance = max(name_score, best_sku_score * 0.82)
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
