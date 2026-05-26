"""通用数据校验工具 (业务需求 §6 - 客服后台加密地址检测).

客服后台抓取的订单, 收货地址会被打码 (****/******* 或包含 "隐藏").
导入这种订单时应立即提示运营上传清晰版本, 否则后续工厂排单 / 物流环节都会卡。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 常见打码字符 + 关键字
MASK_CHARS = "*＊·•●◯○⊙＊﹡＊*"
MASK_PATTERN = re.compile(r"[*＊·•●◯○⊙﹡]{3,}|\*{2,}")
ENCRYPTED_KEYWORDS = ("隐藏", "保护", "已加密", "*隐藏*", "已隐藏")


@dataclass
class AddressCheck:
    is_encrypted: bool
    reasons: list[str]
    masked_segments: list[str]


def is_address_encrypted(address: str | None) -> AddressCheck:
    """检测一个地址字符串是否被打码 / 加密了.

    多重判定:
      - 出现连续 3+ 个打码字符
      - 出现 "隐藏" / "保护" / "已加密" 关键字
      - 同一字符串里星号比例超过 15%
    """
    if not address:
        return AddressCheck(is_encrypted=False, reasons=[], masked_segments=[])

    reasons: list[str] = []
    masked: list[str] = []

    masked_runs = MASK_PATTERN.findall(address)
    if masked_runs:
        reasons.append(f"包含 {len(masked_runs)} 段打码")
        masked.extend(masked_runs)

    for kw in ENCRYPTED_KEYWORDS:
        if kw in address:
            reasons.append(f"包含关键字「{kw}」")
            break

    masked_chars = sum(1 for c in address if c in MASK_CHARS)
    if masked_chars and masked_chars / len(address) > 0.15:
        reasons.append(f"打码字符占比 {masked_chars * 100 // len(address)}%")

    return AddressCheck(
        is_encrypted=bool(reasons),
        reasons=reasons,
        masked_segments=masked,
    )


def is_phone_encrypted(phone: str | None) -> bool:
    """简化版手机号打码判定."""
    if not phone:
        return False
    return bool(MASK_PATTERN.search(phone)) or any(k in phone for k in ENCRYPTED_KEYWORDS)
