"""异常严重度自动升级 (plan §12.1)。

规则：同 exception_type 下未处理 (open) 条目 >= 3 时，把该类型所有 open 异常的
severity 升一档 (info → warning → error)。已是 error 的不动。
每次升级写 last_escalated_at + 自增 escalation_count。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.exception import DataException

THRESHOLD = 3
LEVEL_UP = {"info": "warning", "warning": "error"}  # error → 不再升


@dataclass
class EscalationResult:
    exception_type: str
    open_count: int
    escalated_from: str
    escalated_to: str
    affected_ids: list[int]


def run(db: Session) -> list[EscalationResult]:
    """扫一遍所有 open 异常，按 type 聚合后判断是否升级。"""
    out: list[EscalationResult] = []
    counts_q = (
        select(DataException.exception_type, func.count(DataException.id))
        .where(DataException.status == "open")
        .group_by(DataException.exception_type)
        .having(func.count(DataException.id) >= THRESHOLD)
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    for exc_type, cnt in db.execute(counts_q).all():
        rows = db.execute(
            select(DataException).where(
                DataException.exception_type == exc_type, DataException.status == "open"
            )
        ).scalars().all()

        # 按当前 severity 分组分别升级 (避免重复升级)
        by_sev: dict[str, list[DataException]] = {}
        for r in rows:
            by_sev.setdefault(r.severity, []).append(r)

        for old_sev, group in by_sev.items():
            new_sev = LEVEL_UP.get(old_sev)
            if new_sev is None:  # error 不升
                continue
            affected: list[int] = []
            for r in group:
                r.severity = new_sev
                r.escalation_count = (r.escalation_count or 0) + 1
                r.last_escalated_at = now_iso
                affected.append(r.id)
            out.append(EscalationResult(
                exception_type=exc_type,
                open_count=int(cnt),
                escalated_from=old_sev,
                escalated_to=new_sev,
                affected_ids=affected,
            ))
    db.flush()
    return out
