"""
Shadow 观测模式：暂停 Brain；只记录前台窗口切换与（可选）鼠标点击，不写任何模拟操作。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apps.core.runtime_paths import default_shadow_actions_jsonl
from apps.core.shadow.win_foreground import get_foreground_window_title


class ShadowObserver:
    """
    observation：轮询前台窗口标题；可选 pynput 鼠标点击坐标。
    退出时刷 JSONL，并可触发 EvolveEngine（无 UIAutomation）。
    """

    def __init__(
        self,
        *,
        session_id: str,
        log: Callable[[str], None],
    ) -> None:
        self._session_id = (session_id or "").strip() or "_session"
        self._log = log
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._last_title = ""
        self._mouse_listener: Any = None

    def _append(self, kind: str, payload: dict[str, Any]) -> None:
        ev = {
            "ts": time.time(),
            "session_id": self._session_id,
            "type": kind,
            "payload": payload,
        }
        with self._lock:
            self._events.append(ev)

    def _poll_loop(self) -> None:
        while not self._stop.wait(2.0):
            try:
                title = get_foreground_window_title()
            except Exception:
                title = ""
            if title != self._last_title:
                self._last_title = title
                self._append("window_focus", {"title": title[:500]})

    def _try_start_mouse(self) -> None:
        try:
            from pynput import mouse
        except ImportError:
            self._log("Shadow：未安装 pynput，跳过鼠标点击记录")
            return

        def on_click(_x: float, _y: float, button, pressed: bool) -> None:
            if not pressed:
                return
            self._append(
                "mouse_click",
                {"x": float(_x), "y": float(_y), "button": str(button)},
            )

        try:
            self._mouse_listener = mouse.Listener(on_click=on_click)
            self._mouse_listener.start()
        except Exception as e:
            self._log(f"Shadow：鼠标监听启动失败：{e!r}")

    def _stop_mouse(self) -> None:
        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None

    def _flush_jsonl(self) -> None:
        p: Path = default_shadow_actions_jsonl()
        p.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            chunk = list(self._events)
            self._events.clear()
        if not chunk:
            return
        try:
            with p.open("a", encoding="utf-8") as fh:
                for ev in chunk:
                    fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except OSError as e:
            self._log(f"Shadow：写入行为日志失败：{e!r}")

    def enter(self, *, pause_brain: Callable[[], None]) -> None:
        pause_brain()
        self._append("observation_enter", {})
        self._stop.clear()
        self._last_title = ""
        self._poll_thread = threading.Thread(target=self._poll_loop, name="ShadowPoll", daemon=True)
        self._poll_thread.start()
        self._try_start_mouse()
        self._log("Shadow：已进入观测模式（自动回复已暂停）")

    def exit_and_evolve(
        self,
        *,
        resume_brain: Callable[[], None],
        settings: Any | None,
        customer_scene_excerpt: str,
    ) -> None:
        self._append("observation_exit", {})
        self._stop.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
            self._poll_thread = None
        self._stop_mouse()
        with self._lock:
            snap = list(self._events)
        self._flush_jsonl()
        try:
            resume_brain()
        except Exception as e:
            self._log(f"Shadow：恢复 Brain 时异常：{e!r}")
        self._log("Shadow：已退出观测模式")
        if settings is not None and snap:
            try:
                from apps.core.shadow.evolve import EvolveEngine

                EvolveEngine().analyze_and_merge_rules(
                    settings=settings,
                    action_events=snap,
                    customer_scene_excerpt=customer_scene_excerpt,
                    log=self._log,
                )
            except Exception as e:
                self._log(f"Shadow：演化合并失败：{e!r}")
