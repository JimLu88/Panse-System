from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Mapping

import queue as py_queue

from apps.core.ai.input_quality_gate import load_executor_busy_timeout_s

from .action_queue import ActionQueue
from .models import ActionItem, ActionKind


ActionHandler = Callable[[ActionItem], None]


@dataclass(frozen=True, slots=True)
class ExecutorStats:
    last_action_id: str | None
    last_kind: str | None
    last_started_at_ms: int | None
    last_finished_at_ms: int | None
    last_error: str | None


class SequentialExecutor:
    """
    The only component allowed to perform physical actions.

    Implementation notes:
    - Single dedicated thread.
    - Pulls ActionItem from ActionQueue and dispatches to handler registry.
    - Any UI/window/keyboard/mouse code MUST be called inside handlers here.
    """

    def __init__(
        self,
        action_queue: ActionQueue,
        handlers: Mapping[ActionKind, ActionHandler],
        *,
        name: str = "SequentialExecutor",
        poll_timeout_s: float = 0.5,
        on_error: Callable[[str], None] | None = None,
    ):
        self._q = action_queue
        self._handlers = dict(handlers)
        self._poll_timeout_s = float(poll_timeout_s)

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

        self._on_error = on_error
        self._lock = threading.Lock()
        self._busy = False
        self._busy_since: float | None = None
        self._busy_timeout_s = load_executor_busy_timeout_s()
        self._stats = ExecutorStats(
            last_action_id=None,
            last_kind=None,
            last_started_at_ms=None,
            last_finished_at_ms=None,
            last_error=None,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self, *, join_timeout_s: float = 2.0) -> None:
        self._stop.set()
        # Wake executor quickly (don't wait poll timeout).
        try:
            self._q.wake()
        except Exception:
            pass
        self._thread.join(timeout=float(join_timeout_s))

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def is_busy(self) -> bool:
        with self._lock:
            return self._busy

    def stats(self) -> ExecutorStats:
        with self._lock:
            return self._stats

    def _set_busy(self, busy: bool) -> None:
        with self._lock:
            self._busy = busy
            if busy:
                self._busy_since = time.monotonic()
            else:
                self._busy_since = None

    def _maybe_reset_stuck_busy(self) -> None:
        with self._lock:
            if not self._busy or self._busy_since is None:
                return
            if self._q.qsize() > 0:
                return
            elapsed = time.monotonic() - self._busy_since
            if elapsed < self._busy_timeout_s:
                return
            self._busy = False
            self._busy_since = None
        msg = (
            f"[执行器] busy 超时复位（>{self._busy_timeout_s:.0f}s 且 queue=0），"
            "已强制清除 busy 标志"
        )
        self._update_stats(last_error="busy_timeout_reset")
        if self._on_error:
            try:
                self._on_error(msg)
            except Exception:
                pass

    def _update_stats(self, **kwargs: Any) -> None:
        with self._lock:
            self._stats = ExecutorStats(
                last_action_id=kwargs.get("last_action_id", self._stats.last_action_id),
                last_kind=kwargs.get("last_kind", self._stats.last_kind),
                last_started_at_ms=kwargs.get("last_started_at_ms", self._stats.last_started_at_ms),
                last_finished_at_ms=kwargs.get("last_finished_at_ms", self._stats.last_finished_at_ms),
                last_error=kwargs.get("last_error", self._stats.last_error),
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout_s=self._poll_timeout_s)
            except py_queue.Empty:
                self._maybe_reset_stuck_busy()
                continue
            except Exception as e:
                self._update_stats(last_error=f"queue_get_failed:{e!r}")
                time.sleep(0.1)
                continue

            self._set_busy(True)
            started = int(time.time() * 1000)
            self._update_stats(last_action_id=item.action_id, last_kind=str(item.kind), last_started_at_ms=started)

            try:
                handler = self._handlers.get(item.kind)
                if handler is None:
                    raise KeyError(f"no handler registered for kind={item.kind}")
                handler(item)
                self._update_stats(last_error=None)
            except Exception as e:
                err_msg = f"[执行器] {item.kind} 操作异常：{e!r}"
                self._update_stats(last_error=repr(e))
                if self._on_error:
                    try:
                        self._on_error(err_msg)
                    except Exception:
                        pass
            finally:
                finished = int(time.time() * 1000)
                self._update_stats(last_finished_at_ms=finished)
                self._set_busy(False)

