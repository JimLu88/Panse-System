"""后台线程：周期性尝试关闭前台弹窗（需 policy.popup_auto_dismiss）。"""

from __future__ import annotations

import threading
from pathlib import Path

from apps.core.automation.popup_dismiss import dismiss_known_popups_near_foreground


class PopupDismissLoop:
    def __init__(self, *, interval_s: float = 8.0) -> None:
        self._interval_s = float(interval_s)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="PopupDismissLoop", daemon=True)
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=float(join_timeout_s))

    def _run(self) -> None:
        # v1.6.17：本线程要用 UIA(comtypes)，先初始化 COM（防 0x80040155 并发崩溃）
        try:
            from apps.core.automation.uia_guard import init_com_for_thread
            init_com_for_thread()
        except Exception:
            pass
        while not self._stop.wait(self._interval_s):
            try:
                dismiss_known_popups_near_foreground()
            except Exception:
                pass
