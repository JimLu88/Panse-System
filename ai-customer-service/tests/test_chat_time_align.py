from datetime import datetime

from apps.core.ocr import chat_time_align as cta
from apps.core.ocr.models import OCRSpan


def test_parse_chat_timestamp() -> None:
    dt = cta.parse_chat_timestamp("2026-5-17 20:46:54")
    assert dt == datetime(2026, 5, 17, 20, 46, 54)


def test_pick_closest_to_now_not_bottom_y() -> None:
    """中间卖家「hi 已读」时间不应盖过最新买家消息时间。"""
    now = datetime(2026, 5, 20, 2, 38, 45)
    spans = [
        OCRSpan(text="2026-5-20 01:59:03", confidence=0.9, bbox=(10, 200, 200, 220)),
        OCRSpan(text="2026-5-20 01:59:58", confidence=0.9, bbox=(10, 280, 200, 300)),
        OCRSpan(text="2026-5-20 02:38:57", confidence=0.9, bbox=(10, 400, 200, 420)),
    ]
    best = cta.extract_best_chat_timestamp_for_alignment(
        spans, roi_height=500, now=now
    )
    assert best == datetime(2026, 5, 20, 2, 38, 57)
    align = cta.assess_chat_time_alignment_from_spans(
        spans, roi_height=500, now=now, max_skew_minutes=5
    )
    assert align.ok
    assert align.candidate_count == 3


def test_assess_over_skew_when_all_stale() -> None:
    now = datetime(2026, 5, 19, 1, 37, 0)
    spans = [
        OCRSpan(text="2026-5-17 20:46:54", confidence=0.9, bbox=(10, 400, 200, 420)),
    ]
    align = cta.assess_chat_time_alignment_from_spans(
        spans,
        roi_height=500,
        now=now,
        max_skew_minutes=5,
        stale_discard_minutes=15,
    )
    assert not align.ok
    assert align.stale_discard


def test_capture_skew_discards_stale_frame() -> None:
    capture = datetime(2026, 5, 20, 2, 0, 0)
    chat = datetime(2026, 5, 18, 20, 0, 0)
    align = cta.assess_chat_time_alignment(
        chat,
        now=datetime(2026, 5, 20, 2, 0, 0),
        capture_ts=capture,
        stale_discard_minutes=15,
        capture_skew_discard_minutes=15,
    )
    assert not align.ok
    assert align.stale_discard
