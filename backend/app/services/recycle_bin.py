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


def read_archive(filename: str) -> dict | None:
    """读取一个回收站快照内容 (供下载/查看/人工恢复)。防路径穿越: 只允许 bin 目录下纯文件名。"""
    safe = os.path.basename(filename)
    if safe != filename or not safe.endswith(".json"):
        return None
    path = os.path.join(_bin_dir(), safe)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _restore_models() -> list[tuple]:
    """表名 → 模型, 顺序为父表先于子表 (delivery_notes 先于 lines, 保住外键)。"""
    from app.models.finance import AlipayFlow, FactoryReconciliation
    from app.models.order import FactoryOrder, Order
    from app.models.supplier import DeliveryNote, DeliveryNoteLine
    return [
        ("delivery_notes", DeliveryNote),
        ("delivery_note_lines", DeliveryNoteLine),
        ("alipay_flows", AlipayFlow),
        ("orders", Order),
        ("factory_orders", FactoryOrder),
        ("factory_reconciliations", FactoryReconciliation),
    ]


def _coerce(value: Any, col: Any) -> Any:
    """把 JSON 里的字符串值还原成列的 Python 类型 (Decimal/date/datetime)。"""
    if value is None:
        return None
    import datetime as _dt
    from decimal import Decimal as _Dec
    try:
        pt = col.type.python_type
    except Exception:
        return value
    if pt is _Dec and not isinstance(value, _Dec):
        try:
            return _Dec(str(value))
        except Exception:
            return None
    if pt is _dt.datetime and isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    if pt is _dt.date and isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError:
            return None
    return value


def restore(db, filename: str) -> dict:
    """把回收站快照的数据重新插回各表 (保留原主键以维持外键关系); 已存在的主键跳过 (幂等)。

    返回 {restored: {表: 条数}, total}。PG 上会重置自增序列, 避免后续插入撞已恢复的 id。
    """
    from sqlalchemy import text as _text
    data = read_archive(filename)
    if data is None:
        return {"error": "not_found"}
    payload = data.get("data", {})
    restored: dict[str, int] = {}
    touched: list[str] = []
    for table, model in _restore_models():
        rows = payload.get(table) or []
        if not rows:
            continue
        cols = {c.key: c for c in model.__table__.columns}
        n = 0
        for raw in rows:
            pk = raw.get("id")
            if pk is not None and db.get(model, pk) is not None:
                continue   # 已存在 → 跳过, 不覆盖
            db.add(model(**{k: _coerce(v, cols[k]) for k, v in raw.items() if k in cols}))
            n += 1
        if n:
            db.flush()
            restored[table] = n
            touched.append(model.__tablename__)
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        for t in touched:
            db.execute(_text(
                f"SELECT setval(pg_get_serial_sequence('{t}','id'), "
                f"(SELECT COALESCE(MAX(id), 1) FROM {t}))"
            ))
    db.commit()
    return {"restored": restored, "total": sum(restored.values())}


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
