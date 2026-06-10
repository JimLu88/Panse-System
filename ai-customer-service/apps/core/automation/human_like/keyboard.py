from __future__ import annotations

import random
import time


def keystroke_delay() -> None:
    # PRD rule: random.uniform(0.1, 0.3)
    time.sleep(random.uniform(0.1, 0.3))

