# -*- coding: utf-8 -*-
"""异常中心导出 (用户需求 2026-06-11 重申): 异常要随「对应的源表」一起导出。

不是导一张异常清单, 而是: 订单有异常 → 导出订单表, 在出问题的那一行
最后一列写「异常批注」。每个 source_table 一个 sheet:
    - 能定位到源表模型 → 整行数据 + 末列批注 (同一行多条异常编号拼接)
    - 定位不到的异常 (如 reconciliation 这种虚拟源) → 该 sheet 直接列异常本身
只导 open 异常 (可选含 ignored), resolved 的不再打扰。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.exception import DataException

# 源表业务键候选 (按序尝试; 都没有就拿主键 id 按数字匹配)
_KEY_CANDIDATES = ("order_no", "sku_code", "code", "purchase_no", "transaction_no",
                   "wsf_order_no", "factory_order_no", "sample_no", "platform_order_no")

_SEVERITY_CN = {"info": "提示", "warning": "警告", "error": "严重"}
_NUMS = "①②③④⑤⑥⑦⑧⑨⑩"


def _model_for_table(table_name: str):
    for mapper in Base.registry.mappers:
        if getattr(mapper.class_, "__tablename__", None) == table_name:
            return mapper.class_
    return None


def _key_column(model):
    cols = {c.key for c in model.__table__.columns}
    for k in _KEY_CANDIDATES:
        if k in cols:
            return k
    return "id"


def _cell(v):
    """openpyxl 能写的标量; Decimal→float、date/datetime 原生透传(让 Excel 当数字/日期),
    其余 dict/list 等转 str。修: 旧版把 Decimal/日期一并 str() 导致全量导出数字变文本。
    注意: datetime/time 必须剥掉 tzinfo —— openpyxl 不支持带时区的时间(TimestampMixin 是 tz-aware)。"""
    from datetime import date as _date, datetime as _dt, time as _time
    from decimal import Decimal as _Dec
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    if isinstance(v, _Dec):
        return float(v)
    if isinstance(v, _dt):            # datetime 是 date 子类, 必须先判
        return v.replace(tzinfo=None)
    if isinstance(v, _time):
        return v.replace(tzinfo=None)
    if isinstance(v, _date):
        return v   # 纯日期无时区, openpyxl 原生写成日期, 由调用方设 number_format
    return str(v)


def _join_notes(ns: list[str]) -> str:
    if len(ns) == 1:
        return ns[0]
    return " ".join(f"{_NUMS[i] if i < len(_NUMS) else i+1}{n}" for i, n in enumerate(ns))


def build_export_workbook(db: Session, *, source_table: Optional[str] = None,
                          include_ignored: bool = False):
    import openpyxl
    statuses = ("open", "ignored") if include_ignored else ("open",)
    stmt = select(DataException).where(DataException.status.in_(statuses))
    if source_table:
        stmt = stmt.where(DataException.source_table == source_table)
    excs = db.execute(stmt.order_by(DataException.source_table, DataException.id)).scalars().all()

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    by_table: dict[str, list[DataException]] = {}
    for e in excs:
        by_table.setdefault(e.source_table, []).append(e)

    if not by_table:
        ws = wb.create_sheet("无未处理异常")
        ws.append(["当前没有未处理 (open) 的异常。"])
        return wb

    for table, items in by_table.items():
        model = _model_for_table(table)
        sheet = table[:28]   # sheet 名 31 字符上限
        if model is None:
            # 虚拟源 (对账差异等没有真实源表): 直接列异常本身
            ws = wb.create_sheet(sheet)
            ws.append(["业务键", "严重度", "异常说明", "状态", "记录时间"])
            for e in items:
                ws.append([e.source_pk, _SEVERITY_CN.get(e.severity, e.severity),
                           e.description, e.status,
                           str(e.created_at)[:19] if e.created_at else None])
            continue

        key_col = _key_column(model)
        notes: dict[str, list[str]] = {}
        for e in items:
            if e.source_pk:
                notes.setdefault(str(e.source_pk), []).append(
                    f"[{_SEVERITY_CN.get(e.severity, e.severity)}] {e.description}")
        rows = []
        located: set[str] = set()
        if notes:
            col = getattr(model, key_col)
            keys = list(notes.keys())
            if key_col == "id":
                int_keys = [int(k) for k in keys if k.isdigit()]
                found = (db.execute(select(model).where(col.in_(int_keys))).scalars().all()
                         if int_keys else [])
            else:
                found = db.execute(select(model).where(col.in_(keys))).scalars().all()
            for r in found:
                k = str(getattr(r, key_col))
                located.add(k)
                rows.append((r, _join_notes(notes[k])))

        ws = wb.create_sheet(sheet)
        col_names = [c.key for c in model.__table__.columns]
        ws.append(col_names + ["异常批注"])
        for r, note in rows:
            ws.append([_cell(getattr(r, c)) for c in col_names] + [note])
        # 定位不到源行的异常 (行已删/键变了) 也不能丢
        orphans = [(k, ns) for k, ns in notes.items() if k not in located]
        if orphans:
            ws.append([])
            ws.append(["── 以下异常在源表里找不到对应行 (可能已删除/键已变) ──"])
            for k, ns in orphans:
                ws.append([k] + [None] * (len(col_names) - 1) + ["; ".join(ns)])
    return wb
