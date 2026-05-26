"""产品编码生成器 (plan §3)。

编码格式：P + 品牌(2) + 年(2) + 类目(2) + 计数(3) + 月日(MMDD) = 14 位
完整 SKU 编码：上面 14 位 + SKU 编号(2) = 16 位（细分(2) 暂未启用）

例子：PPS26330070320
       └┬┘ ┌─年=26
        │  │  ┌─ 类目=33 (卧室-床)
        │  │  │  ┌─ 计数=007
        │  │  │  │  ┌─ 月日=0320
        P  PS 26 33 007 0320

计数规则：在同一 (品牌, 年, 类目) 维度内累加，每次 +1。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.product import Product

CODE_RE = re.compile(r"^P([A-Z]{2})(\d{2})(\d{2})(\d{3})(\d{4})$")


@dataclass
class ProductCodeParts:
    brand: str  # 2 chars, e.g. "PS"
    year: str  # 2 digits, e.g. "26"
    category: str  # 2 digits, e.g. "33"
    counter: int  # 1-999
    month_day: str  # MMDD


def parse_code(code: str) -> ProductCodeParts | None:
    m = CODE_RE.match((code or "").strip())
    if not m:
        return None
    brand, year, category, counter, monthday = m.groups()
    return ProductCodeParts(
        brand=brand,
        year=year,
        category=category,
        counter=int(counter),
        month_day=monthday,
    )


def format_code(parts: ProductCodeParts) -> str:
    return f"P{parts.brand}{parts.year}{parts.category}{parts.counter:03d}{parts.month_day}"


def next_product_code(
    db: Session,
    *,
    brand: str,
    category: str,
    created_at: date | None = None,
) -> str:
    """根据 (品牌, 年, 类目) 维度分配下一个计数。"""
    brand = brand.upper().strip()
    if len(brand) != 2 or not brand.isalpha():
        raise ValueError("brand must be 2 letters, e.g. PS / FG")
    if len(category) != 2 or not category.isdigit():
        raise ValueError("category must be 2 digits, e.g. 33")
    dt = created_at or date.today()
    year = f"{dt.year % 100:02d}"
    month_day = f"{dt.month:02d}{dt.day:02d}"

    prefix = f"P{brand}{year}{category}"
    pattern = f"{prefix}%"
    rows = db.execute(select(Product.code).where(Product.code.like(pattern))).scalars()
    max_counter = 0
    for code in rows:
        parsed = parse_code(code)
        if parsed and parsed.counter > max_counter:
            max_counter = parsed.counter

    return format_code(
        ProductCodeParts(brand=brand, year=year, category=category, counter=max_counter + 1, month_day=month_day)
    )
