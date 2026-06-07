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
    # source_pk 列宽仅 64; 业务键(如支付宝交易流水号)可能更长 → 截断,
    # 防 StringDataRightTruncation 直接崩掉整批导入 (异常记录只需可定位的引用即可)。
    # 仅处理字符串: int 等其他类型本就很短, 交给 SQLAlchemy 自行转换。
    if isinstance(source_pk, str) and len(source_pk) > 64:
        source_pk = source_pk[:64]
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
