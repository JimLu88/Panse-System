"""
千牛窗口：空闲最小化、任务栏恢复 + 标题区 OCR 校验（多实例任务栏图标区分）。
"""

from __future__ import annotations

import ctypes
import time
from collections.abc import Callable

import uiautomation as auto

from apps.core.capture.screen import Rect, ScreenCapture
from apps.core.configs.loader import ShopConfig
from apps.core.ocr.dual_engine import get_dual_ocr_engine

SW_MINIMIZE = 6
SW_RESTORE = 9

LogFn = Callable[[str], None]


def _main_control(shop: ShopConfig) -> auto.WindowControl:
    qn = shop.qianniu
    if qn is None:
        raise RuntimeError("shop 缺少 qianniu 配置")
    return auto.WindowControl(searchDepth=6, NameContains=qn.main_window_name_contains)


def minimize_qianniu_main(shop: ShopConfig) -> bool:
    """
    最小化千牛主窗口（便于仅最小化态下的叮咚提示音）。

    v1.6.0 返回 bool：True=成功调用 ShowWindow(SW_MINIMIZE)；False=找不到窗口或 hwnd 异常。
    旧调用方（不接返回值）行为不变。
    """
    w = _main_control(shop)
    if not w.Exists(0.45):
        return False
    try:
        hwnd = int(w.NativeWindowHandle)
    except (TypeError, ValueError, AttributeError):
        return False
    if not hwnd:
        return False
    try:
        ctypes.windll.user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True
    except Exception:
        return False


def force_hide_offscreen(shop: ShopConfig) -> bool:
    """
    v1.6.0 最小化兜底：把千牛窗口移到屏幕外（left=-2000, top=-2000）。

    用于 SW_MINIMIZE 连续失败时的"硬隐藏"兜底，等同视觉上不见。
    下一次叮咚到达时 bring_to_front 会用 taskbar_click 把窗口拉回正常位置。

    返回 True=成功移出；False=找不到窗口或 SetWindowPos 失败。
    """
    w = _main_control(shop)
    if not w.Exists(0.45):
        return False
    try:
        hwnd = int(w.NativeWindowHandle)
    except (TypeError, ValueError, AttributeError):
        return False
    if not hwnd:
        return False
    try:
        # SetWindowPos 参数：hwnd, hwndInsertAfter=0, x, y, w, h, flags
        # SWP_NOSIZE(0x1) | SWP_NOZORDER(0x4) | SWP_NOACTIVATE(0x10) = 0x15
        ok = bool(
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, -2000, -2000, 0, 0, 0x15
            )
        )
        return ok
    except Exception:
        return False


def get_seconds_since_last_user_input() -> float:
    """
    返回距上次鼠标/键盘事件的秒数（Win32 GetLastInputInfo）。

    v1.6.0 用于自动最小化判断：主理人最近 30s 在动鼠标/键盘
    （即使没有买家消息）则跳过最小化，避免打断她在千牛里手操作。

    返回 -1.0 = 调用失败（保守起见调用方应当不跳过）。
    """
    try:
        class _LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return -1.0
        tick_now = int(ctypes.windll.kernel32.GetTickCount())
        # GetTickCount/dwTime 都是 32-bit 毫秒计数，可能溢出（49.7 天回卷一次），取无符号差
        delta_ms = (tick_now - int(lii.dwTime)) & 0xFFFFFFFF
        return float(delta_ms) / 1000.0
    except Exception:
        return -1.0


def _title_ocr_matches_shop(shop: ShopConfig, rect: Rect, log: LogFn) -> bool:
    cap = ScreenCapture()
    img = cap.grab_rgb(rect)
    ocr = get_dual_ocr_engine()
    res = ocr.recognize(img)
    blob = "".join(s.text for s in res.spans if s.text).strip().lower()
    name = (shop.shop_display_name or "").strip().lower()
    code = (shop.shop_code or "").strip().lower()
    if name and name in blob:
        log(f"标题 OCR 命中展示名：{shop.shop_display_name}")
        return True
    if code and code in blob:
        log(f"标题 OCR 命中店铺代码：{shop.shop_code}")
        return True
    log(f"标题 OCR 未命中本店（截取）：{blob[:160]!r}")
    return False


def _foreground_keyboard_nudge() -> None:
    """Alt 键轻敲：部分环境下可放宽 SetForegroundWindow 限制。"""
    VK_MENU = 0x12
    KEYEVENTF_KEYUP = 0x0002
    u = ctypes.windll.user32
    u.keybd_event(VK_MENU, 0, 0, 0)
    u.keybd_event(VK_MENU, 0, 0, KEYEVENTF_KEYUP)


def _is_qianniu_foreground(shop: ShopConfig) -> bool:
    """前台 HWND 为千牛主窗且窗口非最小化、尺寸可见（用户能「看到弹出」）。"""
    try:
        from apps.core.channels.qianniu.win_hwnd import (
            find_qianniu_main_hwnd_best_effort,
            hwnd_is_user_visible,
        )

        fg = int(ctypes.windll.user32.GetForegroundWindow())
        if fg <= 0:
            return False
        if not hwnd_is_user_visible(fg):
            return False

        hwnd_enum = find_qianniu_main_hwnd_best_effort()
        if hwnd_enum and fg == hwnd_enum:
            return True

        qn = shop.qianniu
        if qn is None:
            return False
        w = auto.WindowControl(searchDepth=6, NameContains=qn.main_window_name_contains)
        if not w.Exists(0.15):
            return False
        hwnd_uia = int(w.NativeWindowHandle or 0)
        return hwnd_uia != 0 and fg == hwnd_uia and hwnd_is_user_visible(hwnd_uia)
    except Exception:
        return False


def _foreground_status_line(shop: ShopConfig) -> str:
    """诊断：当前前台 HWND、千牛枚举 HWND、是否可见。"""
    try:
        from apps.core.channels.qianniu.win_hwnd import (
            find_qianniu_main_hwnd_best_effort,
            hwnd_is_user_visible,
        )

        user32 = ctypes.windll.user32
        fg = int(user32.GetForegroundWindow())
        hwnd_qn = find_qianniu_main_hwnd_best_effort()
        title_fg = ""
        if fg > 0:
            n = user32.GetWindowTextLengthW(fg)
            if n > 0:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(fg, buf, n + 1)
                title_fg = (buf.value or "")[:40]
        iconic_qn = bool(hwnd_qn and user32.IsIconic(hwnd_qn))
        return (
            f"置前诊断：前台HWND={fg} 标题≈{title_fg!r} | "
            f"千牛HWND={hwnd_qn} 最小化={iconic_qn} 可见={hwnd_is_user_visible(hwnd_qn or 0)} | "
            f"前台匹配={_is_qianniu_foreground(shop)}"
        )
    except Exception as e:
        return f"置前诊断异常：{e!r}"


def maybe_prepare_window_for_capture(shop: ShopConfig, trigger: str, log: LogFn) -> None:
    """截图前将千牛置前（委托 bring_to_front.prepare_qianniu_for_capture）。"""
    from apps.core.channels.qianniu.bring_to_front import prepare_qianniu_for_capture

    prepare_qianniu_for_capture(shop, trigger, log)
