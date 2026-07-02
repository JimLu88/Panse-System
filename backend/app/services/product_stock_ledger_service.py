"""成品现货 自动进出库账本 (R3)。

把「现货」(ProductInventory.physical_qty) 从静态快照, 变成随业务事件自动增减的活账:
  - 出库(ship):            订单发货时, 若该产品有备货现货 → 扣现货 (只动有货的备货款,
                           MTO 款现货=0 时自动 no-op, 不会扣成负数)。
  - 入库(restock_receipt): 备货工厂单(非客户单, source_order_id 为空)到货 → 加现货。
                           客户单(MTO)到货直接发给客户、不进可售现货, 故不加。
  - 冲正(reversal):        撤销发货 / 作废工厂单 → 反向一笔, 现货复原。

每笔都落 ProductStockMovement 流水 (唯一 reason+entity 保证幂等), physical_qty 同步增减。
physical_qty 仍是权威值; 盘点/导入直接覆盖它 = 设新基线, 之后的流水在新基线上继续。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.inventory import ProductInventory, ProductStockMovement
from app.services import product_coder

_ZERO = Decimal("0")


def _has_movement(db: Session, reason: str, entity_type: str, entity_id: int) -> bool:
    return db.execute(
        select(ProductStockMovement.id).where(
            ProductStockMovement.reason == reason,
            ProductStockMovement.entity_type == entity_type,
            ProductStockMovement.entity_id == entity_id,
        ).limit(1)
    ).scalar_one_or_none() is not None


def _pick_stock_row(db: Session, product_code: str, sku: Optional[str] = None,
                    warehouse: Optional[str] = None) -> Optional[ProductInventory]:
    """找该产品的成品库存行(品牌变体归并)。优先 sku 精确匹配, 否则取现货最多的一行。"""
    if not product_code:
        return None
    codes = product_coder.brand_variants(product_code) or {product_code}
    stmt = select(ProductInventory).where(ProductInventory.product_code.in_(codes))
    if warehouse:
        stmt = stmt.where(ProductInventory.warehouse == warehouse)
    rows = db.execute(stmt).scalars().all()
    if not rows:
        return None
    if sku:
        exact = [r for r in rows if (r.sku or "") == sku]
        if exact:
            return max(exact, key=lambda r: Decimal(r.physical_qty or 0))
    return max(rows, key=lambda r: Decimal(r.physical_qty or 0))


def _add_movement(db: Session, *, warehouse: str, product_code: str, qty: Decimal,
                  reason: str, entity_type: str, entity_id: int,
                  remark: Optional[str] = None) -> Optional[ProductStockMovement]:
    """插一笔流水(幂等: 撞唯一键则回滚该 savepoint 并返回 None, 不污染外层事务)。"""
    mv = ProductStockMovement(
        warehouse=warehouse, product_code=product_code, qty=qty, reason=reason,
        entity_type=entity_type, entity_id=entity_id, occurred_on=date.today(), remark=remark,
    )
    try:
        with db.begin_nested():
            db.add(mv)
    except IntegrityError:
        return None
    return mv


def record_shipment(db: Session, order) -> dict:
    """订单发货 → 出库扣现货 (只扣有备货现货的款; 幂等)。返回 {deducted, product_code}。"""
    pc = getattr(order, "product_code", None)
    oid = getattr(order, "id", None)
    if not pc or oid is None:
        return {"deducted": 0.0, "reason": "no_product_or_id"}
    if _has_movement(db, "ship", "order", oid):
        return {"deducted": 0.0, "reason": "already_recorded"}
    row = _pick_stock_row(db, pc, getattr(order, "sku", None))
    if row is None:
        return {"deducted": 0.0, "reason": "no_stock_row"}
    have = Decimal(row.physical_qty or 0)
    if have <= _ZERO:
        return {"deducted": 0.0, "reason": "no_stock_on_hand"}   # MTO 款: 无现货可扣, no-op
    want = Decimal(str(getattr(order, "qty", 1) or 1))
    take = min(want, have)                                       # 只扣到 0, 绝不为负
    mv = _add_movement(
        db, warehouse=row.warehouse, product_code=row.product_code, qty=-take,
        reason="ship", entity_type="order", entity_id=oid,
        remark=f"订单 {getattr(order, 'order_no', oid)} 发货扣现货",
    )
    if mv is None:
        return {"deducted": 0.0, "reason": "race_skipped"}
    row.physical_qty = have - take
    row.last_outbound_at = date.today()
    return {"deducted": float(take), "product_code": row.product_code, "left": float(row.physical_qty)}


def record_restock_receipt(db: Session, fo) -> dict:
    """备货工厂单(非客户单)到货 → 入库加现货 (幂等)。客户单(MTO)不加。返回 {added}。"""
    pc = getattr(fo, "product_code", None)
    fid = getattr(fo, "id", None)
    if not pc or fid is None:
        return {"added": 0.0, "reason": "no_product_or_id"}
    if getattr(fo, "source_order_id", None) is not None:
        return {"added": 0.0, "reason": "mto_not_restock"}      # 客户单到货直接发客户, 不进可售现货
    if _has_movement(db, "restock_receipt", "factory_order", fid):
        return {"added": 0.0, "reason": "already_recorded"}
    add = Decimal(str(getattr(fo, "qty", 0) or 0))
    if add <= _ZERO:
        return {"added": 0.0, "reason": "zero_qty"}
    row = _pick_stock_row(db, pc)
    if row is None:                                             # 首次备货入库 → 建库存行
        canon = min(product_coder.brand_variants(pc) or {pc})
        row = ProductInventory(warehouse="default", product_code=canon,
                               sku=getattr(fo, "sku", None), physical_qty=_ZERO)
        db.add(row)
        db.flush()
    mv = _add_movement(
        db, warehouse=row.warehouse, product_code=row.product_code, qty=add,
        reason="restock_receipt", entity_type="factory_order", entity_id=fid,
        remark=f"备货工厂单 {getattr(fo, 'factory_order_no', fid)} 到货入库",
    )
    if mv is None:
        return {"added": 0.0, "reason": "race_skipped"}
    row.physical_qty = Decimal(row.physical_qty or 0) + add
    row.last_inbound_at = date.today()
    return {"added": float(add), "product_code": row.product_code, "now": float(row.physical_qty)}


def reverse(db: Session, reason: str, entity_type: str, entity_id: int, *,
            note: str = "") -> dict:
    """冲正某笔已记录的出/入库(撤销发货/作废工厂单): 反向调整现货 + 记一笔 reversal。幂等。"""
    orig = db.execute(
        select(ProductStockMovement).where(
            ProductStockMovement.reason == reason,
            ProductStockMovement.entity_type == entity_type,
            ProductStockMovement.entity_id == entity_id,
        ).limit(1)
    ).scalar_one_or_none()
    if orig is None:
        return {"reversed": 0.0, "reason": "nothing_to_reverse"}
    if _has_movement(db, "reversal", entity_type, entity_id):
        return {"reversed": 0.0, "reason": "already_reversed"}
    back = -Decimal(orig.qty or 0)                              # 反向
    row = _pick_stock_row(db, orig.product_code, warehouse=orig.warehouse)
    mv = _add_movement(
        db, warehouse=orig.warehouse, product_code=orig.product_code, qty=back,
        reason="reversal", entity_type=entity_type, entity_id=entity_id,
        remark=(note or f"冲正 {reason} {entity_type}#{entity_id}")[:255],
    )
    if mv is None:
        return {"reversed": 0.0, "reason": "race_skipped"}
    if row is not None:
        # 反向为负(冲正入库)时不许扣成负数
        row.physical_qty = max(Decimal(row.physical_qty or 0) + back, _ZERO)
    return {"reversed": float(back), "product_code": orig.product_code}


def check_negative_stock(db: Session) -> list[dict]:
    """自愈检查器: 列出现货为负的成品库存行(理论上不应出现, 出库已 floor)。"""
    rows = db.execute(
        select(ProductInventory).where(ProductInventory.physical_qty < 0)
    ).scalars().all()
    return [{"id": r.id, "product_code": r.product_code, "sku": r.sku,
             "physical_qty": float(r.physical_qty)} for r in rows]
