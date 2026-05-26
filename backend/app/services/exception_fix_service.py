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


# exception_type -> fn(db, row) -> bool (True = 问题仍存在). 缺省的类型不复检, 直接 resolve.
def _broken_order_cost(db, o) -> bool:
    return o.theoretical_cost is None and o.actual_cost is None


def _broken_order_alipay(db, o) -> bool:
    from app.models.finance import AlipayFlow
    linked = {
        r.related_order_no
        for r in db.query(AlipayFlow.related_order_no).filter(AlipayFlow.related_order_no.isnot(None)).all()
    }
    return o.order_no not in linked


def _broken_order_tracking(db, o) -> bool:
    return o.tracking_no is None


def _broken_refill(db, r) -> bool:
    from app.models.order import Order
    known = {x.order_no for x in db.query(Order.order_no).all()}
    return r.order_no not in known


def _broken_alipay_txn(db, row) -> bool:
    return row.transaction_no in ("", "null", None)


def _broken_factory_recon(db, r) -> bool:
    return r.bill_amount is None or r.paid_amount is None or not r.alipay_flow_no


def _broken_outsourcing(db, r) -> bool:
    return (not r.alipay_flow_no) or (not r.payment_date)


_STILL_BROKEN = {
    "order_missing_cost": _broken_order_cost,
    "order_missing_alipay": _broken_order_alipay,
    "order_missing_tracking": _broken_order_tracking,
    "refill_unmatched": _broken_refill,
    "alipay_missing_txn": _broken_alipay_txn,
    "factory_recon_incomplete": _broken_factory_recon,
    "outsourcing_missing": _broken_outsourcing,
}


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
    db.flush()  # 让复检看到新值

    # 写回后复扫: 若该行仍不满足规则, 不解除, 保留 open (闭环 — 防"假解决")
    checker = _STILL_BROKEN.get(exc.exception_type)
    if checker is not None and checker(db, row):
        db.commit()
        db.refresh(exc)
        _log.info("exception %d: fields written but rule still unmet, kept open", exception_id)
        return exc

    exc.status = "resolved"
    exc.resolved_at = __import__("datetime").datetime.utcnow()
    exc.resolved_by = "inline_fix"

    db.commit()
    db.refresh(exc)
    _log.info("fixed exception %d (table=%s pk=%s fields=%s)", exception_id, table, pk, list(fields))
    return exc
