"""
PRD 约定的 Paddle 主引擎入口：项目内实现于 engine_paddle_optional.py，
此处提供稳定导入名 `engine_paddle` 供编排与其它模块引用。
"""

from __future__ import annotations

from apps.core.ocr.dual_engine import DualOCREngine, get_dual_ocr_engine

__all__ = ["DualOCREngine", "get_dual_ocr_engine"]
