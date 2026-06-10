from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from apps.core.capture.screen import Rect, ScreenCapture
from apps.core.configs.loader import ShopConfig


def effective_chat_rect(shop: ShopConfig) -> Rect:
    """v1.3.98：返回 capture_chat_rgb 实际会使用的 rect（含自动扩展）。

    所有调用方在计算 roi_height/roi_width 给下游 OCR 处理函数
    （如 extract_buyer_message_block）时，**必须用本函数**，
    不能直接用 shop.ocr_chat_rect.height() —— 否则 ROI 实际高度
    和声明高度不一致，下游切割逻辑会误判，把最新消息剪掉。
    """
    rect = shop.ocr_chat_rect
    if rect is None or rect.width() <= 0 or rect.height() <= 0:
        raise RuntimeError("shop 缺少有效的 ocr_chat_rect")

    # 用 send_button_y 自动校正 bottom——发送按钮在输入工具栏中央
    qn = shop.qianniu
    sb = getattr(qn, "send_button_point", None) if qn else None
    if sb is not None and sb.y > 0:
        target_bottom = int(sb.y) - 25
        if rect.bottom < target_bottom - 50:
            return Rect(
                left=rect.left,
                top=rect.top,
                right=rect.right,
                bottom=target_bottom,
            )
    return rect


def capture_chat_rgb(shop: ShopConfig) -> np.ndarray:
    """截取聊天 ROI（RGB ndarray）。bottom 自动扩展见 effective_chat_rect。"""
    rect = effective_chat_rect(shop)
    cap = ScreenCapture()
    return cap.grab_rgb(rect)


def maybe_save_debug_chat_snapshot(
    img: np.ndarray,
    tag: str,
    *,
    out_dir: Path | None = None,
) -> Path | None:
    """query_rewrite debug.save_snapshot=true 时保存 OCR 截图供人工核对。"""
    from apps.core.ai.input_quality_gate import load_debug_snapshot_settings
    from apps.core.runtime_paths import default_sqlite_db_path

    if not load_debug_snapshot_settings().save_snapshot:
        return None
    base = out_dir or (default_sqlite_db_path().parent / "debug")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"chat_{tag}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    try:
        from PIL import Image

        Image.fromarray(img).save(path)
        return path
    except Exception:
        return None
