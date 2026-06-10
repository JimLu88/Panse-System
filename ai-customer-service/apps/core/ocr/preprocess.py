"""
聊天截图 OCR 前图像预处理：2x 放大 + 自动对比度 + 轻度锐化。

使用仅 Pillow（已在 requirements.txt），无需额外依赖。
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps


def preprocess_chat_roi(
    rgb: "np.ndarray",
    *,
    scale: float = 2.0,
    contrast_cutoff: int = 1,
    sharpen_radius: float = 1.0,
    sharpen_percent: int = 150,
) -> "np.ndarray":
    """
    对聊天区截图做增强处理，提升 OCR 识别率：
    - scale=2.0：2x 放大，改善小字体识别（96/120 DPI 场景）
    - autocontrast：拉伸对比度，消除气泡背景色干扰
    - UnsharpMask：轻度锐化，减少模糊字符误读

    如需跳过，传 scale=1.0, contrast_cutoff=0, sharpen_percent=0。
    """
    if rgb is None or rgb.size == 0:
        return rgb

    img = Image.fromarray(rgb.astype("uint8"), mode="RGB")

    if scale > 1.0:
        w, h = img.size
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    if contrast_cutoff > 0:
        img = ImageOps.autocontrast(img, cutoff=contrast_cutoff)

    if sharpen_percent > 0:
        img = img.filter(
            ImageFilter.UnsharpMask(
                radius=sharpen_radius,
                percent=sharpen_percent,
                threshold=0,
            )
        )

    return np.array(img)
