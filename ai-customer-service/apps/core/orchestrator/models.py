from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping


class ActionPriority(int):
    """
    Lower value means higher priority (compatible with PriorityQueue semantics).
    """


class ActionKind(StrEnum):
    # Minimal core kinds; channel-specific kinds will be added later.
    NOOP = "noop"
    SOOTHE_WAIT = "soothe_wait"
    SEND_TEXT = "send_text"
    SEND_IMAGE = "send_image"
    REACQUIRE_CONTEXT = "reacquire_context"


@dataclass(frozen=True, slots=True)
class ActionItem:
    action_id: str
    source_id: str
    session_id: str
    kind: ActionKind
    payload: Mapping[str, Any] = field(default_factory=dict)
    priority: ActionPriority = ActionPriority(100)
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    deadline_ms: int | None = None
    requires_window_focus: bool = True


class ChannelState(StrEnum):
    IDLE = "Idle"
    CAPTURING = "Capturing"
    DECIDING = "Deciding"
    AUTO_REPLYING = "AutoReplying"
    MANUAL_HOLD = "ManualHold"
    REACQUIRE = "Reacquire"
    ERROR = "Error"


@dataclass(frozen=True, slots=True)
class NewMessageEvent:
    """
    感官层触发：全系统音量峰值（audio_peak）、兜底定时扫屏（sweep_fallback）、人工测试等。
    trigger: audio_peak | sweep_fallback | manual_test
    """

    source_id: str
    session_id: str
    trigger: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChannelStatus:
    source_id: str
    state: ChannelState
    queue_len: int
    manual: bool
    executor_busy: bool
    last_error: str | None = None

