"""灰度监控 (2026-07-09): 部署幂等导入修复后, 盯订单是否还在"翻烧饼"。

检测判据 = 近 N 天某字段出现【值回跳】(同一取值重复出现 = A→B→A 震荡), 以此区别于
付款→发货→签收 这类【单向进展】(值不重复, 不算翻)。命中 → 记 order_import_flip 异常;
该单稳定 N 天(不再震荡)→ 复核自动销账(检查器 check_resolved, 已注册进 exception_recheck)。

用法: 灰度一周里每天 scan 一次(scheduler daily_flip_monitor), 异常页即可看到"哪些单还在翻";
理想情况修复后异常逐日清空。留作长期护栏也可(捕捉未来导入回归)。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.exception import DataException
from app.models.field_change import FieldChange

EXC_TYPE = "order_import_flip"
_FIELDS = ("status", "paid_amount", "refund_amount", "shop_received_amount")
_ACTOR = "订单重导"          # 导入覆盖时 field_change 的 actor
_DEFAULT_DAYS = 3


def _norm_val(v) -> str:
    """取值归一: 数值统一成标准形('0.00'→'0', '42.40'→'42.4'), 非数值原样。
    防 '0.00' 与 '0' 被当成两个值 (2026-07-13: 等价写入曾把报警喂成僵尸)。"""
    s = str(v)
    try:
        from decimal import Decimal as _D
        d = _D(s)
        return str(d.normalize())
    except Exception:  # noqa: BLE001
        return s


def _flip_fields(db: Session, order_no: str, days: int) -> dict[str, list[str]]:
    """近 days 天内【出现值回跳】的字段 → 其取值序列。空 = 没在翻。

    判据细化 (2026-07-13): 先数值归一 + 连续同值去重(同值重写不是震荡),
    再看剩余序列是否有值重复出现(A→B→A 才算回跳; 单向进展/原地重写都不算)。"""
    since = datetime.now() - timedelta(days=days)
    rows = db.execute(
        select(FieldChange).where(
            FieldChange.table_name == "orders",
            FieldChange.row_pk == order_no,
            FieldChange.actor == _ACTOR,
            FieldChange.field.in_(_FIELDS),
            FieldChange.created_at >= since,
        ).order_by(FieldChange.created_at)
    ).scalars().all()
    seq: dict[str, list[str]] = {}
    for r in rows:
        v = _norm_val(r.new_value)
        vals = seq.setdefault(r.field, [])
        if not vals or vals[-1] != v:   # 连续同值去重
            vals.append(v)
    return {f: vals for f, vals in seq.items() if len(vals) > len(set(vals))}


def _open_exc(db: Session, order_no: str) -> Optional[DataException]:
    return db.execute(
        select(DataException).where(
            DataException.exception_type == EXC_TYPE,
            DataException.source_pk == order_no,
            DataException.status == "open",
        )
    ).scalars().first()


def scan(db: Session, *, days: int = _DEFAULT_DAYS) -> dict:
    """扫近 days 天仍在震荡的订单, 幂等记异常(已有 open 的同单则跳过)。返回统计。"""
    from app.services import exception_service

    since = datetime.now() - timedelta(days=days)
    onos = db.execute(
        select(FieldChange.row_pk).where(
            FieldChange.table_name == "orders",
            FieldChange.actor == _ACTOR,
            FieldChange.field.in_(_FIELDS),
            FieldChange.created_at >= since,
        ).distinct()
    ).scalars().all()

    recorded = skipped = 0
    for ono in onos:
        if not ono:
            continue
        flips = _flip_fields(db, ono, days)
        if not flips:
            continue
        if _open_exc(db, ono) is not None:
            skipped += 1
            continue
        detail = "; ".join(
            f"{f} 在 {sorted(set(v))} 之间弹了 {len(v)} 次" for f, v in flips.items()
        )
        exception_service.record(
            db,
            source_table="orders",
            source_pk=ono,
            exception_type=EXC_TYPE,
            severity="warning",
            description=(
                f"订单 {ono} 近 {days} 天仍被重导反复横跳: {detail}。已部署幂等修复但仍震荡 —— "
                f"多半是该单在两个淘宝导出里状态/金额确实各执一词, 需人工定夺。"
            ),
            suggestion_action="核对该单淘宝真实状态/金额; 若两导出矛盾, 以订单报表(一行一单)为准手工订正",
            context={"fields": {f: sorted(set(v)) for f, v in flips.items()}},
        )
        recorded += 1
    db.commit()
    return {"scanned_orders": len(onos), "recorded": recorded, "skipped_existing": skipped}


def check_resolved(db: Session, exc: DataException, *, days: int = _DEFAULT_DAYS) -> Optional[str]:
    """复核器(供 exception_recheck 注册): 近 days 天不再震荡 → None(销账); 仍震荡 → 原因串。"""
    ono = exc.source_pk
    if not ono:
        return None
    flips = _flip_fields(db, ono, days)
    if not flips:
        return None
    return f"订单 {ono} 仍在横跳: {', '.join(flips.keys())}"
