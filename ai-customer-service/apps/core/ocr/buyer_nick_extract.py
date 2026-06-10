"""
买家昵称提取：截取 ``ocr_buyer_nick_rect`` 区域并 OCR，
返回清洗后的买家昵称文本（去除在线状态词等噪声）。

用于发送前校验——确认当前聊天窗口对应的买家身份与
触发时提取的昵称一致，防止发错人。
"""

from __future__ import annotations

import re

from apps.core.capture.screen import Rect, ScreenCapture
from apps.core.configs.loader import ShopConfig
from apps.core.ocr.dual_engine import get_dual_ocr_engine

# 千牛标题栏常见的非昵称文本（在线状态、平台标识等）
_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"正在输入", re.IGNORECASE),
    re.compile(r"在线|离线|忙碌|离开|隐身", re.IGNORECASE),
    re.compile(r"手机在线|电脑在线", re.IGNORECASE),
    re.compile(r"千牛|淘宝|天猫|旺旺", re.IGNORECASE),
    re.compile(r"客服|客户服务", re.IGNORECASE),
    re.compile(r"[\[\]【】（）(){}]"),
]


def _clean_nickname(raw: str) -> str:
    """去除状态词、括号、多余空白，保留核心昵称。"""
    text = raw.strip()
    for pat in _NOISE_PATTERNS:
        text = pat.sub("", text)
    # 去除首尾标点/空格
    text = re.sub(r"^[\s:：\-—_·.。,，]+", "", text)
    text = re.sub(r"[\s:：\-—_·.。,，]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_buyer_nickname(shop: ShopConfig) -> str | None:
    """
    截取 ``shop.ocr_buyer_nick_rect`` 区域并 OCR，返回清洗后昵称。

    - 未配置 ``ocr_buyer_nick_rect`` → 返回 ``None``（跳过校验）
    - 区域过小/OCR 无结果 → 返回 ``None``
    """
    rect = shop.ocr_buyer_nick_rect
    if rect is None or rect.width() < 10 or rect.height() < 8:
        return None
    try:
        cap = ScreenCapture()
        img = cap.grab_rgb(rect)
        ocr = get_dual_ocr_engine()
        result = ocr.recognize(img)
        raw = " ".join(s.text for s in result.spans if s.text).strip()
        if not raw:
            return None
        cleaned = _clean_nickname(raw)
        return cleaned if cleaned else None
    except Exception:
        return None


def nickname_matches(anchor: str, current: str) -> bool:
    """
    判断锚定昵称与当前昵称是否匹配。

    采用宽松匹配：任一方包含另一方即可（处理 OCR 可能截断或多识别的情况）。
    同时对短昵称（≤2字符）做额外保护——必须完全相等。
    """
    a = anchor.strip()
    c = current.strip()
    if not a or not c:
        return False
    # 短昵称：要求完全匹配
    if len(a) <= 2 or len(c) <= 2:
        return a == c
    # 宽松：子串包含
    return a in c or c in a
