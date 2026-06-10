from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass

import uiautomation as auto

from apps.core.automation.actions.driver import PhysicalDriver
from apps.core.automation.win_input import press_vk, VK_RETURN, VK_DELETE
from apps.core.automation.win_input import press_ctrl_combo
from pathlib import Path

from apps.core.automation.win_clipboard import set_clipboard_text
from apps.core.automation.win_clipboard_image import set_clipboard_dib_from_image_path
from apps.core.capture.screen import Rect


@dataclass(frozen=True, slots=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class QianniuConfig:
    main_window_name_contains: str
    input_box_point: Point
    send_button_point: Point | None = None
    chat_scroll_point: Point | None = None
    # 左侧会话头像列表 ROI（屏幕绝对像素）；配合 unread_session_switch 在未读/提示时先点选会话
    session_list_rect: Rect | None = None
    unread_session_switch: bool = True  # v1.5.5+ 默认开启：自动切到未读会话再回复
    # 空闲超过 N 秒未识别到买家新留言则最小化主窗口（0=关闭）；便于仅最小化时接收叮咚
    idle_auto_minimize_seconds: int = 0
    # 任务栏上本店千牛图标点击坐标（恢复窗口）；叮咚触发时优先点此再 OCR 标题校验
    taskbar_icon_point: Point | None = None
    restore_title_ocr_rect: Rect | None = None


class QianniuDriver(PhysicalDriver):
    """
    Minimal Qianniu physical driver:
    - Bring Qianniu main window to foreground (best-effort).
    - Click input box coordinate.
    - Paste + Enter (or click send button if configured).
    """

    @staticmethod
    def _win32_force_foreground(hwnd: int) -> None:
        """恢复 + AttachThreadInput + SetForegroundWindow + 短暂 TOPMOST，尽量弹出到用户眼前。"""
        if hwnd <= 0:
            return
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        SW_RESTORE = 9
        SW_SHOW = 5
        ASFW_ANY = 0xFFFFFFFF
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        swp_flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW

        try:
            user32.AllowSetForegroundWindow(ASFW_ANY)
        except Exception:
            pass

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)

        fg = user32.GetForegroundWindow()
        if not fg:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            return

        pid_dummy = wintypes.DWORD()
        tid_fg = int(user32.GetWindowThreadProcessId(fg, ctypes.byref(pid_dummy)))
        tid_cur = int(kernel32.GetCurrentThreadId())
        attached = False
        if tid_fg and tid_fg != tid_cur:
            attached = bool(user32.AttachThreadInput(tid_cur, tid_fg, True))
        try:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.BringWindowToTop(hwnd)
            # Alt 键轻敲：解锁 Windows 前台锁，使 SetForegroundWindow 真正生效
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(0x12, 0, 0, 0)           # Alt 按下
            user32.keybd_event(0x12, 0, KEYEVENTF_KEYUP, 0)  # Alt 松开
            user32.SetForegroundWindow(hwnd)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, swp_flags)
            user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, swp_flags)
            try:
                user32.SwitchToThisWindow(hwnd, True)
            except Exception:
                pass
        finally:
            if attached:
                user32.AttachThreadInput(tid_cur, tid_fg, False)

    def __init__(self, cfg: QianniuConfig):
        self._cfg = cfg
        # 拟人化模块的 lazy 缓存（首次 _human_click / paste_text 时加载）
        self._humanize_cache_ready = False
        self._mouse_jitter_cfg = None  # type: ignore[assignment]
        self._typing_cfg = None  # type: ignore[assignment]

    def _ensure_humanize_cache(self) -> None:
        """懒加载 BaseSettings 中的拟人化配置（避免每次点击都读 yaml）。"""
        if self._humanize_cache_ready:
            return
        try:
            from apps.core.configs.base_settings import (
                load_base_settings,
                to_mouse_jitter_settings,
                to_typing_settings,
            )
            bs = load_base_settings()
            self._mouse_jitter_cfg = to_mouse_jitter_settings(bs)
            self._typing_cfg = to_typing_settings(bs)
        except Exception:
            # 配置加载失败：用默认值（mouse_jitter 默认开 3px，typing 默认关）
            from apps.core.automation.human_like.mouse_jitter import MouseJitterSettings
            from apps.core.automation.human_like.typing_real import TypingSettings
            self._mouse_jitter_cfg = MouseJitterSettings()
            self._typing_cfg = TypingSettings()
        self._humanize_cache_ready = True

    def invalidate_humanize_cache(self) -> None:
        """UI 改配置后调一次，下次点击 / 输入会重新读 yaml。"""
        self._humanize_cache_ready = False

    def _human_click(self, x: int, y: int) -> None:
        """
        统一的点击入口：根据 humanize_mouse_jitter_enabled 选择
        click_with_jitter（带 ±N px 正态抖动）或退化到 auto.Click。
        """
        self._ensure_humanize_cache()
        cfg = self._mouse_jitter_cfg
        if cfg is not None and cfg.enabled:
            try:
                from apps.core.automation.human_like.mouse_jitter import click_with_jitter
                click_with_jitter(int(x), int(y), cfg)
                return
            except Exception:
                # 抖动点击失败（如 mouse_event 注入异常）退化到 auto.Click
                pass
        auto.Click(int(x), int(y))

    def _focus_main_window(self) -> None:
        w = auto.WindowControl(searchDepth=6, NameContains=self._cfg.main_window_name_contains)
        if not w.Exists(1.0):
            raise RuntimeError(f"未找到千牛主窗口（标题含『{self._cfg.main_window_name_contains}』，请确认千牛已打开）")
        try:
            hwnd = int(w.NativeWindowHandle or 0)
        except (TypeError, ValueError, AttributeError):
            hwnd = 0

        if hwnd:
            try:
                self._win32_force_foreground(hwnd)
                time.sleep(0.08)
            except Exception:
                pass
        try:
            w.SetActive()
        except Exception:
            pass
        try:
            w.SetFocus()
        except Exception:
            pass
        if hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def focus_main_window(self) -> None:
        """供 Brain 在 OCR 前尽力将千牛置前（与发送前一致）。"""
        self._focus_main_window()

    def _focus_input_box(self) -> None:
        self._human_click(int(self._cfg.input_box_point.x), int(self._cfg.input_box_point.y))

    def scroll_up(self, times: int = 6) -> None:
        self._focus_main_window()
        if self._cfg.chat_scroll_point is not None:
            self._human_click(int(self._cfg.chat_scroll_point.x), int(self._cfg.chat_scroll_point.y))
        for _ in range(max(1, int(times))):
            try:
                auto.WheelUp(12)
            except Exception:
                auto.SendKeys("{PGUP}")
            time.sleep(0.04)

    def _clear_input_box(self) -> None:
        """v1.6.28：发送前清空输入框（全选+删除）。
        修「返回修改」后原草稿还在 → 新文案被追加到后面 的 bug；输入框本就为空时是无操作。"""
        try:
            press_ctrl_combo("a")
            time.sleep(0.03)
            press_vk(VK_DELETE)
            time.sleep(0.03)
        except Exception:
            pass

    def paste_text(self, text: str) -> None:
        self._focus_main_window()
        self._focus_input_box()
        # v1.6.28：先全选删除原有内容（防「返回修改」后在旧草稿后追加），再输入新文案
        self._clear_input_box()
        # 真实打字模式：逐字 SendInput + 错别字回退
        self._ensure_humanize_cache()
        tcfg = self._typing_cfg
        if tcfg is not None and tcfg.enabled:
            try:
                from apps.core.automation.human_like.typing_real import type_text_realistic
                type_text_realistic(text, tcfg)
                return
            except Exception:
                # 打字失败退化到剪贴板粘贴
                pass
        set_clipboard_text(text)
        press_ctrl_combo("v")

    def paste_image_file(self, path: str | Path) -> None:
        self._focus_main_window()
        self._focus_input_box()
        set_clipboard_dib_from_image_path(Path(path))
        press_ctrl_combo("v")

    def press_enter(self) -> None:
        if self._cfg.send_button_point is not None:
            self._human_click(int(self._cfg.send_button_point.x), int(self._cfg.send_button_point.y))
            return
        press_vk(VK_RETURN)

