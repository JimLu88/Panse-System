from __future__ import annotations

import time
from dataclasses import dataclass

from apps.core.capture.screen import ScreenCapture
from apps.core.channels.qianniu.driver import QianniuDriver
from apps.core.configs.loader import ShopConfig
from apps.core.ocr.dual_engine import get_dual_ocr_engine
from apps.core.ocr.models import OCRSpan


@dataclass(frozen=True, slots=True)
class ReacquireResult:
    engine: str
    avg_conf: float
    full_text: str
    """Original OCR lines joined (for debug)."""
    patch_text: str
    """Text after anchor / human tail for LLM short memory."""
    anchor_matched: bool


def _spans_to_lines(spans: list[OCRSpan]) -> list[str]:
    return [s.text.strip() for s in spans if s.text and s.text.strip()]


def extract_patch_after_anchor(lines: list[str], *, last_ai_snippet: str | None) -> tuple[str, bool]:
    """
    Find the last line containing last_ai_snippet; patch = lines after that.
    If no anchor, use last few lines as recent tail (MVP fallback).
    """
    if not lines:
        return "", False
    anchor = (last_ai_snippet or "").strip()
    if anchor:
        last_idx = -1
        for i, line in enumerate(lines):
            if anchor in line:
                last_idx = i
        if last_idx >= 0:
            tail = lines[last_idx + 1 :]
            return "\n".join(tail).strip(), True
    tail = lines[-6:] if len(lines) > 6 else lines
    return "\n".join(tail).strip(), False


def run_reacquire_physical(
    shop: ShopConfig,
    qn: QianniuDriver,
    *,
    scroll_times: int = 8,
    last_ai_snippet: str | None = None,
    settle_s: float = 0.35,
) -> ReacquireResult:
    """
    Must run only on SequentialExecutor thread (physical scroll + capture).

    Sequence: scroll up -> wait -> OCR chat ROI -> extract patch after AI anchor.
    """
    if shop.qianniu is None:
        raise RuntimeError("shop missing qianniu config")
    if shop.ocr_chat_rect is None or shop.ocr_chat_rect.width() <= 0 or shop.ocr_chat_rect.height() <= 0:
        raise RuntimeError("shop missing valid ocr_chat_rect")

    qn.scroll_up(times=max(1, int(scroll_times)))
    time.sleep(float(settle_s))

    cap = ScreenCapture()
    img = cap.grab_rgb(shop.ocr_chat_rect)
    result = get_dual_ocr_engine().recognize(img)
    lines = _spans_to_lines(result.spans)
    full_text = "\n".join(lines)
    patch_text, anchor_ok = extract_patch_after_anchor(lines, last_ai_snippet=last_ai_snippet)

    return ReacquireResult(
        engine=result.engine,
        avg_conf=result.avg_confidence,
        full_text=full_text,
        patch_text=patch_text,
        anchor_matched=anchor_ok,
    )


def reacquire_context_from_chat(shop: ShopConfig, *, scroll_times: int = 6) -> ReacquireResult:
    """
    Deprecated path: OCR only without physical scroll (UI thread).
    Prefer run_reacquire_physical via executor.
    """
    if shop.ocr_chat_rect is None or shop.ocr_chat_rect.width() <= 0 or shop.ocr_chat_rect.height() <= 0:
        raise RuntimeError("shop missing valid ocr_chat_rect")
    cap = ScreenCapture()
    img = cap.grab_rgb(shop.ocr_chat_rect)
    result = get_dual_ocr_engine().recognize(img)
    lines = _spans_to_lines(result.spans)
    full_text = "\n".join(lines)
    patch_text, anchor_ok = extract_patch_after_anchor(lines, last_ai_snippet=None)
    return ReacquireResult(
        engine=result.engine,
        avg_conf=result.avg_confidence,
        full_text=full_text,
        patch_text=patch_text,
        anchor_matched=anchor_ok,
    )
