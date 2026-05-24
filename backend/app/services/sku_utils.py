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


def sku_suffix(sku_code: Optional[str], product_code: Optional[str] = None) -> Optional[int]:
    """取 SKU 编码尾部 2 位数字后缀。拿不到返回 None。"""
    if not sku_code:
        return None
    s = sku_code.strip()
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
