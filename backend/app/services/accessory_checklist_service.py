"""订单配件清单服务 — 按 BOM 自动生成每单配件行，追踪采购/物流状态。

规则:
  AC-* / SP-* → 需采购，初始状态「未采购」
  MW-* / MP-* → 工厂提供，状态「工厂提供」，is_factory_provided=True
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order, OrderAccessoryItem

_logger = logging.getLogger("panse.accessory_checklist")

# 工厂自备前缀 (不需要外部采购)
_FACTORY_PREFIXES = ("MW", "MP")
# 触发采购预警的提前天数
_ALERT_WARN_DAYS = 5
_ALERT_CRITICAL_DAYS = 2


def generate_for_order(db: Session, order_id: int) -> list[OrderAccessoryItem]:
    """为订单生成配件清单行（幂等：已存在的行不重复创建）。

    返回本次新建的行。
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"order {order_id} not found")

    sku_code = order.sku_code
    if not sku_code:
        return []

    bom_rows = db.execute(
        select(BomLine, Material.name.label("mat_name"), Material.unit.label("mat_unit"))
        .join(Material, BomLine.material_code == Material.code, isouter=True)
        .where(BomLine.sku_code == sku_code)
    ).all()

    existing = {
        row.material_code
        for row in db.execute(
            select(OrderAccessoryItem.material_code).where(
                OrderAccessoryItem.order_id == order_id
            )
        ).all()
    }

    created: list[OrderAccessoryItem] = []
    for line, mat_name, mat_unit in bom_rows:
        if line.material_code in existing:
            continue
        prefix = line.material_code.split("-", 1)[0].upper()
        factory_provided = prefix in _FACTORY_PREFIXES
        item = OrderAccessoryItem(
            order_id=order_id,
            order_no=order.order_no,
            material_code=line.material_code,
            material_name=mat_name or line.material_name,
            qty_required=Decimal(line.qty_per_product or 1) * Decimal(order.qty or 1),
            unit=line.unit or mat_unit,
            is_factory_provided=factory_provided,
            status="工厂提供" if factory_provided else "未采购",
        )
        db.add(item)
        created.append(item)

    if created:
        db.commit()
        _logger.info("订单 %s 生成配件清单 %d 行", order.order_no, len(created))
    return created


def get_checklist(db: Session, order_id: int) -> list[OrderAccessoryItem]:
    return list(
        db.execute(
            select(OrderAccessoryItem)
            .where(OrderAccessoryItem.order_id == order_id)
            .order_by(OrderAccessoryItem.is_factory_provided, OrderAccessoryItem.material_code)
        ).scalars().all()
    )


def update_item(
    db: Session,
    item_id: int,
    *,
    status: Optional[str] = None,
    tracking_no: Optional[str] = None,
    carrier_code: Optional[str] = None,
    carrier_name: Optional[str] = None,
    remark: Optional[str] = None,
    part_purchase_id: Optional[int] = None,
) -> OrderAccessoryItem:
    item = db.get(OrderAccessoryItem, item_id)
    if not item:
        raise ValueError(f"accessory item {item_id} not found")

    if status is not None:
        item.status = status
    if tracking_no is not None:
        item.tracking_no = tracking_no or None
        # 填了快递单号自动升级状态
        if tracking_no and item.status == "已下单":
            item.status = "运输中"
        elif tracking_no and item.status == "未采购":
            item.status = "运输中"
    if carrier_code is not None:
        item.carrier_code = carrier_code or None
    if carrier_name is not None:
        item.carrier_name = carrier_name or None
    if remark is not None:
        item.remark = remark or None
    if part_purchase_id is not None:
        item.part_purchase_id = part_purchase_id

    db.commit()
    db.refresh(item)
    _refresh_alert(item)
    db.commit()
    return item


def _refresh_alert(item: OrderAccessoryItem) -> None:
    """根据当前状态和发货日期更新预警等级（直接修改对象，调用方需 commit）。"""
    if item.status in ("已到货", "工厂提供"):
        item.alert_level = None
        item.alert_reason = None
        return

    order = item.__dict__.get("_order_cache")
    # 需要 ship_date: 通过 session 加载
    from sqlalchemy.orm import object_session
    sess = object_session(item)
    if sess is None:
        return
    order = sess.get(Order, item.order_id)
    if not order or not order.ship_date:
        item.alert_level = None
        item.alert_reason = None
        return

    days_left = (order.ship_date - date.today()).days
    if days_left <= _ALERT_CRITICAL_DAYS:
        item.alert_level = "critical"
        item.alert_reason = f"距发货仅 {days_left} 天，配件未到货"
    elif days_left <= _ALERT_WARN_DAYS:
        item.alert_level = "warn"
        item.alert_reason = f"距发货 {days_left} 天，建议尽快确认到货"
    else:
        item.alert_level = None
        item.alert_reason = None


def refresh_all_alerts(db: Session) -> int:
    """刷新所有未到货配件行的预警等级，返回更新数量。"""
    items = list(
        db.execute(
            select(OrderAccessoryItem).where(
                OrderAccessoryItem.status.notin_(["已到货", "工厂提供"])
            )
        ).scalars().all()
    )
    for item in items:
        _refresh_alert(item)
    db.commit()
    return len(items)


def get_summary(db: Session) -> list[dict]:
    """跨订单汇总：返回有未到货配件的订单摘要列表（按紧急程度排序）。"""
    rows = db.execute(
        select(OrderAccessoryItem).where(
            OrderAccessoryItem.status.notin_(["已到货", "工厂提供"])
        ).order_by(OrderAccessoryItem.alert_level.desc().nullslast())
    ).scalars().all()

    by_order: dict[int, dict] = {}
    for item in rows:
        if item.order_id not in by_order:
            order = db.get(Order, item.order_id)
            by_order[item.order_id] = {
                "order_id": item.order_id,
                "order_no": item.order_no,
                "ship_date": order.ship_date.isoformat() if order and order.ship_date else None,
                "product_name": order.product_name if order else None,
                "pending_items": [],
                "critical_count": 0,
                "warn_count": 0,
                "missing_tracking_count": 0,
            }
        entry = by_order[item.order_id]
        entry["pending_items"].append({
            "id": item.id,
            "material_code": item.material_code,
            "material_name": item.material_name,
            "status": item.status,
            "alert_level": item.alert_level,
            "tracking_no": item.tracking_no,
        })
        if item.alert_level == "critical":
            entry["critical_count"] += 1
        elif item.alert_level == "warn":
            entry["warn_count"] += 1
        if item.status in ("运输中",) and not item.tracking_no:
            entry["missing_tracking_count"] += 1
        elif item.status in ("已下单", "未采购") and not item.tracking_no:
            entry["missing_tracking_count"] += 1

    result = list(by_order.values())
    result.sort(key=lambda x: (-(x["critical_count"]), -(x["warn_count"]), x["ship_date"] or "9999"))
    return result
