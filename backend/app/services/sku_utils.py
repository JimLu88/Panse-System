"""SKU 编码工具.

业务规则: SKU 编码 = 产品编码 + 2 位数字后缀。
普通款后缀 11/12/13…; 后缀 >= 90 (含 99/98/97) 视为「定制」款。
阈值可在 system_settings 用 key `custom_sku_suffix_threshold` 调整 (默认 90)。
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

DEFAULT_CUSTOM_THRESHOLD = 90
_TRAILING_DIGITS = re.compile(r"(\d+)\s*$")
# 定制「改」后缀: 在原SKU编码后加「改」(可带连字符), 如 PPS2325005020237-改 / ...改
_GAI_SUFFIX = re.compile(r"[-_ ]*改+\s*$")


def has_gai_suffix(sku_code: Optional[str]) -> bool:
    """SKU 编码是否带定制「改」后缀。"""
    return bool(sku_code) and bool(_GAI_SUFFIX.search(sku_code.strip()))


def strip_custom_suffix(sku_code: Optional[str]) -> Optional[str]:
    """去掉定制「改」后缀, 还原基础SKU编码 (BOM/定价存在基础码下)。

    'PPS2325005020237-改' -> 'PPS2325005020237'; 无改后缀原样返回。
    """
    if not sku_code:
        return sku_code
    base = _GAI_SUFFIX.sub("", sku_code.strip())
    return base or sku_code.strip()


def sku_suffix(sku_code: Optional[str], product_code: Optional[str] = None) -> Optional[int]:
    """取 SKU 编码尾部 2 位数字后缀。拿不到返回 None。"""
    if not sku_code:
        return None
    s = strip_custom_suffix(sku_code)  # 先去「改」后缀再取数字后缀
    if product_code and s.startswith(product_code):
        s = s[len(product_code):]
    m = _TRAILING_DIGITS.search(s)
    if not m:
        return None
    digits = m.group(1)
    return int(digits[-2:]) if len(digits) >= 2 else int(digits)


def is_custom_sku_code(
    sku_code: Optional[str], product_code: Optional[str] = None,
    threshold: int = DEFAULT_CUSTOM_THRESHOLD,
) -> bool:
    # 「改」后缀 = 定制 (方案B 在原规格上改); 或 数字后缀 >= 阈值 (纯定制 99/98/97)
    if has_gai_suffix(sku_code):
        return True
    suf = sku_suffix(sku_code, product_code)
    return suf is not None and suf >= threshold


def get_threshold(db: Session) -> int:
    """从 system_settings 读阈值, 没配则用默认 90。"""
    from app.services import settings_service
    raw = settings_service.get(db, "custom_sku_suffix_threshold", env_fallback=False)
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return DEFAULT_CUSTOM_THRESHOLD
