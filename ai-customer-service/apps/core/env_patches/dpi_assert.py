from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes


def _get_dpi_scale_percent() -> int:
    """
    Returns Windows display scaling percent for the primary monitor.

    必须在 64 位进程里为 GDI 调用声明指针宽度句柄，否则会 ctypes OverflowError。
    """
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    user32.SetProcessDPIAware.restype = wintypes.BOOL

    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC

    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
    gdi32.GetDeviceCaps.restype = ctypes.c_int

    # Ensure the process is DPI aware to get real pixels.
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

    hwnd_desktop = wintypes.HWND(0)
    hdc = user32.GetDC(hwnd_desktop)
    if not hdc:
        raise RuntimeError("GetDC failed")
    try:
        LOGPIXELSX = 88
        dpi_x = int(gdi32.GetDeviceCaps(hdc, LOGPIXELSX))
    finally:
        user32.ReleaseDC(hwnd_desktop, hdc)

    # 96 DPI == 100%
    return int(round(dpi_x * 100 / 96))


def read_windows_dpi_percent() -> int:
    """
    当前主显示器缩放百分比（96 DPI = 100%）。
    非 Windows 返回 100；读取失败时返回 100，避免界面启动崩溃。
    """
    if sys.platform != "win32":
        return 100
    try:
        return _get_dpi_scale_percent()
    except Exception:
        return 100


def assert_dpi_100() -> None:
    """兼容旧调用点；不再因系统缩放阻止启动（用户可保持习惯的 DPI）。"""
    return

