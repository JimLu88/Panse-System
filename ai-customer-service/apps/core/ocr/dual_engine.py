from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from apps.core.ocr.engine_paddle_optional import PaddleOCREngineOptional
from apps.core.ocr.engine_rapid import RapidOCREngine
from apps.core.ocr.models import OCRSpan
from apps.core.ocr.preprocess import preprocess_chat_roi


class OCREngine(Protocol):
    def recognize(self, rgb_img: "np.ndarray") -> list[OCRSpan]: ...


@dataclass(frozen=True, slots=True)
class OCRResult:
    engine: str
    spans: list[OCRSpan]
    avg_confidence: float


def _load_ocr_cfg() -> dict:
    try:
        from pathlib import Path
        import yaml
        p = Path(__file__).parent.parent.parent.parent / "configs" / "query_rewrite.yaml"
        if p.is_file():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            return raw.get("ocr") if isinstance(raw.get("ocr"), dict) else {}
    except Exception:
        pass
    return {}


class DualOCREngine:
    """
    Default: PaddleOCR. Fallback: RapidOCR when empty or avg_conf < threshold.

    engine_mode="best_of_two"（配置 ocr.engine_mode）时同时运行两个引擎，
    取平均置信度更高的结果，适合精度优先场景。

    预处理（ocr.preprocess.enabled=true）在送入任意引擎前统一执行一次。
    """

    def __init__(self, *, fallback_threshold: float = 0.8) -> None:
        cfg = _load_ocr_cfg()
        self._fallback_threshold = float(fallback_threshold)
        self._engine_mode = str(cfg.get("engine_mode") or "fallback").strip().lower()

        rapid_cfg = cfg.get("rapid") if isinstance(cfg.get("rapid"), dict) else {}
        self._rapid = RapidOCREngine(
            text_score=float(rapid_cfg.get("text_score") or 0.5),
            det_limit_side_len=int(rapid_cfg.get("det_limit_side_len") or 1280),
            det_limit_type=str(rapid_cfg.get("det_limit_type") or "max"),
        )

        self._paddle: PaddleOCREngineOptional | None = None
        try:
            self._paddle = PaddleOCREngineOptional()
        except Exception:
            self._paddle = None

        pre_cfg = cfg.get("preprocess") if isinstance(cfg.get("preprocess"), dict) else {}
        self._pre_enabled = bool(pre_cfg.get("enabled", True))
        self._pre_scale = float(pre_cfg.get("scale") or 2.0)
        self._pre_contrast = int(pre_cfg.get("contrast_cutoff") or 1)
        self._pre_sharpen_radius = float(pre_cfg.get("sharpen_radius") or 1.0)
        self._pre_sharpen_percent = int(pre_cfg.get("sharpen_percent") or 150)

    def _preprocess(self, rgb_img: "np.ndarray") -> tuple["np.ndarray", float]:
        """返回 (预处理后图像, 缩放倍率)。"""
        if not self._pre_enabled or self._pre_scale <= 1.0:
            return rgb_img, 1.0
        processed = preprocess_chat_roi(
            rgb_img,
            scale=self._pre_scale,
            contrast_cutoff=self._pre_contrast,
            sharpen_radius=self._pre_sharpen_radius,
            sharpen_percent=self._pre_sharpen_percent,
        )
        return processed, self._pre_scale

    @staticmethod
    def _scale_spans_back(
        spans: list[OCRSpan], scale: float
    ) -> list[OCRSpan]:
        """将预处理放大后的 span 坐标缩回原始图像空间，保证下游 buyer_extract 等
        函数拿到与 roi_height/roi_width 一致的坐标。"""
        if scale <= 1.0:
            return spans
        inv = 1.0 / scale
        return [
            OCRSpan(
                text=s.text,
                confidence=s.confidence,
                bbox=(
                    int(s.bbox[0] * inv),
                    int(s.bbox[1] * inv),
                    int(s.bbox[2] * inv),
                    int(s.bbox[3] * inv),
                ),
            )
            for s in spans
        ]

    def recognize(self, rgb_img: "np.ndarray") -> OCRResult:
        img, scale = self._preprocess(rgb_img)

        if self._engine_mode == "best_of_two" and self._paddle is not None:
            spans_p = self._paddle.recognize(img)
            spans_r = self._rapid.recognize(img)
            avg_p = _avg_conf(spans_p)
            avg_r = _avg_conf(spans_r)
            if avg_p >= avg_r and spans_p:
                return OCRResult(engine="paddle", spans=self._scale_spans_back(spans_p, scale), avg_confidence=avg_p)
            return OCRResult(engine="rapid", spans=self._scale_spans_back(spans_r, scale), avg_confidence=avg_r)

        if self._paddle is not None:
            spans = self._paddle.recognize(img)
            avg = _avg_conf(spans)
            if spans and avg >= self._fallback_threshold:
                return OCRResult(engine="paddle", spans=self._scale_spans_back(spans, scale), avg_confidence=avg)
            spans2 = self._rapid.recognize(img)
            return OCRResult(engine="rapid", spans=self._scale_spans_back(spans2, scale), avg_confidence=_avg_conf(spans2))

        spans = self._rapid.recognize(img)
        return OCRResult(engine="rapid", spans=self._scale_spans_back(spans, scale), avg_confidence=_avg_conf(spans))


def _avg_conf(spans: list[OCRSpan]) -> float:
    if not spans:
        return 0.0
    return sum(s.confidence for s in spans) / float(len(spans))


_dual_lock = threading.Lock()
_dual_singleton: DualOCREngine | None = None


def get_dual_ocr_engine() -> DualOCREngine:
    """
   进程内复用同一引擎实例，避免每次回读重复初始化 Paddle（首次加载可达数十秒，易被误判为卡死）。
    """
    global _dual_singleton
    with _dual_lock:
        if _dual_singleton is None:
            _dual_singleton = DualOCREngine()
        return _dual_singleton

