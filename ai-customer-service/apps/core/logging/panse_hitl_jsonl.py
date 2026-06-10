"""
HITL / 微调语料静默采集：人工介入或成功命中话术时追加 JSONL。

格式优先兼容对比学习：{"query","pos","neg","meta"}；禁止写入 HyDE 类假设文档。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from apps.core.runtime_paths import default_panse_embedding_finetune_jsonl

_lock = threading.Lock()


def append_panse_hitl_record(
    *,
    query: str,
    pos: list[str],
    neg: list[str],
    meta: dict[str, Any] | None = None,
) -> None:
    """线程安全追加一行 JSONL。"""
    path = default_panse_embedding_finetune_jsonl()
    path.parent.mkdir(parents=True, exist_ok=True)
    rec: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "query": (query or "").strip(),
        "pos": [str(x).strip() for x in (pos or []) if str(x).strip()],
        "neg": [str(x).strip() for x in (neg or []) if str(x).strip()],
    }
    if meta:
        rec["meta"] = meta
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _lock:
        with path.open("a", encoding="utf-8") as fp:
            fp.write(line)
