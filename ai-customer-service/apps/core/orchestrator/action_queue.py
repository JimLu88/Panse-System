from __future__ import annotations

import itertools
import queue
from dataclasses import dataclass
from typing import Optional

from .models import ActionItem, ActionKind, ActionPriority


@dataclass(frozen=True, slots=True)
class _QueuedAction:
    priority: ActionPriority
    seq: int
    item: ActionItem

    def as_tuple(self) -> tuple[int, int, ActionItem]:
        return (int(self.priority), int(self.seq), self.item)


class ActionQueue:
    """
    Global unique queue for all physical actions.

    Contract:
    - Any code can enqueue ActionItem from any thread.
    - Only SequentialExecutor is allowed to dequeue+execute.
    """

    def __init__(self, maxsize: int = 0):
        self._q: queue.PriorityQueue[tuple[int, int, ActionItem]] = queue.PriorityQueue(maxsize=maxsize)
        self._seq = itertools.count(1)

    def put(self, item: ActionItem) -> None:
        seq = next(self._seq)
        qa = _QueuedAction(priority=item.priority, seq=seq, item=item)
        self._q.put(qa.as_tuple())

    def get(self, *, timeout_s: Optional[float] = None) -> ActionItem:
        if timeout_s is None:
            _p, _seq, item = self._q.get()
            return item
        _p, _seq, item = self._q.get(timeout=timeout_s)
        return item

    def wake(self) -> None:
        """
        Wake any blocking consumer quickly.

        Used by SequentialExecutor shutdown to avoid waiting poll timeouts.
        """
        self.put(
            ActionItem(
                action_id="__wake__",
                source_id="__system__",
                session_id="__system__",
                kind=ActionKind.NOOP,
                payload={"reason": "wake"},
                priority=ActionPriority(0),
                requires_window_focus=False,
            )
        )

    def qsize(self) -> int:
        return self._q.qsize()

