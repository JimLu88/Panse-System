"""
聊天区哨兵：每 N 秒抓取「聊天区 ROI 底部左侧」做像素差分，检测当前选中会话的新买家消息。

与 VisualSentryLoop（监控会话列表）互补：
- VisualSentryLoop 只看左侧会话列表的黄条/像素变化 → 检测「其他会话有新消息」
- ChatAreaSentry 看聊天区底部左侧（买家气泡区）→ 检测「当前选中会话有新消息」

设计要点：
- 只截取 ocr_chat_rect 底部 40% x 左侧 50%（买家气泡区），避免检测到自己发的消息
- 维护 _last_sent_ts：SequentialExecutor 发送文本后回调更新，4 秒内忽略差分（自身回复排除）
- 仅在千牛前台且非最小化时运行，千牛最小化时挂起
"""

from __future__ import annotations

import ctypes
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from apps.core.capture.screen import Rect, ScreenCapture
from apps.core.configs.loader import load_shop_config

OnTrigger = Callable[[], None]
LogFn = Callable[[str], None]


class ChatAreaSentry:
    """
    独立线程，每 ``interval_s`` 秒抓一次聊天区底部左侧，与上一帧做差分。
    命中即调 ``on_trigger``。
    """

    def __init__(
        self,
        shop_cfg_path: Path,
        *,
        interval_s: float = 8.0,
        on_trigger: OnTrigger,
        log: LogFn,
        cooldown_s: float = 6.0,
        diff_threshold: float = 5.0,
        bottom_ratio: float = 0.40,
        left_ratio: float = 0.50,
        self_reply_ignore_s: float = 4.0,
    ) -> None:
        self._shop_cfg_path = Path(shop_cfg_path)
        self._interval = max(2.0, float(interval_s))
        self._cooldown = max(1.0, float(cooldown_s))
        self._diff_threshold = max(1.0, float(diff_threshold))
        self._bottom_ratio = max(0.1, min(0.8, float(bottom_ratio)))
        self._left_ratio = max(0.2, min(0.8, float(left_ratio)))
        self._self_reply_ignore_s = max(0.0, float(self_reply_ignore_s))

        self._on_trigger = on_trigger
        self._log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cap = ScreenCapture()
        self._prev_rgb: np.ndarray | None = None
        self._last_fire = 0.0
        self._last_sent_ts: float = 0.0
        self._warned_no_rect = False
        self._warned_started = False
        self._loop_started_at = 0.0
        # 启动后的建基线期：不触发，先采集帧
        self._arm_seconds = 20.0
        self._consecutive_hits = 0
        self._required_consecutive = 2

    def notify_message_sent(self) -> None:
        """SequentialExecutor 发送文本后调用，标记自身回复时间戳。"""
        self._last_sent_ts = time.monotonic()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._loop_started_at = time.monotonic()
        self._consecutive_hits = 0
        self._prev_rgb = None
        self._warned_started = False
        self._thread = threading.Thread(
            target=self._loop, name="ChatAreaSentry", daemon=True
        )
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=float(join_timeout_s))

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                self._log(f"聊天区哨兵：tick 异常 {e!r}")
            if self._stop.wait(timeout=self._interval):
                break

    def _sub_roi(self, full_rect: Rect) -> Rect:
        """从完整 ocr_chat_rect 计算底部左侧子区域。"""
        h = full_rect.height()
        w = full_rect.width()
        top_offset = int(h * (1.0 - self._bottom_ratio))
        right_limit = int(w * self._left_ratio)
        return Rect(
            left=full_rect.left,
            top=full_rect.top + top_offset,
            right=full_rect.left + right_limit,
            bottom=full_rect.bottom,
        )

    def _tick(self) -> None:
        # 千牛必须在前台且非最小化
        try:
            from apps.core.channels.qianniu.win_hwnd import (
                find_qianniu_main_hwnd_best_effort,
            )

            hwnd = find_qianniu_main_hwnd_best_effort()
            if not hwnd:
                self._consecutive_hits = 0
                return
            user32 = ctypes.windll.user32
            if user32.IsIconic(hwnd):
                self._consecutive_hits = 0
                return
            fg = int(user32.GetForegroundWindow())
            if fg != hwnd:
                self._consecutive_hits = 0
                return
        except Exception:
            self._consecutive_hits = 0
            return

        try:
            shop = load_shop_config(self._shop_cfg_path)
        except Exception as e:
            if not self._warned_no_rect:
                self._warned_no_rect = True
                self._log(f"聊天区哨兵：店铺配置读不到 ({e!r})")
            return

        chat_rect = shop.ocr_chat_rect
        if chat_rect is None or chat_rect.width() < 20 or chat_rect.height() < 20:
            if not self._warned_no_rect:
                self._warned_no_rect = True
                self._log(
                    "聊天区哨兵：未配置 ocr_chat_rect，哨兵将保持空转直到配置出现。"
                )
            return
        self._warned_no_rect = False

        sub = self._sub_roi(chat_rect)
        try:
            curr = self._cap.grab_rgb(sub)
        except Exception as e:
            self._log(f"聊天区哨兵：截图失败 {e!r}")
            return

        prev = self._prev_rgb
        self._prev_rgb = curr

        if not self._warned_started:
            self._warned_started = True
            self._log(
                f"聊天区哨兵：已启用，每 {self._interval:.0f}s 扫聊天区底部左侧 "
                f"({sub.width()}x{sub.height()}) diff阈值={self._diff_threshold:.1f}。"
                f"启动后 {self._arm_seconds:.0f}s 仅建基线。"
            )

        if prev is None or prev.shape != curr.shape:
            self._consecutive_hits = 0
            return

        since_start = time.monotonic() - self._loop_started_at
        if since_start < self._arm_seconds:
            return

        # 自身回复排除：刚发过消息后短暂忽略
        if self._self_reply_ignore_s > 0:
            since_sent = time.monotonic() - self._last_sent_ts
            if self._last_sent_ts > 0 and since_sent < self._self_reply_ignore_s:
                return

        diff = np.mean(np.abs(curr.astype(np.int32) - prev.astype(np.int32)))
        mean_diff = float(diff)

        if mean_diff < self._diff_threshold:
            self._consecutive_hits = 0
            return

        self._consecutive_hits += 1
        if self._consecutive_hits < self._required_consecutive:
            self._log(
                f"聊天区哨兵：疑似变化 ({self._consecutive_hits}/{self._required_consecutive}) "
                f"meanDiff={mean_diff:.1f}（未达连续命中，暂不触发）"
            )
            return

        now = time.monotonic()
        if (now - self._last_fire) < self._cooldown:
            self._consecutive_hits = 0
            return

        self._last_fire = now
        self._consecutive_hits = 0
        self._log(
            f"聊天区哨兵：连续命中，聊天区有新动静（meanDiff={mean_diff:.1f}），触发接待"
        )
        try:
            self._on_trigger()
        except Exception as e:
            self._log(f"聊天区哨兵：on_trigger 回调异常 {e!r}")
