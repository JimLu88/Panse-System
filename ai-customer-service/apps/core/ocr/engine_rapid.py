from __future__ import annotations

import numpy as np
from rapidocr_onnxruntime import RapidOCR

from apps.core.ocr.models import OCRSpan


class RapidOCREngine:
    def __init__(
        self,
        *,
        text_score: float = 0.5,
        det_limit_side_len: int = 1280,
        det_limit_type: str = "max",
    ) -> None:
        try:
            self._ocr = RapidOCR(
                text_score=text_score,
                det_limit_side_len=det_limit_side_len,
                det_limit_type=det_limit_type,
            )
        except TypeError:
            # 旧版 rapidocr 不接受这些 kwargs，降级到默认初始化
            self._ocr = RapidOCR()

    def recognize(self, rgb_img: "np.ndarray") -> list[OCRSpan]:
        res, _ = self._ocr(rgb_img)
        if not res:
            return []
        out: list[OCRSpan] = []
        for (box, text, score) in res:
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
        # sort top-to-bottom
        out.sort(key=lambda s: (s.bbox[1], s.bbox[0]))
        return out

