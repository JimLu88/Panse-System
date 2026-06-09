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
# 木作料号(WD-*): 由木作厂自制, 默认已备(不当外购缺料); 占位时显示"木作部分"
_WOODWORK_PREFIXES = ("WD",)
_WOODWORK_DEFAULT_STATUS = "已到货"
# 触发采购预警的提前天数
_ALERT_WARN_DAYS = 5
_ALERT_CRITICAL_DAYS = 2


# 物料库里"占位/待补"型伪名 (导 BOM 时引用了库里还没有的料号会自动建 "占位 (XX)" 行,
# 等导物料表再用真名覆盖)。这类名字没意义, 显示时应退回 BOM 行里写的人话名。
_PLACEHOLDER_NAMES = {"待补", "—", "-", "?", "？", "待定", "未知"}


def _is_placeholder_name(name: Optional[str]) -> bool:
    s = (name or "").strip()
    return (not s) or s.startswith("占位") or s in _PLACEHOLDER_NAMES


def _resolve_name(mat_name: Optional[str], line: BomLine) -> Optional[str]:
    """配件显示名: 优先用物料库真实名; 物料库还是占位/空 → 退回 BOM 行里写的名(常含人话描述)。"""
    if not _is_placeholder_name(mat_name):
        return mat_name
    if not _is_placeholder_name(line.material_name):
        return line.material_name
    # 木作料号(WD-*)还是占位 → 用清晰全名"木作部分", 不显示"占位"
    if (line.material_code or "").upper().startswith("WD"):
        return "木作部分"
    return mat_name or line.material_name or line.material_code


def _bom_rows_for_order(db: Session, order: Order) -> list:
    """取该订单对应的 BOM 行。

    关键: 优先按 product_code(+sku_code) 精确匹配 —— 一个 sku_code 可能在 BOM 里挂了
    多个产品(脏数据), 只按 sku_code 抓会把多个产品的料串在一起。product_code 缺失时才退回 sku_code。
    """
    base = (
        select(BomLine, Material.name.label("mat_name"), Material.unit.label("mat_unit"))
        .join(Material, BomLine.material_code == Material.code, isouter=True)
    )
    if order.product_code:
        conds = [BomLine.product_code == order.product_code]
        if order.sku_code:
            conds.append(BomLine.sku_code == order.sku_code)
        rows = db.execute(base.where(*conds)).all()
        if rows:
            return rows
        # (product_code + sku_code) 无果 → 退回仅 product_code (BOM 可能没填 sku_code)
        rows = db.execute(base.where(BomLine.product_code == order.product_code)).all()
        if rows:
            return rows
    if order.sku_code:
        return db.execute(base.where(BomLine.sku_code == order.sku_code)).all()
    return []


def _bom_item_fields(order: Order, line: BomLine, mat_name, mat_unit) -> dict:
    """从一条 BOM 行算出配件清单行的字段 (名字/数量/单位/是否工厂提供/状态)。"""
    prefix = line.material_code.split("-", 1)[0].upper()
    factory_provided = prefix in _FACTORY_PREFIXES
    if factory_provided:
        status = "工厂提供"
    elif prefix in _WOODWORK_PREFIXES:
        status = _WOODWORK_DEFAULT_STATUS   # 木作默认已备, 不当外购缺料(用户可改)
    else:
        status = "未采购"
    return {
        "material_name": _resolve_name(mat_name, line),
        "qty_required": Decimal(line.qty_per_product or 1) * Decimal(order.qty or 1),
        "unit": line.unit or mat_unit,
        "is_factory_provided": factory_provided,
        "status_default": status,
    }


def generate_for_order(db: Session, order_id: int) -> list[OrderAccessoryItem]:
    """为订单生成配件清单行（幂等：已存在的行不重复创建）。

    返回本次新建的行。按 product_code(+sku_code) 取 BOM, 避免一个 sku_code 挂多产品时串料。
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"order {order_id} not found")
    if not (order.product_code or order.sku_code):
        return []

    bom_rows = _bom_rows_for_order(db, order)
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
        existing.add(line.material_code)   # 防同一 BOM 里重复料号触发唯一约束
        f = _bom_item_fields(order, line, mat_name, mat_unit)
        item = OrderAccessoryItem(
            order_id=order_id,
            order_no=order.order_no,
            material_code=line.material_code,
            material_name=f["material_name"],
            qty_required=f["qty_required"],
            unit=f["unit"],
            is_factory_provided=f["is_factory_provided"],
            source="bom",
            status=f["status_default"],
        )
        db.add(item)
        created.append(item)

    if created:
        db.commit()
        _logger.info("订单 %s 生成配件清单 %d 行", order.order_no, len(created))
    return created


def resync_for_order(db: Session, order_id: int) -> list[OrderAccessoryItem]:
    """按当前 BOM 重新对齐配件清单 (source=bom): 刷新名字/数量、删掉不在 BOM 里的串料行、补齐缺失。

    保留 source=客户备注 的行, 以及已对上料号的行上已填的采购/物流状态 —— 只修错的, 不丢进度。
    用于修复历史脏数据(如一个 sku_code 串了两个产品的料)。
    """
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"order {order_id} not found")

    correct: dict[str, tuple] = {}
    for line, mat_name, mat_unit in _bom_rows_for_order(db, order):
        correct.setdefault(line.material_code, (line, mat_name, mat_unit))

    existing = {
        it.material_code: it
        for it in db.execute(
            select(OrderAccessoryItem).where(
                OrderAccessoryItem.order_id == order_id,
                OrderAccessoryItem.source == "bom",
            )
        ).scalars().all()
    }

    removed = 0
    for code, it in existing.items():
        if code not in correct:        # BOM 里已没有的料(串料/旧数据) → 删
            db.delete(it)
            removed += 1

    for code, (line, mat_name, mat_unit) in correct.items():
        f = _bom_item_fields(order, line, mat_name, mat_unit)
        it = existing.get(code)
        if it is not None:             # 已存在且 BOM 仍有 → 刷新名字/数量/单位, 保留状态/快递
            it.material_name = f["material_name"]
            it.qty_required = f["qty_required"]
            it.unit = f["unit"]
            it.is_factory_provided = f["is_factory_provided"]
            # 木作历史行还停在旧默认"未采购"(没人工动过) → 升级为新默认(已备), 不再当外购缺料
            if it.status == "未采购" and (code or "").upper().startswith("WD"):
                it.status = _WOODWORK_DEFAULT_STATUS
        else:                          # BOM 有但清单缺 → 补
            db.add(OrderAccessoryItem(
                order_id=order_id, order_no=order.order_no, material_code=code,
                material_name=f["material_name"], qty_required=f["qty_required"],
                unit=f["unit"], is_factory_provided=f["is_factory_provided"],
                source="bom", status=f["status_default"],
            ))

    db.commit()
    _logger.info("订单 %s 配件重对齐: 删 %d 行串料, 现 %d 行 BOM 料", order.order_no, removed, len(correct))
    return get_checklist(db, order_id)


def add_extra_accessories(
    db: Session, order_id: int, extra: list[dict]
) -> list[OrderAccessoryItem]:
    """把截图 OCR 备注里识别的新增配件加入配件清单 (source=客户备注)。

    每项 {name, qty?, note?}。已存在同编码则跳过。返回新建行。
    """
    if not extra:
        return []
    order = db.get(Order, order_id)
    if not order:
        raise ValueError(f"order {order_id} not found")

    existing = {
        c for c in db.execute(
            select(OrderAccessoryItem.material_code).where(
                OrderAccessoryItem.order_id == order_id
            )
        ).scalars().all()
    }
    created: list[OrderAccessoryItem] = []
    for item in extra:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        try:
            per = Decimal(str(item.get("qty") or 1))
        except (ValueError, ArithmeticError):
            per = Decimal(1)
        mat = db.execute(select(Material).where(Material.name == name)).scalar_one_or_none()
        code = mat.code if mat else f"NEW-{name[:8]}"
        if code in existing:
            continue
        existing.add(code)
        row = OrderAccessoryItem(
            order_id=order_id,
            order_no=order.order_no,
            material_code=code,
            material_name=mat.name if mat else name,
            qty_required=per,   # 备注配件是整单绝对数量, 不乘订单件数
            unit=mat.unit if mat else None,
            is_factory_provided=False,
            source="客户备注",
            status="未采购",
            remark=item.get("note") or None,
        )
        db.add(row)
        created.append(row)

    if created:
        db.commit()
        _logger.info("订单 %s 加入客户备注配件 %d 项", order.order_no, len(created))
    return created


def summary_by_order(db: Session) -> dict[int, dict]:
    """看板用: 每个(已生成配件的)订单的配齐进度 {order_id: {total, done, pending}}。

    done = 状态为「已到货」或「工厂提供」。没生成配件行的订单不在结果里(前端按"未生成"处理)。
    """
    from sqlalchemy import case, func
    rows = db.execute(
        select(
            OrderAccessoryItem.order_id,
            func.count().label("total"),
            func.sum(case((OrderAccessoryItem.status.in_(["已到货", "工厂提供"]), 1), else_=0)).label("done"),
        ).group_by(OrderAccessoryItem.order_id)
    ).all()
    out: dict[int, dict] = {}
    for oid, total, done in rows:
        t, d = int(total or 0), int(done or 0)
        out[oid] = {"total": t, "done": d, "pending": t - d}
    return out


def _fmt_qty(d: Decimal) -> str:
    """数量去掉尾零(Numeric(12,4) 会带 .0000), 不用科学计数法。2.0000→'2', 2.5000→'2.5'。"""
    s = f"{d:f}"
    return (s.rstrip("0").rstrip(".") if "." in s else s) or "0"


def by_component(db: Session) -> list[dict]:
    """按配件聚合(跨订单)采购视图: 还需采购/在途的配件, 按料号汇总缺多少 + 涉及哪些订单。

    只统计 需采购(非工厂提供) 且 还没到货(状态 未采购/已下单/运输中) 的行。
    每个料号给出: 待买量(未采购) / 已买未到量(已下单+运输中) / 涉及订单明细。按名称排序。
    """
    rows = db.execute(
        select(OrderAccessoryItem).where(
            OrderAccessoryItem.is_factory_provided.is_(False),
            OrderAccessoryItem.status.in_(["未采购", "已下单", "运输中"]),
        )
    ).scalars().all()
    groups: dict[str, dict] = {}
    for it in rows:
        g = groups.get(it.material_code)
        if g is None:
            g = groups[it.material_code] = {
                "material_code": it.material_code, "material_name": it.material_name,
                "unit": it.unit, "to_buy_qty": Decimal(0), "bought_pending_qty": Decimal(0),
                "order_count": 0, "items": [],
            }
        qty = it.qty_required or Decimal(0)
        if it.status == "未采购":
            g["to_buy_qty"] += qty
        else:
            g["bought_pending_qty"] += qty
        g["order_count"] += 1
        g["items"].append({
            "id": it.id, "order_id": it.order_id, "order_no": it.order_no,
            "qty_required": _fmt_qty(qty), "status": it.status, "purchase_no": it.purchase_no,
            "tracking_no": it.tracking_no, "self_delivered": it.self_delivered,
        })
    out = list(groups.values())
    for g in out:
        g["to_buy_qty"] = _fmt_qty(g["to_buy_qty"])
        g["bought_pending_qty"] = _fmt_qty(g["bought_pending_qty"])
    out.sort(key=lambda g: g["material_name"] or g["material_code"])
    return out


def bulk_update(db: Session, item_ids: list[int], *, status: Optional[str] = None,
                purchase_no: Optional[str] = None, tracking_no: Optional[str] = None,
                self_delivered: Optional[bool] = None) -> int:
    """批量更新配件行 (聚合采购视图里勾选若干单一起标 已购买/已到货/自送/填单号)。返回更新数。"""
    if not item_ids:
        return 0
    items = db.execute(
        select(OrderAccessoryItem).where(OrderAccessoryItem.id.in_(item_ids))
    ).scalars().all()
    for it in items:
        if status is not None:
            it.status = status
        if purchase_no is not None:
            it.purchase_no = purchase_no or None
        if tracking_no is not None:
            it.tracking_no = tracking_no or None
        if self_delivered is not None:
            it.self_delivered = self_delivered
            if self_delivered:
                it.tracking_no = None   # 自送无物流号
    db.commit()
    return len(items)


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
