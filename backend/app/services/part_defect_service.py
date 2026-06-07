"""配件坏件 / 返厂维修闭环 (方案 B).

业务: 配件到货后发现不可用 → 从良品库移到「待返厂/维修中」(defective_qty), 不计入可用。
之后三种归宿:
    - repaired  维修好 / 换新到货 → 移回良品库 (physical += qty)
    - scrapped  报废 → 直接核销 (defective -= qty)
    - returned  退货退款 → 核销 (defective -= qty), 备注记退款 (财务后续对账)

所有动作写 InventoryLockLedger 留痕 (append-only)。可用库存 = 物理 - 锁定 始终正确,
因为坏件一旦标记就从 physical 移出, 不会虚高可用。

公开 API:
    mark_defective(db, material_code=..., qty=..., reason=..., ...)   良品→待返厂
    resolve_defective(db, material_code=..., qty=..., disposition=...) 处理待返厂坏件
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.inventory import PartInventory
from app.services import inventory_lock_service as _ils

DEFAULT_WAREHOUSE = _ils.DEFAULT_WAREHOUSE

# disposition -> (ledger kind, 中文说明). kind 长度需 ≤ 16 (InventoryLockLedger.kind 列宽)。
_DISPOSITIONS: dict[str, tuple[str, str]] = {
    "repaired": ("defect_repair", "维修/换新回库"),
    "scrapped": ("defect_scrap", "报废核销"),
    "returned": ("defect_return", "退货退款核销"),
}


def mark_defective(
    db: Session, *, material_code: str, qty,
    actor: str = "user", reason: str = "到货不良",
    remark: Optional[str] = None, warehouse: str = DEFAULT_WAREHOUSE,
) -> PartInventory:
    """良品 → 待返厂/维修中: physical_qty -= qty, defective_qty += qty (可用随之下降)."""
    qty_d = Decimal(str(qty))
    if qty_d <= 0:
        raise ValueError("qty 必须 > 0")
    inv = _ils._get_or_create_inventory(db, material_code, warehouse=warehouse)
    if qty_d > Decimal(inv.physical_qty or 0):
        raise ValueError(f"良品库存不足: 现有 {inv.physical_qty}, 要标记坏件 {qty_d}")
    inv.physical_qty = Decimal(inv.physical_qty or 0) - qty_d
    inv.defective_qty = Decimal(inv.defective_qty or 0) + qty_d
    note = f"标记坏件({reason})" + (f": {remark}" if remark else "")
    _ils._write_ledger(
        db, source_kind="defect", source_id=None,
        material_code=material_code, kind="defect_out",
        qty=qty_d, actor=actor, warehouse=warehouse, remark=note,
    )
    return inv


def resolve_defective(
    db: Session, *, material_code: str, qty, disposition: str,
    actor: str = "user", remark: Optional[str] = None,
    warehouse: str = DEFAULT_WAREHOUSE,
) -> PartInventory:
    """处理待返厂坏件.

    disposition:
        repaired  → 移回良品库 (defective -= qty, physical += qty)
        scrapped  → 报废核销   (defective -= qty)
        returned  → 退货退款核销 (defective -= qty)
    """
    if disposition not in _DISPOSITIONS:
        raise ValueError(f"未知处理方式: {disposition!r} (应为 repaired/scrapped/returned)")
    qty_d = Decimal(str(qty))
    if qty_d <= 0:
        raise ValueError("qty 必须 > 0")
    inv = _ils._get_or_create_inventory(db, material_code, warehouse=warehouse)
    if qty_d > Decimal(inv.defective_qty or 0):
        raise ValueError(f"待返厂数量不足: 现有 {inv.defective_qty}, 要处理 {qty_d}")
    inv.defective_qty = Decimal(inv.defective_qty or 0) - qty_d
    kind, label = _DISPOSITIONS[disposition]
    if disposition == "repaired":
        inv.physical_qty = Decimal(inv.physical_qty or 0) + qty_d
    note = label + (f": {remark}" if remark else "")
    _ils._write_ledger(
        db, source_kind="defect", source_id=None,
        material_code=material_code, kind=kind,
        qty=qty_d, actor=actor, warehouse=warehouse, remark=note,
    )
    return inv
