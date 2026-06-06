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


# ───────────────────── 跨品牌归并 (同一实物, 两个品牌) ─────────────────────
# 约定: 同一个实物在两个品牌下用「相同的数字主体」, 只有品牌字母不同
#       (畔色 PPS{X} / 孚格 PFG{X}, X 完全一致; 订单商家编码去品牌后为 P{X})。
# 物理层 (BOM / 库存 / 工厂成本) 按「数字主体」共用一份;
# 商业层 (定价 / 上架) 按品牌各设一份 (PPS{X}.. / PFG{X}.. 可不同价)。
KNOWN_BRAND_CODES = ("PS", "FG")   # P 之后那两位: PS=畔色, FG=孚格


def core_of(code: str | None) -> str | None:
    """取编码的「数字主体」(去掉 P + 品牌字母前缀)。

    PPS26380040225 / PFG26380040225 / P26380040225(订单商家编码) → 26380040225。
    SKU 编码同理 (含尾部 2 位 SKU 号)。同一实物在不同品牌下数字主体相同。
    """
    if not code:
        return None
    core = re.sub(r"^[A-Za-z]+", "", str(code).strip())
    return core or None


def brand_variants(code: str | None) -> set[str]:
    """同一数字主体在各品牌下的等价编码 + 品牌无关(订单)形式。

    给定任意一种写法, 返回所有等价写法 (PPS…/PFG…/P…), 供 BOM/库存/销量
    按物理实物归并匹配。
    """
    if not code:
        return set()
    core = core_of(code)
    if not core:
        return {str(code).strip()}
    out = {str(code).strip(), "P" + core}
    for b in KNOWN_BRAND_CODES:
        out.add("P" + b + core)
    return out


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
