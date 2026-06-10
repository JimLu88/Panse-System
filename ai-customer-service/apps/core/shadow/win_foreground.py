"""只读：取前台窗口标题（Win32 User32，无 pywin32 依赖）。"""

from __future__ import annotations

import ctypes
import sys


def get_foreground_window_title() -> str:
    if not sys.platform.startswith("win"):
        return ""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return str(buf.value or "")
