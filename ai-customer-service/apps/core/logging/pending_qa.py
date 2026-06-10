"""strategy_takeover / KB 未命中问句归档，供运营补录话术。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from apps.core.runtime_paths import default_sqlite_db_path


def pending_qa_path() -> Path:
    d = default_sqlite_db_path().parent
    d.mkdir(parents=True, exist_ok=True)
    return d / "pending_qa.jsonl"


def append_pending_qa(
    *,
    query: str,
    noise: bool = False,
    reason: str = "",
    session_id: str = "",
) -> None:
    path = pending_qa_path()
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "query": (query or "")[:2000],
        "noise": bool(noise),
        "reason": (reason or "")[:500],
        "session_id": session_id,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
