from __future__ import annotations

import hashlib

import numpy as np


def image_sig(rgb_img: "np.ndarray") -> str:
    """
    Cheap content signature for change detection.
    Uses sha256 of downsampled bytes to be robust-ish and fast.
    """
    if rgb_img is None:
        return ""
    # downsample aggressively
    h, w = rgb_img.shape[:2]
    if h <= 0 or w <= 0:
        return ""
    step_y = max(1, int(h / 64))
    step_x = max(1, int(w / 64))
    small = rgb_img[0:h:step_y, 0:w:step_x, :]
    return hashlib.sha256(small.tobytes()).hexdigest()

