"""异常处理与建议模块 (plan §6) 的服务入口。

Phase 1 只实现「写入异常」一条路径；扫描/解决逻辑放到 Phase 3.5。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.exception import DataException


def record(
    db: Session,
    *,
    source_table: str,
    source_pk: Optional[str],
    exception_type: str,
    description: str,
    severity: str = "warning",
    suggestion_action: Optional[str] = None,
    context: Optional[dict[str, Any]] = None,
) -> DataException:
    exc = DataException(
        source_table=source_table,
        source_pk=source_pk,
        exception_type=exception_type,
        description=description,
        severity=severity,
        suggestion_action=suggestion_action,
        context=context,
    )
    db.add(exc)
    db.flush()
    return exc
