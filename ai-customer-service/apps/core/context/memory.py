from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(slots=True)
class SessionMemory:
    last_context_patch: str = ""
    """Human-side tail after anchor for LLM short-term context."""
    last_ai_snippet: str = ""
    """Substring of last AI reply line(s); used as OCR anchor for reacquire."""


class MemoryStore:
    """
    Minimal in-memory short-term context store.
    Later we can persist to SQLite if needed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._m: dict[str, SessionMemory] = {}

    def set_patch(self, session_id: str, patch: str) -> None:
        with self._lock:
            mem = self._m.get(session_id)
            if mem is None:
                mem = SessionMemory()
                self._m[session_id] = mem
            mem.last_context_patch = patch or ""

    def get_patch(self, session_id: str) -> str:
        with self._lock:
            mem = self._m.get(session_id)
            return "" if mem is None else (mem.last_context_patch or "")

    def set_last_ai_snippet(self, session_id: str, snippet: str) -> None:
        with self._lock:
            mem = self._m.get(session_id)
            if mem is None:
                mem = SessionMemory()
                self._m[session_id] = mem
            mem.last_ai_snippet = (snippet or "").strip()

    def get_last_ai_snippet(self, session_id: str) -> str:
        with self._lock:
            mem = self._m.get(session_id)
            return "" if mem is None else (mem.last_ai_snippet or "")

