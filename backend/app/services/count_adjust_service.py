"""库存盘点二次确认 (Phase 7 P1-10).

业务: 盘点改 physical_qty 直接生效太危险, 容易误填.

流程:
    propose(material_code, new_physical, actor, remark)
        → 创建一条 PendingAdjustment 记录 (status=pending), 不改库存
    list_pending(db)
        → admin / operator 看待审清单
    approve(adjustment_id, approver)
        → 调 inventory_lock_service.manual_adjust 真的改库存, status=approved
    reject(adjustment_id, approver, reason)
        → status=rejected, 不改库存

复用 Alert 中心: 创建后生成 alert 提醒主管审批。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory_lock import InventoryLockLedger
from app.services import alert_service, inventory_lock_service


# 用 InventoryLockLedger 的扩展记法替代独立表: kind="count_pending" 表示待审, kind="count_adjust" 表示已生效.

def propose(
    db: Session, *, material_code: str, new_physical,
    actor: str, remark: str, warehouse: str = "default",
) -> InventoryLockLedger:
    """提交一个盘点调整待审批 (不真的改库存)."""
    new_d = Decimal(str(new_physical))
    inv = inventory_lock_service._get_or_create_inventory(
        db, material_code, warehouse=warehouse,
    )
    delta = new_d - Decimal(inv.physical_qty or 0)
    if delta == 0:
        raise ValueError("无变化, 不需要调整")
    entry = InventoryLockLedger(
        source_kind="count_pending",
        source_id=None,
        material_code=material_code,
        warehouse=warehouse,
        kind="count_pending",
        qty=abs(delta),
        actor=actor,
        remark=f"待审 → {new_d} (delta {delta}): {remark}",
    )
    db.add(entry)
    db.flush()
    # 生成 alert 让审批人看
    alert_service.upsert(
        db, kind="count_adjust_pending", severity="warn",
        title=f"盘点待审: {material_code}",
        body=(f"{actor} 申请把 {material_code} 从 {inv.physical_qty} 改为 {new_d} "
              f"(差 {delta}). 原因: {remark}"),
        dedupe_key=f"count_adjust_pending:{entry.id}",
        related_url="/inventory/parts?tab=pending",
        context={"adjustment_id": entry.id, "material_code": material_code,
                 "current": float(inv.physical_qty or 0), "proposed": float(new_d),
                 "delta": float(delta), "proposer": actor, "remark": remark},
        sticky=True,
    )
    return entry


def list_pending(db: Session, *, material_code: Optional[str] = None
                 ) -> list[InventoryLockLedger]:
    q = select(InventoryLockLedger).where(
        InventoryLockLedger.kind == "count_pending",
    ).order_by(InventoryLockLedger.id.desc())
    if material_code:
        q = q.where(InventoryLockLedger.material_code == material_code)
    return list(db.execute(q).scalars())


def approve(
    db: Session, adjustment_id: int, *, approver: str,
) -> InventoryLockLedger:
    """二次确认: 真的修改库存."""
    entry = db.get(InventoryLockLedger, adjustment_id)
    if entry is None or entry.kind != "count_pending":
        raise ValueError("调整不存在或已处理")
    # 从 remark 解出 "→ N (delta ...)"
    remark = entry.remark or ""
    target = None
    import re
    m = re.search(r"→ ([\d.]+) \(", remark)
    if m:
        target = Decimal(m.group(1))
    if target is None:
        raise ValueError("无法解析目标数, 请使用 propose 接口创建")
    inventory_lock_service.manual_adjust(
        db, material_code=entry.material_code, new_physical=target,
        actor=approver, remark=f"批准 #{entry.id} from {entry.actor}",
    )
    # 标记原 pending 为 approved (改 kind, 留 trail)
    entry.kind = "count_approved"
    entry.remark = (entry.remark or "") + f"\n[approved by {approver} @ {datetime.now(timezone.utc).isoformat()}]"
    alert_service.resolve_by_dedupe(
        db, f"count_adjust_pending:{adjustment_id}", resolved_by=approver,
    )
    return entry


def reject(
    db: Session, adjustment_id: int, *, approver: str, reason: str,
) -> InventoryLockLedger:
    entry = db.get(InventoryLockLedger, adjustment_id)
    if entry is None or entry.kind != "count_pending":
        raise ValueError("调整不存在或已处理")
    entry.kind = "count_rejected"
    entry.remark = (entry.remark or "") + f"\n[rejected by {approver}: {reason}]"
    alert_service.resolve_by_dedupe(
        db, f"count_adjust_pending:{adjustment_id}", resolved_by=approver,
    )
    return entry
