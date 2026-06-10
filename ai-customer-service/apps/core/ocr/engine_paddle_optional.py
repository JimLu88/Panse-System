from __future__ import annotations

from typing import Any

import numpy as np

from apps.core.ocr.models import OCRSpan


class PaddleOCREngineOptional:
    """
    Optional PaddleOCR engine.

    We keep it optional because PaddleOCR is heavy; if installed, it can be used as primary engine.
    """

    def __init__(self) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception as e:  # pragma: no cover
            raise RuntimeError("PaddleOCR is not installed") from e

        self._ocr = PaddleOCR(use_angle_cls=True, lang="ch")

    def recognize(self, rgb_img: "np.ndarray") -> list[OCRSpan]:
        # PaddleOCR expects BGR
        bgr = rgb_img[:, :, ::-1]
        res = self._ocr.ocr(bgr, cls=True)  # type: ignore[attr-defined]
        if not res:
            return []
        out: list[OCRSpan] = []
        # res is list of lines, each: [ [box], (text, score) ]
        for line in res[0] if isinstance(res, list) else []:
            try:
                box, (text, score) = line
            except Exception:
                continue
            if not text:
                continue
            try:
                x1 = int(min(p[0] for p in box))
                y1 = int(min(p[1] for p in box))
                x2 = int(max(p[0] for p in box))
                y2 = int(max(p[1] for p in box))
            except Exception:
                x1 = y1 = x2 = y2 = 0
            out.append(OCRSpan(text=str(text), confidence=float(score or 0.0), bbox=(x1, y1, x2, y2)))
        out.sort(key=lambda s: (s.bbox[1], s.bbox[0]))
        return out

