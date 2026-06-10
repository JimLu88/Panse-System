from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from mss import mss


@dataclass(frozen=True, slots=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    def width(self) -> int:
        return int(self.right - self.left)

    def height(self) -> int:
        return int(self.bottom - self.top)

    def as_mss(self) -> dict[str, int]:
        return {"left": int(self.left), "top": int(self.top), "width": self.width(), "height": self.height()}


class ScreenCapture:
    def __init__(self) -> None:
        self._sct = mss()

    def grab_rgb(self, rect: Rect) -> "np.ndarray":
        img = self._sct.grab(rect.as_mss())
        arr = np.array(img)[:, :, :3][:, :, ::-1]  # BGRA->RGB
        return arr

