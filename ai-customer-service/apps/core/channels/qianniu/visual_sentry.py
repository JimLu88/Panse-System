"""
视觉哨兵：每 N 秒主动抓取「左侧会话列表 ROI」做像素差分；命中则发 trigger=visual_scan 事件。

存在意义：不依赖系统音量，作为听觉流水线的「平行触发源」。
当 pycaw 在某些机器/声卡上读不到千牛 Meter、或叮咚走 PID=0 通知声道而 pycaw
无法捕获时，视觉哨兵仍能在客户进会话后几秒内主动触发接待。

判定阈值刻意比 maybe_switch_unread_session 中「audio_peak 路径」更保守一些
（哨兵每隔几秒打一次自检，不能太敏感，否则会跟 audio_peak 重叠触发）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from apps.core.capture.screen import ScreenCapture
from apps.core.channels.qianniu.session_list_unread import _row_sums, _yellow_mask
from apps.core.configs.loader import load_shop_config

OnTrigger = Callable[[], None]
LogFn = Callable[[str], None]


class VisualSentryLoop:
    """
    独立线程，每 ``interval_s`` 秒抓一次会话列表 ROI，与上一帧做差分。
    命中即调 ``on_trigger``。短窗口内（cooldown_s）不重复触发。

    设计取舍：
    - 由调用方传入 ``shop_cfg_path``（与 PipelineOrchestrator 一致），
      每次循环都重新读 YAML —— 用户在界面里改坐标后无需重启 Brain。
    - 失败/异常不影响后续 tick；ROI 未配置时安静空转（只在第一次提示一次）。
    """

    def __init__(
        self,
        shop_cfg_path: Path,
        *,
        interval_s: float,
        on_trigger: OnTrigger,
        log: LogFn,
        cooldown_s: float = 2.5,
    ) -> None:
        self._shop_cfg_path = Path(shop_cfg_path)
        self._interval = max(1.0, float(interval_s))
        self._cooldown = max(0.5, float(cooldown_s))
        self._on_trigger = on_trigger
        self._log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cap = ScreenCapture()
        self._prev_rgb: np.ndarray | None = None
        self._last_fire = 0.0
        self._warned_no_rect = False
        self._warned_started = False
        self._first_frame_at = 0.0
        self._loop_started_at = 0.0
        self._consecutive_hits = 0
        # 托管刚启动时列表 UI 仍在渲染/选中态，禁止立即触发接待
        self._arm_seconds = 50.0
        self._required_consecutive = 2

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._loop_started_at = time.monotonic()
        self._consecutive_hits = 0
        self._prev_rgb = None
        self._first_frame_at = 0.0
        self._warned_started = False
        self._thread = threading.Thread(target=self._loop, name="VisualSentry", daemon=True)
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
                self._log(f"视觉哨兵：tick 异常 {e!r}")
            if self._stop.wait(timeout=self._interval):
                break

    def _tick(self) -> None:
        # v1.5.x 软附加：扫描前先矫正千牛窗口位置（如果开了窗口锁定）。
        # 不耗 OCR / 不抢前台，只 GetWindowRect 一次 + 必要时 MoveWindow 一次。
        # 视觉哨兵每 4-8s 才 tick 一次，频率不高，且 ensure_pinned_if_drifted 有「漂移容忍」阈值。
        # 任何异常都吞掉，不影响主扫描流程。
        try:
            from apps.core.configs.base_settings import (
                load_base_settings, to_pin_settings,
            )
            from apps.core.channels.qianniu.win_hwnd import (
                find_qianniu_main_hwnd_best_effort,
            )
            from apps.core.channels.qianniu.window_pin import (
                ensure_pinned_if_drifted,
            )
            pin_cfg = to_pin_settings(load_base_settings())
            if pin_cfg.enabled:
                hwnd = find_qianniu_main_hwnd_best_effort() or 0
                if hwnd > 0:
                    ensure_pinned_if_drifted(hwnd, pin_cfg, self._log)
        except Exception:
            # 软附加，绝不影响视觉哨兵主流程
            pass

        try:
            shop = load_shop_config(self._shop_cfg_path)
        except Exception as e:
            if not self._warned_no_rect:
                self._warned_no_rect = True
                self._log(f"视觉哨兵：店铺配置读不到，已暂停哨兵 ({e!r})")
            return

        qn = shop.qianniu
        if qn is None:
            return
        rect = qn.session_list_rect
        if rect is None or rect.width() < 8 or rect.height() < 8:
            if not self._warned_no_rect:
                self._warned_no_rect = True
                self._log(
                    "视觉哨兵：未配置 session_list_rect（左侧会话列表 ROI），"
                    "请用「千牛屏幕坐标校准」录入。哨兵将保持空转直到配置出现。"
                )
            return
        self._warned_no_rect = False

        try:
            curr = self._cap.grab_rgb(rect)
        except Exception as e:
            self._log(f"视觉哨兵：会话列表截图失败 {e!r}")
            return

        prev = self._prev_rgb
        self._prev_rgb = curr

        if not self._warned_started:
            self._warned_started = True
            self._first_frame_at = time.monotonic()
            self._log(
                f"视觉哨兵：已启用，每 {self._interval:.0f}s 自扫会话列表 ROI "
                f"({rect.width()}x{rect.height()})。"
                f"启动后 {self._arm_seconds:.0f}s 内仅建基线不触发；"
                f"之后需连续 {self._required_consecutive} 次命中才触发（防静止误触）。"
            )

        if prev is None or prev.shape != curr.shape:
            self._consecutive_hits = 0
            return

        since_start = time.monotonic() - self._loop_started_at
        if since_start < self._arm_seconds:
            return

        # 首帧差分后额外等待，避免 UI 渐入造成 meanDiff 虚高
        if time.monotonic() - self._first_frame_at < max(self._interval * 3.0, 12.0):
            return

        diff = np.mean(np.abs(curr.astype(np.int32) - prev.astype(np.int32)), axis=2)
        mean_diff = float(diff.mean())
        yel = _yellow_mask(curr)
        yel_prev = _yellow_mask(prev)
        novel = yel & ~yel_prev
        novel_rows_max = float(_row_sums(novel).max()) if novel.size else 0.0
        yel_grow = float(_row_sums(yel).max()) - float(_row_sums(yel_prev).max())
        spike_rows_max = float(_row_sums(diff > 18).max()) if diff.size else 0.0

        # 须像「新未读黄条」：黄色新增足够大，且伴随明显差分（静止界面不触发）
        has_unread_signal = novel_rows_max >= 18.0 or yel_grow >= 22.0
        has_motion = mean_diff >= 5.5 or spike_rows_max >= 28.0
        hit = has_unread_signal and has_motion
        if not hit:
            self._consecutive_hits = 0
            return

        self._consecutive_hits += 1
        if self._consecutive_hits < self._required_consecutive:
            self._log(
                f"视觉哨兵：疑似变化 ({self._consecutive_hits}/{self._required_consecutive}) "
                f"meanDiff={mean_diff:.1f} novel={novel_rows_max:.0f} yelGrow={yel_grow:.0f} "
                f"（未达连续命中，暂不触发）"
            )
            return

        now = time.monotonic()
        if (now - self._last_fire) < self._cooldown:
            self._consecutive_hits = 0
            return
        self._last_fire = now
        self._consecutive_hits = 0
        self._log(
            f"视觉哨兵：连续命中，确认会话列表有新动静（meanDiff={mean_diff:.1f} "
            f"novel={novel_rows_max:.0f} yelGrow={yel_grow:.0f} spike={spike_rows_max:.0f}），触发接待"
        )
        try:
            self._on_trigger()
        except Exception as e:
            self._log(f"视觉哨兵：on_trigger 回调异常 {e!r}")
