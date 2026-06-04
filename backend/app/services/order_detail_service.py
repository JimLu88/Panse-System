"""订单细节表自动生成 — 不再手工导入, 由 订单 + BOM 联表推导.

业务背景:
    订单细节表记录"每个订单 → 用到哪些物料 (BOM 分解)"的行级关系。
    用户不应手填这张表: 订单总表里已有 产品编码 / SKU, BOM 表里已有 产品→物料 的分解,
    两者一关联就能自动算出每个订单要用哪些物料。

推导规则:
    对每张订单 (Order), 用 product_code (+ sku_code 若有) 去 BomLine 找匹配的物料行:
      - BOM 行 sku_code 命中订单 sku_code → 精确匹配
      - BOM 行 sku_code 为空 → 视为该产品所有 SKU 通用
    每条匹配的 BOM 行生成一条 OrderDetail。

幂等:
    用 sync_key = "auto:{order_no}:{material_code}" 去重, 重复生成只补缺、不重复插。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.order import FactoryOrder, Order, OrderDetail

_logger = logging.getLogger("panse.order_detail")


@dataclass
class GenerateReport:
    orders_scanned: int = 0          # 扫描的订单数
    orders_matched: int = 0          # 找到 BOM 的订单数
    details_created: int = 0         # 新生成的细节行数
    details_skipped: int = 0         # 已存在跳过的行数
    orders_no_bom: list[str] = field(default_factory=list)   # 没找到 BOM 的订单号
    orders_no_product: int = 0       # 没填产品编码的订单数


def _bom_lines_for(db: Session, product_code: str, sku_code: Optional[str]) -> list[BomLine]:
    """取某产品 (+SKU) 的 BOM 行.

    一个 SKU 的物料 = 该 SKU 专属行 (sku_code 命中) + 通用行 (BOM 未指定 SKU, 全 SKU 共用)。
    若该产品根本没有任何 SKU 级 BOM, 则退回全部行。
    """
    rows = db.execute(
        select(BomLine).where(BomLine.product_code == product_code)
    ).scalars().all()
    if not rows:
        return []
    has_sku_level = any(b.sku_code for b in rows)
    if not has_sku_level:
        return rows
    # SKU 专属行 + 通用行 (sku_code 为空)
    return [b for b in rows if (sku_code and b.sku_code == sku_code) or not b.sku_code]


def generate(
    db: Session,
    *,
    order_nos: Optional[list[str]] = None,
    only_missing: bool = True,
) -> GenerateReport:
    """从 订单 + BOM 生成订单细节.

    order_nos: 只处理指定订单号; None = 全部订单。
    only_missing: True 时跳过已生成过细节的订单 (幂等增量); False 时仍按 sync_key 去重。
    """
    report = GenerateReport()

    q = select(Order)
    if order_nos:
        q = q.where(Order.order_no.in_(order_nos))
    orders = db.execute(q).scalars().all()

    # 预取已存在的 auto sync_key, 避免逐行查库
    existing_keys = set(db.execute(
        select(OrderDetail.sync_key).where(OrderDetail.sync_key.like("auto:%"))
    ).scalars().all())

    # 预取工厂订单号: platform_order_no → factory_order_no (取最新一条)
    factory_no_map: dict[str, str] = {}
    for fo in db.execute(select(FactoryOrder).where(FactoryOrder.platform_order_no.isnot(None))).scalars().all():
        if fo.platform_order_no and fo.factory_order_no:
            factory_no_map[fo.platform_order_no] = fo.factory_order_no

    for order in orders:
        report.orders_scanned += 1
        if not order.product_code:
            report.orders_no_product += 1
            continue
        bom_lines = _bom_lines_for(db, order.product_code, order.sku_code)
        if not bom_lines:
            report.orders_no_bom.append(order.order_no)
            continue
        report.orders_matched += 1
        for b in bom_lines:
            key = f"auto:{order.order_no}:{b.material_code}"
            if key in existing_keys:
                report.details_skipped += 1
                continue
            db.add(OrderDetail(
                sync_key=key,
                order_no=order.order_no,
                factory_order_no=factory_no_map.get(order.order_no),
                product_code=order.product_code,
                product_name=order.product_name or b.product_name,
                sku_code=order.sku_code,
                sku_name=order.sku,
                bom_material_code=b.material_code,
                material_name=b.material_name,
                remark="自动生成 (订单+BOM)",
            ))
            existing_keys.add(key)
            report.details_created += 1

    db.flush()
    _logger.info(
        "订单细节生成: 扫描 %d 单, 命中BOM %d 单, 新建 %d 行, 跳过 %d 行, 无BOM %d 单, 无产品码 %d 单",
        report.orders_scanned, report.orders_matched, report.details_created,
        report.details_skipped, len(report.orders_no_bom), report.orders_no_product,
    )
    return report
