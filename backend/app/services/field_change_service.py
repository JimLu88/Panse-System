"""人工编辑历史档案服务 (方向 2+4)。

约定:
  - 只在"人"触发的写端点调用 (web 编辑 / 飞书修改 source='feishu');
    系统自动重算/导入回填不调 → 档案里全是人的决定, 不被机器噪音淹没。
  - diff_and_apply(): 编辑端点一行接入 — 捕获旧值 → setattr → 记录差异。
  - history(): 单字段最近 N 份 (默认 30); recent(): 修改档案中心全局检索。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.field_change import FieldChange

_logger = logging.getLogger("panse.field_change")


def _to_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v)


def _num_equal(a: Optional[str], b: Optional[str]) -> bool:
    """数值等价判定: '0.00' 与 '0'、'42.40' 与 '42.4' 是同一个数, 不算修改。
    (2026-07-13: 重导把 refund 0.00 写成 0 被记成"变化", 喂给翻烧饼检测器当回跳,
    异常永远凑不齐3天静默 → 僵尸报警。)"""
    if a is None or b is None:
        return False
    try:
        from decimal import Decimal as _D
        return _D(a) == _D(b)
    except Exception:  # noqa: BLE001 - 非数值走字符串比较
        return False


def record(
    db: Session, *,
    table: str, pk: Any, field: str,
    old: Any, new: Any,
    actor: Optional[str] = None, source: str = "web",
    row_label: Optional[str] = None, field_label: Optional[str] = None,
) -> None:
    """记一条字段修改。old==new(含数值等价) 时不记。失败只告警, 绝不阻断业务保存。"""
    try:
        old_s, new_s = _to_str(old), _to_str(new)
        if old_s == new_s or _num_equal(old_s, new_s):
            return
        db.add(FieldChange(
            table_name=table, row_pk=str(pk), row_label=row_label,
            field=field, field_label=field_label,
            old_value=old_s, new_value=new_s,
            actor=actor, source=source,
        ))
        db.flush()
    except Exception:  # pragma: no cover - 审计失败不影响保存
        _logger.warning("field_change 记录失败 %s.%s#%s", table, field, pk, exc_info=True)


def human_pks(db: Session, *, table: str, field: str,
              exclude_sources: tuple = ("import",)) -> set[str]:
    """该表该字段被「人」改过的行号集合(默认排除 source='import' 的机器写入档案)。
    有人工档案 = 人拍过板。

    用途: 机器批处理(智能归类/自动纠正/重导覆盖)改写前查此集合, **人改过的行不许机器再翻**。
    2026-07-12 复发案: 流水19365(山**退款)人工归 refund_out 后, 双机战期间旧镜像(无退款护栏)
    的 route 又翻回 factory_payment → 逐月对账假差反复重建。退款护栏只认得"退款"特征, 人工锁
    兜住所有未来写入方: 只要人拍过板, 机器一律绕行(人自己仍随时可改, 只锁机器)。
    2026-07-13 扩展: 订单表的重导覆盖(_trace source='import')也记档案 → 判"人"须排除 import。"""
    stmt = select(FieldChange.row_pk).where(
        FieldChange.table_name == table, FieldChange.field == field,
    )
    if exclude_sources:
        stmt = stmt.where(FieldChange.source.notin_(exclude_sources))
    rows = db.execute(stmt.distinct()).scalars().all()
    return set(rows)


def diff_and_apply(
    db: Session, obj: Any, data: dict, *,
    table: str, pk: Any,
    actor: Optional[str] = None, source: str = "web",
    row_label: Optional[str] = None,
    field_labels: Optional[dict] = None,
    skip_fields: tuple = (),
) -> int:
    """编辑端点一行接入: 对 data 里的每个键, 捕获旧值 → setattr → 记差异。

    返回实际变化的字段数。data 值为 None 表示"清空"也照记。
    """
    changed = 0
    labels = field_labels or {}
    for k, v in data.items():
        if k in skip_fields or not hasattr(obj, k):
            continue
        old = getattr(obj, k)
        setattr(obj, k, v)
        old_s, new_s = _to_str(old), _to_str(v)
        if old_s != new_s:
            record(db, table=table, pk=pk, field=k, old=old, new=v,
                   actor=actor, source=source, row_label=row_label,
                   field_label=labels.get(k))
            changed += 1
    return changed


def history(db: Session, *, table: str, pk: str, field: str, limit: int = 30) -> list[dict]:
    """单字段最近 N 份历史 (新→旧), 每份带日期/人/来源。"""
    rows = db.execute(
        select(FieldChange).where(
            FieldChange.table_name == table,
            FieldChange.row_pk == str(pk),
            FieldChange.field == field,
        ).order_by(FieldChange.id.desc()).limit(limit)
    ).scalars().all()
    return [_out(r) for r in rows]


def recent(
    db: Session, *,
    table: Optional[str] = None, pk: Optional[str] = None,
    actor: Optional[str] = None, source: Optional[str] = None,
    q: Optional[str] = None, limit: int = 200, offset: int = 0,
) -> dict:
    """修改档案中心: 按 表/行/人/来源/关键词 过滤的全局流水。"""
    stmt = select(FieldChange)
    if table:
        stmt = stmt.where(FieldChange.table_name == table)
    if pk:
        stmt = stmt.where(FieldChange.row_pk == str(pk))
    if actor:
        stmt = stmt.where(FieldChange.actor == actor)
    if source:
        stmt = stmt.where(FieldChange.source == source)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            FieldChange.row_pk.ilike(like)
            | FieldChange.row_label.ilike(like)
            | FieldChange.field.ilike(like)
            | FieldChange.field_label.ilike(like)
        )
    rows = db.execute(
        stmt.order_by(FieldChange.id.desc()).offset(offset).limit(limit)
    ).scalars().all()
    return {"rows": [_out(r) for r in rows]}


def _out(r: FieldChange) -> dict:
    return {
        "id": r.id,
        "table_name": r.table_name,
        "row_pk": r.row_pk,
        "row_label": r.row_label,
        "field": r.field,
        "field_label": r.field_label,
        "old_value": r.old_value,
        "new_value": r.new_value,
        "actor": r.actor,
        "source": r.source,
        "source_label": {"feishu": "飞书修改", "import": "导入覆盖"}.get(r.source, "网页编辑"),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
