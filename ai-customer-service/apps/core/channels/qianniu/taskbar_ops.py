"""
任务栏点击恢复千牛：坐标校验、UIA 自动探测纠偏、点击前后 HWND 诊断。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

import uiautomation as auto

from apps.core.channels.qianniu.win_hwnd import (
    find_qianniu_main_hwnd_best_effort,
    hwnd_is_user_visible,
)
from apps.core.configs.loader import ShopConfig
from apps.core.env_patches.dpi_assert import read_windows_dpi_percent
from collections.abc import Callable

LogFn = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ScreenInfo:
    width: int
    height: int
    dpi_percent: int
    taskbar_band_top: int  # Y >= 此值视为在底部任务栏带内


def get_screen_info() -> ScreenInfo:
    user32 = ctypes.windll.user32
    w = int(user32.GetSystemMetrics(0))  # SM_CXSCREEN
    h = int(user32.GetSystemMetrics(1))  # SM_CYSCREEN
    band_h = max(80, h * 12 // 100)
    return ScreenInfo(
        width=w,
        height=h,
        dpi_percent=read_windows_dpi_percent(),
        taskbar_band_top=max(0, h - band_h),
    )


def locate_taskbar_icon_uia() -> tuple[int, int] | None:
    """在任务栏 UIA 树中查找千牛按钮中心（屏幕绝对像素）。"""
    try:
        tray = auto.PaneControl(searchDepth=1, ClassName="Shell_TrayWnd")
        if not tray.Exists(0.5):
            return None
        stack = list(tray.GetChildren())
        while stack:
            node = stack.pop()
            try:
                name = node.Name or ""
                if "千牛" in name or "AliWorkbench" in name or "aliworkbench" in name.lower():
                    rect = node.BoundingRectangle
                    if rect and rect.right > rect.left and rect.bottom > rect.top:
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        return int(cx), int(cy)
            except Exception:
                pass
            try:
                stack.extend(node.GetChildren())
            except Exception:
                pass
    except Exception:
        pass
    return None


@dataclass(frozen=True, slots=True)
class TaskbarPointCheck:
    ok: bool
    configured: bool
    x: int
    y: int
    reasons: tuple[str, ...]


def check_taskbar_point(x: int, y: int, *, screen: ScreenInfo | None = None) -> TaskbarPointCheck:
    """校验 YAML 中的任务栏坐标是否合理（未配置 / 越界 / 不在任务栏带）。"""
    reasons: list[str] = []
    if x == 0 and y == 0:
        return TaskbarPointCheck(False, False, x, y, ("未配置（x,y 均为 0）",))
    if x <= 10 and y <= 10:
        reasons.append(f"坐标过小 ({x},{y})，疑似占位未校准")

    scr = screen or get_screen_info()
    if x < 0 or y < 0 or x >= scr.width or y >= scr.height:
        reasons.append(
            f"坐标 ({x},{y}) 超出当前屏幕 {scr.width}×{scr.height}（可能换显示器或改过缩放后未重校）"
        )
    elif y < scr.taskbar_band_top:
        reasons.append(
            f"坐标 Y={y} 不在屏幕底部任务栏区域（本机任务栏约在 Y≥{scr.taskbar_band_top}，"
            f"屏幕高={scr.height}）；常见原因：点到了聊天区/桌面，或 DPI 缩放后坐标失效"
        )

    ok = len(reasons) == 0
    return TaskbarPointCheck(ok, True, x, y, tuple(reasons))


def _hwnd_brief(hwnd: int | None) -> str:
    if not hwnd:
        return "千牛HWND=无"
    user32 = ctypes.windll.user32
    iconic = bool(user32.IsIconic(hwnd))
    vis = bool(user32.IsWindowVisible(hwnd))
    user_vis = hwnd_is_user_visible(hwnd)
    return f"HWND={hwnd} 最小化={iconic} 可见={vis} 用户可见={user_vis}"


def _distance(px: int, py: int, ux: int, uy: int) -> float:
    return ((px - ux) ** 2 + (py - uy) ** 2) ** 0.5


def click_taskbar_restore(shop: ShopConfig, log: LogFn) -> bool:
    """
    点击任务栏恢复千牛。返回 True 表示点击后千牛已非最小化且用户可见。
    会记录：屏幕/DPI、坐标校验、与 UIA 探测偏差、每次点击前后 HWND 状态。
    """
    qn = shop.qianniu
    if qn is None:
        log("任务栏：无 qianniu 配置，跳过")
        return False

    scr = get_screen_info()
    log(
        f"任务栏环境：屏幕={scr.width}×{scr.height} 系统缩放={scr.dpi_percent}% "
        f"任务栏带 Y≥{scr.taskbar_band_top}"
    )

    tp = qn.taskbar_icon_point
    cfg_x = int(tp.x) if tp is not None else 0
    cfg_y = int(tp.y) if tp is not None else 0
    check = check_taskbar_point(cfg_x, cfg_y, screen=scr)

    uia_pt = locate_taskbar_icon_uia()
    if uia_pt:
        log(f"任务栏 UIA 自动探测千牛图标中心≈({uia_pt[0]},{uia_pt[1]})")
        if check.configured:
            dist = _distance(cfg_x, cfg_y, uia_pt[0], uia_pt[1])
            if dist > 60:
                log(
                    f"⚠ 任务栏坐标偏差较大：YAML=({cfg_x},{cfg_y}) 与 UIA 相差约 {dist:.0f}px，"
                    f"建议打开「千牛屏幕坐标校准」重新录任务栏图标"
                )
            elif dist > 25:
                log(f"任务栏坐标与 UIA 相差约 {dist:.0f}px（可接受，若仍无法弹出请重校）")
    else:
        log("任务栏 UIA：未在任务栏找到名为「千牛」的按钮（可能未固定到任务栏）")

    if not check.ok:
        for r in check.reasons:
            log(f"⚠ 任务栏坐标校验：{r}")
        if uia_pt:
            log(f"将改用 UIA 探测坐标 ({uia_pt[0]},{uia_pt[1]}) 点击（忽略 YAML 中可能错误的坐标）")
            click_pts: list[tuple[int, int, str]] = [(uia_pt[0], uia_pt[1], "UIA探测")]
        else:
            log("无法点击任务栏：坐标无效且 UIA 未找到千牛图标")
            return False
    else:
        click_pts = [(cfg_x, cfg_y, "YAML配置")]
        if uia_pt and _distance(cfg_x, cfg_y, uia_pt[0], uia_pt[1]) > 60:
            click_pts.append((uia_pt[0], uia_pt[1], "UIA纠偏"))

    hwnd_before = find_qianniu_main_hwnd_best_effort()
    log(f"任务栏点击前：{_hwnd_brief(hwnd_before)}")

    restored = False
    for x, y, label in click_pts:
        log(f"任务栏点击 [{label}] 屏幕坐标 ({x},{y}) …")
        try:
            auto.Click(int(x), int(y))
        except Exception as e:
            log(f"任务栏点击 [{label}] 失败：{e!r}")
            continue
        time.sleep(0.55)
        hwnd_after = find_qianniu_main_hwnd_best_effort()
        log(f"任务栏点击后 [{label}]：{_hwnd_brief(hwnd_after)}")
        if hwnd_after and hwnd_is_user_visible(hwnd_after):
            log(f"任务栏点击 [{label}]：千牛窗口已恢复为可见 ✓")
            restored = True
            break
        if hwnd_after and hwnd_before:
            user32 = ctypes.windll.user32
            was_iconic = user32.IsIconic(hwnd_before)
            now_iconic = user32.IsIconic(hwnd_after)
            if was_iconic and not now_iconic:
                log(f"任务栏点击 [{label}]：已取消最小化，但窗口尺寸/前台仍异常，继续尝试…")
        log(
            f"任务栏点击 [{label}]：千牛仍未可见（可能点偏：实际点到其它图标/空白区），"
            f"请目视确认光标是否落在千牛任务栏图标上"
        )

    if restored and qn.restore_title_ocr_rect is not None:
        rt = qn.restore_title_ocr_rect
        if rt.right > rt.left and rt.bottom > rt.top:
            from apps.core.channels.qianniu.window_ops import _title_ocr_matches_shop

            if _title_ocr_matches_shop(shop, rt, log):
                log("任务栏恢复后标题 OCR 已确认本店 ✓")
            else:
                log(
                    "⚠ 任务栏恢复后标题 OCR 未认出本店：可能点到了其它店铺的千牛实例，"
                    "请校准「恢复后标题栏 OCR」矩形或任务栏图标坐标"
                )

    return restored
