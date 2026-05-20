"""飞书同步引擎骨架 (plan §5)。

Phase 1 仅占位：定义接口、记录绑定关系、跑一个 dry-run 报告每张表的待同步条数。
真正的拉/推 + 冲突检测放到 Phase 1 末尾 / Phase 3.5 落地。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.feishu_sync import FeishuSyncMap, FeishuTableBinding


@dataclass
class SyncStatus:
    system_table: str
    feishu_table_id: str
    direction: str
    enabled: bool
    mapped_rows: int


def list_status(db: Session) -> list[SyncStatus]:
    bindings = db.execute(select(FeishuTableBinding)).scalars().all()
    out: list[SyncStatus] = []
    for b in bindings:
        count = db.execute(
            select(func.count(FeishuSyncMap.id)).where(
                FeishuSyncMap.system_table == b.system_table,
                FeishuSyncMap.feishu_table_id == b.feishu_table_id,
            )
        ).scalar_one()
        out.append(
            SyncStatus(
                system_table=b.system_table,
                feishu_table_id=b.feishu_table_id,
                direction=b.direction,
                enabled=b.enabled,
                mapped_rows=count,
            )
        )
    return out


def push_record(*args, **kwargs):  # pragma: no cover — Phase 1 末再实装
    raise NotImplementedError("飞书 push 还未实装 — 见 plan §5.3 ‘同步引擎设计’")


def pull_records(*args, **kwargs):  # pragma: no cover
    raise NotImplementedError("飞书 pull 还未实装 — 见 plan §5.3 ‘同步引擎设计’")
