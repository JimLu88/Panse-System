# -*- coding: utf-8 -*-
"""导入「凭空消失」检测 (用户拍板 2026-06-12):

重导表格时, 库里有、新文件里没有的记录 = "被覆盖为空" 的危险信号
(平台删单/拆单/导出遗漏/产品下架漏行)。系统**绝不静默删除或清空**,
而是每个消失的键报一条 import_missing 异常, 由用户明确决定:
    - 确认要删 → 人工删除后把异常标已处理
    - 误报/正常 → 忽略
键在后续导入中重新出现 → 自动把还开着的异常销账 (resolve)。
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy.orm import Session

from app.models.exception import DataException
from app.services import exception_service

_logger = logging.getLogger("panse.import_vanish")

EXC_TYPE = "import_missing"
_CAP = 100   # 单次导入最多报多少条 (防半截文件刷屏; 坏文件拦截在前面挡大头)


def report_missing(db: Session, *, source_table: str, label: str,
                   missing: Iterable[str], scope_desc: str) -> int:
    """给每个消失的业务键报一条异常 (幂等: 已有 open/ignored 的不重复报)。

    返回实际新报条数。超过 _CAP 截断并日志告警。
    """
    keys = [k for k in missing if k]
    if not keys:
        return 0
    existing = {
        r[0] for r in db.query(DataException.source_pk).filter(
            DataException.source_table == source_table,
            DataException.exception_type == EXC_TYPE,
            DataException.status.in_(("open", "ignored")),
        ).all() if r[0]
    }
    n = 0
    for key in keys:
        if key in existing:
            continue
        if n >= _CAP:
            _logger.warning("import_missing 超过 %d 条上限, 截断 (%s)", _CAP, source_table)
            break
        exception_service.record(
            db,
            source_table=source_table,
            source_pk=key,
            exception_type=EXC_TYPE,
            severity="warning",
            description=(f"{label}「{key}」在最新导入文件里不存在了 ({scope_desc})。"
                         "系统未做任何改动 — 确认要删除请人工删除后把本异常标已处理; "
                         "若是拆单/导出遗漏等误报请忽略。"),
            suggestion_action="confirm_delete_or_ignore",
            context={"key": key, "scope": scope_desc},
        )
        n += 1
    if n:
        db.flush()
    return n


def resolve_reappeared(db: Session, *, source_table: str,
                       present_keys: set[str]) -> int:
    """键在新导入里重新出现 → 自动销掉还开着的 import_missing 异常。"""
    if not present_keys:
        return 0
    rows = db.query(DataException).filter(
        DataException.source_table == source_table,
        DataException.exception_type == EXC_TYPE,
        DataException.status == "open",
        DataException.source_pk.in_(list(present_keys)),
    ).all()
    for r in rows:
        r.status = "resolved"
        r.resolved_by = "导入重现自动销账"
    if rows:
        db.flush()
    return len(rows)
