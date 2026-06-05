"""库存锁定 / 释放 / 出货 / 退货流程 (Phase 1C).

业务需求 3/10/11: 工厂订单创建 → 锁配件库存; 订单取消 → 释放; 实际出货 → 扣物理库存;
退货完好 → 入库 (整产品, 不拆 BOM); 缺货 → 自动生成 Alert。

所有变动都写 InventoryLockLedger (append-only 审计), PartInventory 的 locked_qty/physical_qty
直接对应 ledger 累加。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.inventory_lock import InventoryLockLedger
from app.models.order import FactoryOrder, Order
from app.services import alert_service

_logger = logging.getLogger("panse.inventory_lock")

DEFAULT_WAREHOUSE = "default"


@dataclass
class LockResult:
    """工厂订单锁库存结果."""
    factory_order_id: int
    locked_lines: list[dict] = field(default_factory=list)
    # [{material_code, qty_locked, available_after}]
    shortages: list[dict] = field(default_factory=list)
    # [{material_code, requested, available, missing}]
    alerts_created: list[int] = field(default_factory=list)


# ----------------------------- 内部辅助 --------------------------- #


def _bom_for(db: Session, *, product_code: str, sku_code: Optional[str]) -> list[BomLine]:
    """取产品 + SKU 对应的 BOM 行. sku_code 优先匹配, 没有就退到产品级."""
    if sku_code:
        rows = db.execute(
            select(BomLine).where(
                BomLine.product_code == product_code,
                BomLine.sku_code == sku_code,
            )
        ).scalars().all()
        if rows:
            return list(rows)
    return list(db.execute(
        select(BomLine).where(BomLine.product_code == product_code)
    ).scalars().all())


def _get_or_create_inventory(
    db: Session, material_code: str, *, warehouse: str = DEFAULT_WAREHOUSE,
) -> PartInventory:
    stmt = select(PartInventory).where(
        PartInventory.warehouse == warehouse,
        PartInventory.material_code == material_code,
    )
    # 并发"已付款"订单同时锁同一物料时, 行锁防止读后写丢失更新 → 超卖。
    # 仅 Postgres 生效 (SELECT ... FOR UPDATE); SQLite 自动忽略, 不影响测试。
    if db.bind is not None and db.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        row = PartInventory(warehouse=warehouse, material_code=material_code,
                            physical_qty=0, locked_qty=0)
        db.add(row)
        db.flush()
    return row


def _write_ledger(
    db: Session, *, source_kind: str, source_id: Optional[int],
    material_code: Optional[str], product_code: Optional[str] = None,
    sku_code: Optional[str] = None,
    kind: str, qty: Decimal, actor: Optional[str] = None,
    warehouse: str = DEFAULT_WAREHOUSE, remark: Optional[str] = None,
) -> InventoryLockLedger:
    entry = InventoryLockLedger(
        source_kind=source_kind, source_id=source_id,
        material_code=material_code, product_code=product_code, sku_code=sku_code,
        warehouse=warehouse, kind=kind, qty=qty,
        actor=actor, remark=remark,
    )
    db.add(entry)
    db.flush()
    return entry


# ----------------------------- 锁定 / 释放 ------------------------ #


def lock_for_factory_order(
    db: Session, factory_order_id: int, *, actor: str = "system",
) -> LockResult:
    """工厂订单创建时调. 按 BOM 展开物料, 每个 PartInventory.locked_qty += qty.

    Phase 6: 用 Decimal 全程算, 不再取整, 避免多锁 (BOM 2.5 件 → 锁 2.5 件而非 3).
    不足时不阻断 (仍写 ledger), 但生成 critical Alert 提示入库。
    """
    fo = db.get(FactoryOrder, factory_order_id)
    if fo is None:
        raise ValueError(f"FactoryOrder {factory_order_id} 不存在")
    if not fo.product_code:
        raise ValueError(f"FactoryOrder {factory_order_id} 缺 product_code, 无法展开 BOM")

    bom = _bom_for(db, product_code=fo.product_code, sku_code=None)
    result = LockResult(factory_order_id=factory_order_id)

    for line in bom:
        per = Decimal(line.qty_per_product or "0")
        need = (per * Decimal(fo.qty or 1)).quantize(Decimal("0.001"))
        if need <= 0:
            continue
        inv = _get_or_create_inventory(db, line.material_code)
        inv.locked_qty = Decimal(inv.locked_qty or 0) + need
        _write_ledger(
            db, source_kind="factory_order", source_id=factory_order_id,
            material_code=line.material_code, kind="lock",
            qty=need, actor=actor,
            remark=f"工厂订单 {fo.factory_order_no}",
        )
        result.locked_lines.append({
            "material_code": line.material_code,
            "qty_locked": float(need),
            "physical": float(inv.physical_qty),
            "locked_after": float(inv.locked_qty),
            "available_after": float(inv.available_qty),
        })
        # 不足 → critical alert
        if inv.available_qty < 0:
            missing = -inv.available_qty
            alert = alert_service.upsert(
                db,
                kind="low_stock_part",
                severity="critical",
                title=f"配件缺货: {line.material_code}",
                body=(f"{line.material_code} 当前已锁定 {inv.locked_qty} > 物理 {inv.physical_qty}, "
                      f"缺 {missing} 件. 请尽快入库或调整订单."),
                dedupe_key=f"low_stock_part:{line.material_code}",
                related_url=f"/inventory/parts?code={line.material_code}",
                context={"material_code": line.material_code,
                         "physical": float(inv.physical_qty),
                         "locked": float(inv.locked_qty),
                         "missing": float(missing),
                         "factory_order_id": factory_order_id},
                sticky=True,
            )
            result.shortages.append({
                "material_code": line.material_code,
                "requested": float(need),
                "available": float(inv.physical_qty),
                "missing": float(missing),
            })
            result.alerts_created.append(alert.id)
    return result


def release_factory_order_lock(
    db: Session, factory_order_id: int, *, actor: str = "system",
    reason: Optional[str] = None,
) -> int:
    """取消 / 作废工厂订单时释放锁定. 按 ledger 倒推 (取所有 kind=lock 的总和减 release/consume).

    返回释放的明细数。
    """
    locked = db.execute(
        select(InventoryLockLedger).where(
            InventoryLockLedger.source_kind == "factory_order",
            InventoryLockLedger.source_id == factory_order_id,
            InventoryLockLedger.kind == "lock",
        )
    ).scalars().all()
    released = db.execute(
        select(InventoryLockLedger).where(
            InventoryLockLedger.source_kind == "factory_order",
            InventoryLockLedger.source_id == factory_order_id,
            InventoryLockLedger.kind.in_(("release", "consume")),
        )
    ).scalars().all()

    # 按物料聚合 net locked
    net: dict[str, Decimal] = {}
    for l in locked:
        net[l.material_code or ""] = net.get(l.material_code or "", Decimal("0")) + Decimal(l.qty)
    for r in released:
        net[r.material_code or ""] = net.get(r.material_code or "", Decimal("0")) - Decimal(r.qty)

    count = 0
    for mat_code, qty in net.items():
        if qty <= 0 or not mat_code:
            continue
        inv = _get_or_create_inventory(db, mat_code)
        inv.locked_qty = max(Decimal(inv.locked_qty or 0) - qty, Decimal("0"))
        _write_ledger(
            db, source_kind="factory_order", source_id=factory_order_id,
            material_code=mat_code, kind="release",
            qty=qty, actor=actor, remark=reason or "订单取消/作废",
        )
        # 释放后可能解决缺货告警 — 重新评估
        if inv.available_qty >= 0:
            alert_service.resolve_by_dedupe(
                db, f"low_stock_part:{mat_code}", resolved_by="auto_after_release",
            )
        count += 1
    return count


def consume_for_shipment(
    db: Session, factory_order_id: int, *, actor: str = "system",
) -> int:
    """实际出货. 把 locked_qty 转成实际扣减: physical -= qty, locked -= qty."""
    locked = db.execute(
        select(InventoryLockLedger).where(
            InventoryLockLedger.source_kind == "factory_order",
            InventoryLockLedger.source_id == factory_order_id,
            InventoryLockLedger.kind == "lock",
        )
    ).scalars().all()
    consumed = db.execute(
        select(InventoryLockLedger).where(
            InventoryLockLedger.source_kind == "factory_order",
            InventoryLockLedger.source_id == factory_order_id,
            InventoryLockLedger.kind == "consume",
        )
    ).scalars().all()

    net: dict[str, Decimal] = {}
    for l in locked:
        net[l.material_code or ""] = net.get(l.material_code or "", Decimal("0")) + Decimal(l.qty)
    for c in consumed:
        net[c.material_code or ""] = net.get(c.material_code or "", Decimal("0")) - Decimal(c.qty)

    count = 0
    for mat_code, qty in net.items():
        if qty <= 0 or not mat_code:
            continue
        inv = _get_or_create_inventory(db, mat_code)
        inv.locked_qty = max(Decimal(inv.locked_qty or 0) - qty, Decimal("0"))
        inv.physical_qty = max(Decimal(inv.physical_qty or 0) - qty, Decimal("0"))
        from datetime import date as _date
        inv.last_outbound_at = _date.today()
        _write_ledger(
            db, source_kind="factory_order", source_id=factory_order_id,
            material_code=mat_code, kind="consume",
            qty=qty, actor=actor, remark="实际出货",
        )
        count += 1
    return count


# ----------------------------- 入库 / 退货 ----------------------- #


def inbound_part(
    db: Session, *, material_code: str, qty,
    actor: str = "system",
    source_kind: str = "manual", source_id: Optional[int] = None,
    warehouse: str = DEFAULT_WAREHOUSE, remark: Optional[str] = None,
) -> PartInventory:
    """物料入库 +physical_qty. 用于采购到货 / 调拨 / 手动. qty 支持 int 或 Decimal."""
    qty_d = Decimal(str(qty))
    if qty_d <= 0:
        raise ValueError("qty 必须 > 0")
    inv = _get_or_create_inventory(db, material_code, warehouse=warehouse)
    inv.physical_qty = Decimal(inv.physical_qty or 0) + qty_d
    from datetime import date as _date
    inv.last_inbound_at = _date.today()
    _write_ledger(
        db, source_kind=source_kind, source_id=source_id,
        material_code=material_code, kind="inbound",
        qty=qty_d, actor=actor, remark=remark,
    )
    # 入库可能解决缺货告警
    if inv.available_qty >= 0:
        alert_service.resolve_by_dedupe(
            db, f"low_stock_part:{material_code}", resolved_by="auto_after_inbound",
        )
    return inv


def return_in_product(
    db: Session, *, product_code: str, sku_code: Optional[str], qty,
    actor: str = "system", source_kind: str = "aftersales",
    source_id: Optional[int] = None,
    warehouse: str = DEFAULT_WAREHOUSE, remark: Optional[str] = None,
) -> ProductInventory:
    """退货完好 → 整产品入库 (不拆 BOM). 业务需求 9.

    用户可后续手动点 "拆 BOM" 时, 调 disassemble_product_to_parts 才拆。
    """
    qty_d = Decimal(str(qty))
    if qty_d <= 0:
        raise ValueError("qty 必须 > 0")
    inv = db.execute(
        select(ProductInventory).where(
            ProductInventory.warehouse == warehouse,
            ProductInventory.product_code == product_code,
            ProductInventory.sku == sku_code if sku_code else
            ProductInventory.product_code == product_code,
        )
    ).scalar_one_or_none() if sku_code else db.execute(
        select(ProductInventory).where(
            ProductInventory.warehouse == warehouse,
            ProductInventory.product_code == product_code,
        )
    ).scalar_one_or_none()
    if inv is None:
        inv = ProductInventory(
            warehouse=warehouse, product_code=product_code, sku=sku_code,
            physical_qty=Decimal("0"), locked_qty=Decimal("0"),
        )
        db.add(inv)
        db.flush()
    inv.physical_qty = Decimal(inv.physical_qty or 0) + qty_d
    _write_ledger(
        db, source_kind=source_kind, source_id=source_id,
        material_code=None, product_code=product_code, sku_code=sku_code,
        kind="return_in", qty=qty_d, actor=actor,
        remark=remark or "退货完好入库",
    )
    return inv


def disassemble_product_to_parts(
    db: Session, *, product_code: str, sku_code: Optional[str], qty,
    actor: str = "system", remark: Optional[str] = None,
) -> dict:
    """业务需求 9: 用户手动点 "拆 BOM" 时调.

    成品 physical_qty -= qty, BOM 展开后每个物料 physical_qty += per * qty。
    """
    qty_d = Decimal(str(qty))
    if qty_d <= 0:
        raise ValueError("qty 必须 > 0")
    pinv = db.execute(
        select(ProductInventory).where(
            ProductInventory.product_code == product_code,
            (ProductInventory.sku == sku_code) if sku_code else
            ProductInventory.product_code == product_code,
        )
    ).scalar_one_or_none()
    if pinv is None or pinv.physical_qty < qty_d:
        raise ValueError(f"成品 {product_code} ({sku_code}) 库存不足 {qty_d}")
    pinv.physical_qty = Decimal(pinv.physical_qty) - qty_d

    bom = _bom_for(db, product_code=product_code, sku_code=sku_code)
    added: list[dict] = []
    for line in bom:
        per = Decimal(line.qty_per_product or "0")
        delta = (per * qty_d).quantize(Decimal("0.001"))
        if delta <= 0:
            continue
        inv = _get_or_create_inventory(db, line.material_code)
        inv.physical_qty = Decimal(inv.physical_qty or 0) + delta
        _write_ledger(
            db, source_kind="disassemble",
            source_id=None, material_code=line.material_code,
            product_code=product_code, sku_code=sku_code,
            kind="inbound", qty=delta,
            actor=actor, remark=remark or f"成品 {product_code} 拆 BOM",
        )
        added.append({"material_code": line.material_code, "qty": float(delta)})
    return {"product_remaining": float(pinv.physical_qty), "parts_added": added}


# ----------------------------- 手动调整 / 盘点 ------------------- #


def manual_adjust(
    db: Session, *, material_code: str, new_physical,
    actor: str, remark: str, warehouse: str = DEFAULT_WAREHOUSE,
) -> PartInventory:
    """业务需求 8: 手动盘库. 调用方需保证已经过二次确认 (UI 层确认)."""
    new_d = Decimal(str(new_physical))
    inv = _get_or_create_inventory(db, material_code, warehouse=warehouse)
    delta = new_d - Decimal(inv.physical_qty or 0)
    if delta == 0:
        return inv
    inv.physical_qty = new_d
    sign = "+" if delta > 0 else ""
    _write_ledger(
        db, source_kind="manual", source_id=None,
        material_code=material_code, kind="count_adjust",
        qty=abs(delta), actor=actor,
        remark=f"盘点 {sign}{delta}: {remark}",
    )
    return inv


# ----------------------------- 查询辅助 -------------------------- #


def ledger_for_factory_order(db: Session, factory_order_id: int) -> list[InventoryLockLedger]:
    return list(db.execute(
        select(InventoryLockLedger).where(
            InventoryLockLedger.source_kind == "factory_order",
            InventoryLockLedger.source_id == factory_order_id,
        ).order_by(InventoryLockLedger.id.asc())
    ).scalars())
