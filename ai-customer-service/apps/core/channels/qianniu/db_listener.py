"""
千牛本地 SQLite 轮询（实验）：复制 .db 快照后只读查询，避免文件锁。

使用前请在 configs/query_rewrite.yaml 的 db_listener 节填写 db_path、table、col_map。
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

from apps.core.ai.input_quality_gate import DBListenerYaml

logger = logging.getLogger(__name__)


class QianniuDBListenerThread:
    """后台线程：轮询快照并回调新消息（按 msg_id 去重）。"""

    def __init__(
        self,
        cfg: DBListenerYaml,
        on_row: Callable[[dict[str, Any]], None],
        *,
        poll_interval: float | None = None,
    ) -> None:
        self._cfg = cfg
        self._on_row = on_row
        self._poll = float(poll_interval or cfg.poll_interval_seconds)
        self._stop = threading.Event()
        self._seen: set[str] = set()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="QianniuDBListener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
        self._thread = None

    def _snapshot_path(self) -> str:
        base = tempfile.gettempdir()
        return os.path.join(base, "aiworkbench_qn_snap.db")

    def _snapshot(self) -> str:
        dst = self._snapshot_path()
        shutil.copy2(self._cfg.db_path, dst)
        return dst

    def _query_new(self, snap: str) -> list[dict[str, Any]]:
        cm = self._cfg.col_map
        need = ("id", "content", "time")
        for k in need:
            if k not in cm or not str(cm[k]).strip():
                raise ValueError(f"db_listener.col_map 缺少 {k}")
        table = self._cfg.table.strip()
        if not table:
            raise ValueError("db_listener.table 为空")

        id_col = str(cm["id"])
        content_col = str(cm["content"])
        time_col = str(cm["time"])
        buyer_col = str(cm.get("buyer") or "").strip()
        direction_col = str(cm.get("direction") or "").strip()

        sel_buyer = f", {buyer_col} AS buyer_id" if buyer_col else ", '' AS buyer_id"
        where_dir = ""
        if direction_col:
            where_dir = f" AND CAST({direction_col} AS INTEGER) = 0"

        sql = (
            f"SELECT {id_col} AS _id, {content_col} AS _content, {time_col} AS _ts{sel_buyer} "
            f"FROM {table} WHERE 1=1{where_dir} ORDER BY _ts ASC LIMIT 200"
        )

        conn = sqlite3.connect(f"file:{snap}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql)
            rows = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

        out: list[dict[str, Any]] = []
        for r in rows:
            mid = str(r.get("_id") or "")
            if not mid or mid in self._seen:
                continue
            body = r.get("_content")
            if not isinstance(body, str) or not body.strip():
                self._seen.add(mid)
                continue
            out.append(
                {
                    "msg_id": mid,
                    "buyer_text": body.strip(),
                    "buyer_id": str(r.get("buyer_id") or ""),
                    "ts": r.get("_ts"),
                }
            )
        return out

    def _run(self) -> None:
        if not self._cfg.db_path or not os.path.isfile(self._cfg.db_path):
            logger.warning("[DB监听] db_path 无效或未配置，线程退出")
            return
        logger.info("[DB监听] 启动 → %s", self._cfg.db_path)
        while not self._stop.is_set():
            try:
                snap = self._snapshot()
                for row in self._query_new(snap):
                    mid = row["msg_id"]
                    if mid in self._seen:
                        continue
                    self._seen.add(mid)
                    try:
                        self._on_row(row)
                    except Exception as e:
                        logger.warning("[DB监听] 回调异常: %s", e)
            except Exception as e:
                logger.warning("[DB监听] 轮询失败（将稍后重试）: %s", e)
            self._stop.wait(self._poll)
