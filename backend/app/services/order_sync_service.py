"""订单总表跨表纠错/回填 (Phase 2).

三个显式操作 (导入后手动触发, 幂等):

1. rederive_refill_flags — 以 8-补单记录 为准重判「是否补单」:
   订单号在补单记录里 → is_refill=True, 否则 False。改动的订单顺带重算理论成本
   (补单归 0; 取消补单标记的订单回到 BOM/定价口径)。返回明细供人工复核。

2. backfill_compensation_from_aftersales — 把 18-售后表 的赔付汇总回写订单:
   按平台订单号聚合售后赔付 (优先 compensation_fee, 否则 工厂补偿+物流补偿),

3. mark_custom_sku_suffix — is_custom=True 的订单, 若 sku 未以「改」结尾则追加「改」字后缀
   写入 Order.compensation_fee。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import RefillRecord
from app.models.marketing import AfterSales
from app.models.order import Order
from app.services import order_cost_service

_logger = logging.getLogger("panse.order_sync")


@dataclass
class RefillRederiveResult:
    scanned: int = 0
    flagged: int = 0            # 新增补单标记 (在补单记录里, 之前未标)
    unflagged: int = 0          # 取消补单标记 (不在补单记录, 之前误标)
    flagged_orders: list[str] = field(default_factory=list)
    unflagged_orders: list[str] = field(default_factory=list)


def rederive_refill_flags(db: Session, *, recompute_cost: bool = True) -> RefillRederiveResult:
    """以补单记录为准重判 is_refill。改动的订单可选顺带重算理论成本。"""
    refill_nos = {
        str(o).strip()
        for o in db.execute(select(RefillRecord.order_no)).scalars().all()
        if o
    }
    orders = db.execute(select(Order)).scalars().all()
    res = RefillRederiveResult(scanned=len(orders))
    changed: list[Order] = []
    for o in orders:
        should = (o.order_no or "").strip() in refill_nos
        if should and not o.is_refill:
            o.is_refill = True
            res.flagged += 1
            res.flagged_orders.append(o.order_no)
            changed.append(o)
        elif not should and o.is_refill:
            o.is_refill = False
            res.unflagged += 1
            res.unflagged_orders.append(o.order_no)
            changed.append(o)
    if recompute_cost:
        for o in changed:
            order_cost_service.recompute_and_save(db, o)
    db.flush()
    _logger.info("补单标记重判: 扫描%d 新增标记%d 取消标记%d",
                 res.scanned, res.flagged, res.unflagged)
    return res


@dataclass
class CompensationBackfillResult:
    orders_updated: int = 0
    aftersales_scanned: int = 0
    total_compensation: Decimal = Decimal("0")


def _aftersales_comp(a: AfterSales) -> Decimal:
    """单条售后的赔付额: 优先 订单赔付费, 否则 工厂补偿+物流补偿。"""
    if a.compensation_fee is not None:
        return a.compensation_fee
    return (a.factory_compensation or Decimal("0")) + (a.logistics_compensation or Decimal("0"))


def backfill_compensation_from_aftersales(db: Session) -> CompensationBackfillResult:
    """把售后表赔付按平台订单号聚合, 回写 Order.compensation_fee。"""
    rows = db.execute(select(AfterSales)).scalars().all()
    by_order: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for a in rows:
        ono = (a.platform_order_no or "").strip()
        if not ono:
            continue
        by_order[ono] += _aftersales_comp(a)
    res = CompensationBackfillResult(aftersales_scanned=len(rows))
    for ono, comp in by_order.items():
        if comp == 0:
            continue
        order = db.execute(
            select(Order).where(Order.order_no == ono)
        ).scalar_one_or_none()
        if order is None:
            continue
        order.compensation_fee = comp
        res.orders_updated += 1
        res.total_compensation += comp
    db.flush()
    _logger.info("售后赔付回写: 售后%d 条 → 更新订单%d 笔, 合计赔付%s",
                 res.aftersales_scanned, res.orders_updated, res.total_compensation)
    return res


def refresh_order_compensation(db: Session, order_no: str) -> bool:
    """单订单版赔付回写 (Plan L4): 售后增改后只刷新该单, 避免全表扫。

    汇总该平台订单号的所有售后赔付 → Order.compensation_fee → 重算成本 → 记 pnl_refreshed 事件。
    返回是否有实际改动。
    """
    ono = (order_no or "").strip()
    if not ono:
        return False
    order = db.execute(
        select(Order).where(Order.order_no == ono)
    ).scalar_one_or_none()
    if order is None:
        return False
    rows = db.execute(
        select(AfterSales).where(AfterSales.platform_order_no == ono)
    ).scalars().all()
    comp = sum((_aftersales_comp(a) for a in rows), Decimal("0"))
    if (order.compensation_fee or Decimal("0")) == comp:
        return False
    order.compensation_fee = comp
    order_cost_service.recompute_and_save(db, order)
    try:
        from app.services import order_event_service
        order_event_service.record(
            db, order_id=order.id, kind="pnl_refreshed",
            summary=f"售后赔付变动 → 利润已刷新 (赔付合计 {comp})",
        )
    except Exception:  # pragma: no cover - 事件记录失败不阻断回写
        _logger.warning("pnl_refreshed 事件记录失败 order=%s", ono, exc_info=True)
    db.flush()
    return True


def backfill_product_code(db: Session) -> int:
    """订单缺 product_code 但 sku_code 能在定价表对上 → 回填 product_code (导入丢编码的补救)。

    用户拍板 2026-06-17: 排行/销售汇总按产品分组+显示内部短名都靠 product_code, 缺了就显淘宝长标题、
    且同款拆成多行。sku_code 本就内含产品编码(如 PPS2398001060612 = PPS23980010606 + sku 12), 经
    定价表 PricingSku(sku_code→product_code) 精确回填。幂等。
    """
    from app.models.pricing import PricingSku
    sku2code = {s: c for s, c in db.execute(
        select(PricingSku.sku_code, PricingSku.product_code)).all() if s}
    n = 0
    for o in db.execute(
        select(Order).where(Order.product_code.is_(None), Order.sku_code.isnot(None))
    ).scalars().all():
        code = sku2code.get(o.sku_code)
        if code:
            o.product_code = code
            n += 1
    if n:
        db.flush()
    _logger.info("backfill_product_code: 回填 %d 条订单", n)
    return n


def backfill_warehouse(db: Session) -> int:
    """存量订单仓库回填: 对 warehouse 为空的订单用 default_warehouse_for 自动判定。幂等。"""
    orders = db.execute(
        select(Order).where(Order.warehouse.is_(None))
    ).scalars().all()
    updated = 0
    for o in orders:
        wh = order_cost_service.default_warehouse_for(o.product_name, o.sku, o.is_refill)
        o.warehouse = wh
        updated += 1
    db.flush()
    _logger.info("仓库回填: 更新 %d 条订单", updated)
    return updated


def mark_custom_sku_suffix(db: Session) -> int:
    """is_custom=True 的订单，若 sku 未以「改」结尾则追加「改」后缀。幂等。

    例: PPS2633007032011 → PPS2633007032011-改
    """
    orders = db.execute(
        select(Order).where(Order.is_custom == True)  # noqa: E712
    ).scalars().all()
    updated = 0
    for o in orders:
        if o.sku and not o.sku.endswith("改") and not o.sku.endswith("-改"):
            o.sku = o.sku + "-改"
            updated += 1
    db.flush()
    _logger.info("微定制 SKU 后缀标注: 共更新 %d 条订单", updated)
    return updated
