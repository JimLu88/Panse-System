"""删除前回收站 (优化 #4): 把将被删除的行序列化成 JSON 存到 storage/recycle_bin/,
防止破坏性操作 (导入回滚 / 清空数据) 把数据删得不可恢复。

不做自动重插 (会踩 FK/自增序列的坑); 但被删数据完整保存为 JSON, 可人工核对 / 重新导入。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _serialize(obj: Any) -> dict:
    return {c.key: _jsonable(getattr(obj, c.key)) for c in obj.__table__.columns}


def _bin_dir() -> str:
    d = os.environ.get("RECYCLE_BIN_DIR", "/app/storage/recycle_bin")
    os.makedirs(d, exist_ok=True)
    return d


def archive(rows_by_table: dict[str, list], *, batch_ref: str, reason: str) -> str | None:
    """把 {表名: [ORM 行,...]} 快照成一个 JSON 文件; 无数据则返回 None, 否则返回文件路径。"""
    payload = {tbl: [_serialize(r) for r in rows] for tbl, rows in rows_by_table.items() if rows}
    if not any(payload.values()):
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_ref = batch_ref.replace(":", "_").replace("/", "_")
    path = os.path.join(_bin_dir(), f"{safe_ref}_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"batch_ref": batch_ref, "reason": reason, "archived_at": ts, "data": payload},
            f, ensure_ascii=False, indent=1,
        )
    return path


def list_archives(limit: int = 100) -> list[dict]:
    """列出回收站文件 (文件名/大小), 供后台查看。"""
    d = _bin_dir()
    out = []
    for name in sorted(os.listdir(d), reverse=True)[:limit]:
        if not name.endswith(".json"):
            continue
        p = os.path.join(d, name)
        try:
            st = os.stat(p)
            out.append({"file": name, "size_bytes": st.st_size})
        except OSError:
            continue
    return out
