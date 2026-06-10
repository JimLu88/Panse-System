from __future__ import annotations

import random
import time


def sleep_range(min_s: float, max_s: float) -> None:
    time.sleep(random.uniform(float(min_s), float(max_s)))


def confirm_pause_before_send() -> None:
    # 0.5~1.2s random pause before clicking send / pressing Enter
    sleep_range(0.5, 1.2)


def per_chars_delay(chars: int) -> None:
    """
    PRD rule:
    - every 10 chars delay 0.8~1.5s
    - between sentences delay 1.0~2.0s (handled elsewhere)
    """
    if chars <= 0:
        return
    blocks = max(1, int((chars + 9) / 10))
    for _ in range(blocks):
        sleep_range(0.8, 1.5)

