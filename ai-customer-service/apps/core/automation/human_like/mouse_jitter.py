"""
鼠标点击位置抖动 + 移动轨迹拟人化。

为什么需要：
  - 千牛风控可监控 MouseEvent 序列：固定像素重复点击 = 机器
  - 真人每次点击不会完全在同一像素，按钮中心 ±3-8px 是常见离散
  - 直线"瞬移"也是机器特征（真人鼠标走 spline 曲线）

提供 3 个层级：
  1. apply_jitter_to_point(x, y)：纯计算，返回抖动后的 (x', y')
  2. click_with_jitter(x, y)：抖动 + 单次 uiautomation.Click
  3. click_with_human_motion(x, y)：抖动 + 沿曲线移动 + 点击

实现说明：
  - level 1/2 用 random.gauss 保证大多数点击落在中心附近
  - level 3 用贝塞尔曲线插值，分 N 步发 MOUSEEVENTF_MOVE
  - 注意：当前不替换 uiautomation.Click，而是提供并行 API；
    上层在 humanize_mouse_jitter_enabled=True 时改调本模块
"""

from __future__ import annotations

import ctypes
import random
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass

LogFn = Callable[[str], None]

_user32 = ctypes.windll.user32

# mouse_event 标志位
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000


@dataclass(frozen=True, slots=True)
class MouseJitterSettings:
    """鼠标点击拟人化参数。"""
    enabled: bool = True
    #: 点击位置抖动半径（px），± 范围
    jitter_px: int = 3
    #: 落点用正态分布；sigma = jitter_px / sigma_divisor（大多数点落在中心附近）
    sigma_divisor: float = 2.5
    #: 是否启用"曲线移动"再点击（成本较高，建议偶尔启用）
    use_curved_motion: bool = False
    #: 曲线移动步数（仅在 use_curved_motion=True 时生效）
    motion_steps: int = 24


def apply_jitter_to_point(
    x: int, y: int,
    cfg: MouseJitterSettings,
) -> tuple[int, int]:
    """
    给目标点加正态分布抖动。

    例如 jitter_px=3，sigma_divisor=2.5 → sigma=1.2，~95% 的点落在 ±2.4px。
    """
    if not cfg.enabled or cfg.jitter_px <= 0:
        return (x, y)
    sigma = max(0.5, cfg.jitter_px / cfg.sigma_divisor)
    dx = int(round(random.gauss(0.0, sigma)))
    dy = int(round(random.gauss(0.0, sigma)))
    # 截断到 ±jitter_px
    dx = max(-cfg.jitter_px, min(cfg.jitter_px, dx))
    dy = max(-cfg.jitter_px, min(cfg.jitter_px, dy))
    return (x + dx, y + dy)


def _set_cursor_pos(x: int, y: int) -> None:
    _user32.SetCursorPos(int(x), int(y))


def _mouse_event(flags: int, x: int = 0, y: int = 0) -> None:
    """调用 mouse_event 注入鼠标事件。"""
    _user32.mouse_event(wintypes.DWORD(flags), wintypes.DWORD(x), wintypes.DWORD(y), 0, 0)


def _click_at(x: int, y: int) -> None:
    """SetCursorPos + LEFTDOWN + LEFTUP。"""
    _set_cursor_pos(x, y)
    time.sleep(random.uniform(0.015, 0.045))
    _mouse_event(MOUSEEVENTF_LEFTDOWN)
    # 真人按下到松开 30-90ms
    time.sleep(random.uniform(0.030, 0.090))
    _mouse_event(MOUSEEVENTF_LEFTUP)


def click_with_jitter(
    x: int, y: int,
    cfg: MouseJitterSettings,
) -> tuple[int, int]:
    """
    在目标点附近按正态分布抖动后单次点击。

    @return 实际点击的坐标（便于上层 log）
    """
    nx, ny = apply_jitter_to_point(x, y, cfg)
    _click_at(nx, ny)
    return (nx, ny)


def _bezier_interpolate(
    src: tuple[float, float],
    dst: tuple[float, float],
    steps: int,
) -> list[tuple[int, int]]:
    """
    生成 src→dst 的简化 2 次贝塞尔曲线插值点（带 1 个随机控制点）。

    控制点在 src/dst 连线中点附近 ±30% 偏移，模拟真人鼠标"略微画弧"。
    """
    sx, sy = src
    dx, dy = dst
    mx, my = (sx + dx) / 2.0, (sy + dy) / 2.0
    span = ((dx - sx) ** 2 + (dy - sy) ** 2) ** 0.5
    offset = max(5.0, span * 0.3)
    cx = mx + random.uniform(-offset, offset)
    cy = my + random.uniform(-offset, offset)
    pts: list[tuple[int, int]] = []
    for i in range(1, steps + 1):
        t = i / steps
        # 二次贝塞尔：B(t) = (1-t)^2 P0 + 2(1-t)t P1 + t^2 P2
        bx = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * dx
        by = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * dy
        pts.append((int(round(bx)), int(round(by))))
    return pts


def _get_cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return (int(pt.x), int(pt.y))


def click_with_human_motion(
    x: int, y: int,
    cfg: MouseJitterSettings,
) -> tuple[int, int]:
    """
    更高保真：抖动 + 曲线移动 + 点击。

    适合"重要操作"场合（发送按钮、切换会话等）；常规点击用 click_with_jitter。
    """
    nx, ny = apply_jitter_to_point(x, y, cfg)
    if cfg.use_curved_motion and cfg.motion_steps >= 2:
        src = _get_cursor_pos()
        path = _bezier_interpolate(src, (nx, ny), cfg.motion_steps)
        for step_x, step_y in path:
            _set_cursor_pos(step_x, step_y)
            time.sleep(random.uniform(0.005, 0.018))
    else:
        _set_cursor_pos(nx, ny)
        time.sleep(random.uniform(0.015, 0.045))
    _mouse_event(MOUSEEVENTF_LEFTDOWN)
    time.sleep(random.uniform(0.030, 0.090))
    _mouse_event(MOUSEEVENTF_LEFTUP)
    return (nx, ny)
