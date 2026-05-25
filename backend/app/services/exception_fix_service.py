"""异常内联补填服务 (Phase 13).

POST /api/exceptions/{id}/fix → fields dict
→ 按 source_table 白名单写回源表对应行 → 解除异常.

白名单设计: 只允许补填"缺失/可修正"字段, 禁止越权修改主键 / 业务关键字段.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.models.exception import DataException

_log = logging.getLogger("panse.exception_fix")

# source_table -> (Model, {可编辑字段集合})
_REGISTRY: dict[str, tuple[Any, set[str]]] = {}


def _register():
    from app.models.order import Order
    from app.models.finance import AlipayFlow, RefillRecord, FactoryReconciliation
    from app.models.marketing import OutsourcingExpense, AfterSales
    from app.models.pricing import PricingSku
    from app.models.inventory import PartInventory, ProductInventory

    _REGISTRY.update({
        "orders": (Order, {
            "theoretical_cost", "actual_cost", "actual_freight",
            "tracking_no", "carrier", "remark",
            "product_code", "product_name", "sku", "sku_code",
        }),
        "alipay_flows": (AlipayFlow, {
            "transaction_no", "related_order_no", "reconciliation_type", "remark",
        }),
        "refill_records": (RefillRecord, {
            "order_no", "product_code", "product_name", "sku",
            "order_amount", "refill_cost", "refill_freight",
        }),
        "factory_reconciliations": (FactoryReconciliation, {
            "bill_amount", "paid_amount", "alipay_flow_no", "diff_reason", "remark",
        }),
        "outsourcing_expenses": (OutsourcingExpense, {
            "alipay_flow_no", "payment_date", "project", "remark",
        }),
        "after_sales": (AfterSales, {
            "reason", "compensation_fee", "direct_compensation", "remark",
        }),
        "pricing_sku": (PricingSku, {
            "list_price", "daily_price", "small_promo", "mid_promo", "big_promo",
            "accounting_cost", "physical_cost", "image_url",
        }),
        "part_inventory": (PartInventory, {
            "physical_qty", "remark",
        }),
        "product_inventory": (ProductInventory, {
            "physical_qty", "remark",
        }),
    })


_register()


def fix_exception(db: Session, exception_id: int, fields: dict[str, Any]) -> DataException:
    exc = db.get(DataException, exception_id)
    if exc is None:
        raise ValueError(f"Exception {exception_id} not found")
    if exc.status == "resolved":
        raise ValueError("Exception already resolved")

    table = exc.source_table
    pk = exc.source_pk

    if table not in _REGISTRY:
        raise ValueError(f"source_table '{table}' does not support inline fix")

    Model, allowed = _REGISTRY[table]

    # 拒绝越界字段
    forbidden = set(fields.keys()) - allowed
    if forbidden:
        raise ValueError(f"Fields not allowed for fix: {forbidden}")

    # 特殊处理 "empty" pk (如 aftersales_empty 没有实际行)
    if pk == "empty":
        exc.status = "resolved"
        exc.resolved_at = __import__("datetime").datetime.utcnow()
        exc.resolved_by = "inline_fix"
        db.commit()
        return exc

    try:
        row_id = int(pk)
    except (ValueError, TypeError) as e:
        raise ValueError(f"source_pk '{pk}' is not an integer row id") from e

    row = db.get(Model, row_id)
    if row is None:
        raise ValueError(f"Row {table}/{pk} not found")

    for k, v in fields.items():
        setattr(row, k, v)

    exc.status = "resolved"
    exc.resolved_at = __import__("datetime").datetime.utcnow()
    exc.resolved_by = "inline_fix"

    db.commit()
    db.refresh(exc)
    _log.info("fixed exception %d (table=%s pk=%s fields=%s)", exception_id, table, pk, list(fields))
    return exc
