from apps.core.ocr.buyer_extract import extract_latest_buyer_line
from apps.core.ocr.models import OCRSpan


def test_extract_skips_timestamp_picks_hi() -> None:
    spans = [
        OCRSpan(text="2026-5-20 01:59:03", confidence=0.9, bbox=(10, 400, 200, 420)),
        OCRSpan(text="hi", confidence=0.9, bbox=(10, 430, 80, 450)),
    ]
    assert extract_latest_buyer_line(spans, roi_height=500, roi_width=400) == "hi"


def test_strip_list_unread_suffix() -> None:
    spans = [
        OCRSpan(text="hi 未读", confidence=0.9, bbox=(10, 430, 80, 450)),
    ]
    assert extract_latest_buyer_line(spans, roi_height=500) == "hi"


def test_extract_empty_when_only_timestamp() -> None:
    spans = [
        OCRSpan(text="2026-5-20 01:59:03", confidence=0.9, bbox=(10, 400, 200, 420)),
    ]
    assert extract_latest_buyer_line(spans, roi_height=500) == ""
