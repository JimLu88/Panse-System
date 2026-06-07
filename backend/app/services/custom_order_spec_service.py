"""定制订单「缺定制需求」扫描 → 单开异常分类 custom_order_missing_spec。

业务背景 (用户): 定制单工厂成本目前只能按基础成本估; 把"没写定制需求(尺寸/规格)"的
定制订单单独挑进异常台账, 用户补上需求后即可用系统定制定价方式精确核算工厂成本。

判定:
  看似定制 = is_custom / SKU或品名含「定制」/ sku_code 带「改」后缀。
  缺需求   = SKU+备注里找不到具体尺寸规格 (长/宽/高/直径 + 数字, 或 数字+cm/mm/米, 或 a×b)。
扫描幂等: 同一订单只建一条 open 异常; 已补需求的订单自动 resolve 旧异常。
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.exception import DataException
from app.models.order import Order
from app.services import exception_service, sku_utils

EXC_TYPE = "custom_order_missing_spec"

# 具体尺寸/规格特征: 「长/宽/高..+数字」 或 「数字+cm/mm/米」 或 「a×b」
_SPEC_RE = re.compile(
    r"(长|宽|高|厚|深|直径|孔距|尺寸)\s*[:：]?\s*\d"
    r"|\d+\s*(cm|厘米|mm|毫米|米|m)\b"
    r"|\d+(\.\d+)?\s*[*×xX]\s*\d",
)


def _looks_custom(o: Order) -> bool:
    # 定制信号取自 SKU(商品属性) / is_custom / 改后缀; 产品名里的"全屋定制"是营销词, 不算。
    if o.is_custom or sku_utils.has_gai_suffix(o.sku_code or ""):
        return True
    sku = o.sku or ""
    return any(k in sku for k in ("定制", "其他尺寸", "其他材质", "尺寸微"))


def _has_spec(o: Order) -> bool:
    return bool(_SPEC_RE.search(f"{o.sku or ''} {o.remark or ''}"))


def scan(db: Session, *, auto_resolve: bool = True) -> dict:
    """扫描真实订单(非取消/非历史/非补单)里"缺定制需求"的定制单, 建/销异常。

    返回 {custom_orders, missing_spec, created, resolved}。
    """
    orders = db.execute(
        select(Order).where(
            Order.status != "cancelled",
            Order.is_historical == False,  # noqa: E712
            Order.is_refill == False,  # noqa: E712
        )
    ).scalars().all()

    existing = {
        e.source_pk: e
        for e in db.execute(
            select(DataException).where(
                DataException.exception_type == EXC_TYPE,
                DataException.status == "open",
            )
        ).scalars().all()
    }

    custom_orders = missing = created = resolved = 0
    for o in orders:
        if not _looks_custom(o):
            continue
        custom_orders += 1
        if _has_spec(o):
            # 已补需求 → 解决旧异常
            if auto_resolve and o.order_no in existing:
                existing[o.order_no].status = "resolved"
                resolved += 1
            continue
        missing += 1
        if o.order_no in existing:
            continue  # 已挂异常, 不重复建
        prod = o.product_name or o.product_code or "?"
        exception_service.record(
            db,
            source_table="orders",
            source_pk=o.order_no,
            exception_type=EXC_TYPE,
            severity="warning",
            description=(
                f"定制订单 {o.order_no}（{prod} / {o.sku or '无SKU'}）缺定制需求(尺寸/规格)，"
                f"工厂成本暂按基础成本估算。补充定制需求后可用系统定制定价精确核算。"
            ),
            suggestion_action="fill_custom_spec",
            context={
                "order_no": o.order_no,
                "product_code": o.product_code,
                "sku": o.sku,
                "sku_code": o.sku_code,
                "paid_amount": float(o.paid_amount) if o.paid_amount is not None else None,
                "status": o.status,
            },
        )
        created += 1
    db.flush()
    return {
        "custom_orders": custom_orders,
        "missing_spec": missing,
        "created": created,
        "resolved": resolved,
    }
