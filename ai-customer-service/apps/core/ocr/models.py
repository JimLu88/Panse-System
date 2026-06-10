from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OCRSpan:
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x1,y1,x2,y2


@dataclass(frozen=True, slots=True)
class StructuredMessage:
    speaker: str  # buyer/seller/unknown
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]

