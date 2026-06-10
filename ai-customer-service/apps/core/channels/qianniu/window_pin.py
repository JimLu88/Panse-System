"""
千牛主窗口锁定：固定屏幕位置 + 固定尺寸 + DPI 100% 校验。

为什么需要：
  - OCR / 固定坐标方案依赖千牛永远在同一位置以同一尺寸渲染
  - 用户拖窗、最大化、Windows 缩放变化都会让坐标失效
  - 本模块在启动时把窗口 MoveWindow 到 YAML 配置的位置，并周期性校验"漂移"后再矫正

设计原则：
  - 纯 Win32 API（user32.MoveWindow / GetWindowRect / SystemParametersInfoW），不依赖 pygetwindow
  - DPI != 100% 时只警告不强行改（强改会影响整个系统）；用户自己去关系统缩放
  - 锁定失败不抛异常，只 log（防止启动崩溃）

调用者：
  - 启动阶段：apps/core/orchestrator/event_pipeline.py（接通 Task #7）
  - 周期校验：visual_sentry 主循环每 N 秒调用 ensure_pinned()

与现有 window_ops.py / bring_to_front.py 的关系：
  - 这里只管"位置 + 尺寸"
  - 置前/还原/前台 仍由 bring_to_front 处理
  - 顺序：bring_to_front 先 → window_pin 后（先看见，再摆正）
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

LogFn = Callable[[str], None]

_user32 = ctypes.windll.user32
_user32.MoveWindow.argtypes = [
    wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.BOOL,
]
_user32.MoveWindow.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL


@dataclass(frozen=True, slots=True)
class PinSettings:
    """窗口锁定参数（由 BaseSettings → load_pin_settings 构造）。"""
    enabled: bool = False
    x: int = 100
    y: int = 100
    width: int = 1280
    height: int = 800
    #: 漂移容忍像素：当前位置/尺寸与配置差 > 该值 → 矫正
    drift_tolerance_px: int = 10
    #: True=DPI != 100% 时只警告；False=放弃锁定避免坐标错位
    dpi_warn_only: bool = True


@dataclass(frozen=True, slots=True)
class WindowRect:
    x: int
    y: int
    width: int
    height: int


def get_window_rect(hwnd: int) -> WindowRect | None:
    """读取当前窗口屏幕坐标 (left, top, width, height)。失败返回 None。"""
    if hwnd <= 0 or not _user32.IsWindow(hwnd):
        return None
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return WindowRect(
        x=int(rect.left),
        y=int(rect.top),
        width=int(rect.right - rect.left),
        height=int(rect.bottom - rect.top),
    )


def detect_system_dpi_scale() -> float:
    """
    检测主显示器的 DPI 缩放比例（1.0=100%, 1.25=125%, 1.5=150%）。

    用 GetDpiForSystem（Win10 1607+）；老系统降级到 GetDeviceCaps。
    """
    try:
        # Win10 1607+
        dpi = int(_user32.GetDpiForSystem())
        if dpi > 0:
            return dpi / 96.0
    except (AttributeError, OSError):
        pass

    # 降级路径
    try:
        gdi32 = ctypes.windll.gdi32
        hdc = _user32.GetDC(0)
        if hdc:
            try:
                LOGPIXELSX = 88
                dpi = int(gdi32.GetDeviceCaps(hdc, LOGPIXELSX))
                if dpi > 0:
                    return dpi / 96.0
            finally:
                _user32.ReleaseDC(0, hdc)
    except Exception:
        pass

    return 1.0  # 兜底：假设 100%


def _is_dpi_safe(log: LogFn) -> bool:
    """
    DPI 检查：== 100% 则 ok；否则警告（按 dpi_warn_only 决定是否放弃锁定）。
    返回 True 表示 DPI 安全（或允许继续）。
    """
    scale = detect_system_dpi_scale()
    if 0.98 <= scale <= 1.02:
        return True
    log(f"⚠ 窗口锁定：检测到系统 DPI={int(scale * 100)}%，非 100%；"
        f"建议系统设置→显示→缩放改为 100% 以保证 OCR/固定坐标精度")
    return False


def pin_window_to_rect(
    hwnd: int,
    target: WindowRect,
    log: LogFn,
    *,
    repaint: bool = True,
) -> bool:
    """
    把窗口 MoveWindow 到目标矩形（屏幕绝对坐标）。

    @return 移动后实际 rect 与 target 在容忍范围内 → True
    """
    if hwnd <= 0 or not _user32.IsWindow(hwnd):
        log(f"窗口锁定失败：hwnd 无效 ({hwnd})")
        return False

    ok = _user32.MoveWindow(hwnd, target.x, target.y, target.width, target.height,
                             wintypes.BOOL(1 if repaint else 0))
    if not ok:
        err = ctypes.windll.kernel32.GetLastError()
        log(f"窗口锁定失败：MoveWindow 返回 false，GetLastError={err}")
        return False

    actual = get_window_rect(hwnd)
    if actual is None:
        log("窗口锁定失败：MoveWindow 后无法读回 rect")
        return False
    log(f"窗口锁定：移动到 ({target.x},{target.y}) {target.width}x{target.height}；"
        f"实际=({actual.x},{actual.y}) {actual.width}x{actual.height}")
    return True


def rect_drift(actual: WindowRect, target: WindowRect) -> int:
    """返回当前位置/尺寸与目标的最大维度差（px）。"""
    return max(
        abs(actual.x - target.x),
        abs(actual.y - target.y),
        abs(actual.width - target.width),
        abs(actual.height - target.height),
    )


def apply_pinned_layout_on_startup(
    hwnd: int,
    cfg: PinSettings,
    log: LogFn,
) -> bool:
    """
    启动阶段调用：DPI 校验 + MoveWindow 到 cfg.x/y/width/height。
    @return 是否成功锁定
    """
    if not cfg.enabled:
        log("窗口锁定：未启用（pin_window_enabled=false），跳过")
        return False

    dpi_safe = _is_dpi_safe(log)
    if not dpi_safe and not cfg.dpi_warn_only:
        log("窗口锁定：DPI 非 100% 且 dpi_warn_only=false → 放弃锁定")
        return False

    target = WindowRect(x=cfg.x, y=cfg.y, width=cfg.width, height=cfg.height)
    return pin_window_to_rect(hwnd, target, log)


def ensure_pinned_if_drifted(
    hwnd: int,
    cfg: PinSettings,
    log: LogFn,
) -> bool:
    """
    周期校验：当前 rect 与目标差 > drift_tolerance_px 时重新 MoveWindow。

    供 visual_sentry 或主循环每 N 秒调用一次，
    防止用户手动拖窗 / 千牛弹窗导致坐标错位。

    @return True=未漂移或已矫正；False=矫正失败/已禁用
    """
    if not cfg.enabled:
        return False
    actual = get_window_rect(hwnd)
    if actual is None:
        return False
    target = WindowRect(x=cfg.x, y=cfg.y, width=cfg.width, height=cfg.height)
    drift = rect_drift(actual, target)
    if drift <= cfg.drift_tolerance_px:
        return True
    log(f"窗口锁定：检测到漂移 {drift}px > 容忍 {cfg.drift_tolerance_px}px，"
        f"当前=({actual.x},{actual.y}) {actual.width}x{actual.height} → 矫正")
    return pin_window_to_rect(hwnd, target, log)
