"""Read-only purchase identity shared by costs, sales and demand projections.

Transaction money belongs to the parent. Without an approved allocation, multi-
item money is a separate explicit bucket, never copied onto the first SKU.
"""
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy import select
from app.models.order import Order, OrderDetail

UNALLOCATED_CODE = "__MULTI_UNALLOCATED__"
UNALLOCATED_NAME = "多商品订单（金额未分摊）"


def _filter_lines(order, imported):
    from app.services.taobao_order_import import _is_service_line_name
    has_children = any(r.sub_order_no and r.sub_order_no != order.order_no for r in imported)
    rows = [r for r in imported if not (has_children and r.sub_order_no == order.order_no
                                       and not r.sku_code and not r.sku_name)
            and not _is_service_line_name(r.product_name)]
    multiple = len(rows) >= 2
    refund_facts = any(r.refund_status or r.refund_amount is not None for r in rows)
    active = []
    for r in rows:
        amount, refund = Decimal(str(r.amount or 0)), Decimal(str(r.refund_amount or 0))
        if r.line_status in {"cancelled", "closed"} or (amount > 0 and refund >= amount * Decimal("0.99")):
            continue
        active.append(r)
    refund = Decimal(str(getattr(order, "refund_amount", None) or 0))
    if refund > 0 and not refund_facts:
        active = [r for r in active if abs(Decimal(str(r.amount or 0)) - refund) >= Decimal("0.5")]
    return active, multiple


def purchase_lines(db, order):
    imported = list(db.scalars(select(OrderDetail).where(
        OrderDetail.order_no == order.order_no, OrderDetail.source == "import")))
    return _filter_lines(order, imported)


def purchase_line_groups(db, orders):
    """Bulk-load once; dashboard calls must not add one SQL query per order."""
    from collections import defaultdict
    groups = defaultdict(list)
    numbers = list({o.order_no for o in orders})
    for offset in range(0, len(numbers), 500):
        for line in db.scalars(select(OrderDetail).where(OrderDetail.source == "import",
                           OrderDetail.order_no.in_(numbers[offset:offset + 500]))):
            groups[line.order_no].append(line)
    return groups


def positive_quantity(value):
    try:
        number = Decimal(str(value))
        return int(number) if number.is_finite() and number > 0 and number == int(number) else None
    except (ValueError, TypeError, ArithmeticError):
        return None


def copy_order(order, **changes):
    values = {column.key: getattr(order, column.key, None) for column in Order.__table__.columns}
    values.update(changes)
    values['_financial_source'] = order
    return SimpleNamespace(**values)


def sales_projections(db, orders):
    """One money row per parent plus exact child quantities; no source mutation.

    Quantity-only rows carry an explicit flag; consumers must not describe their
    zero additive money as a known zero selling price/profit.
    """
    result = []
    groups = purchase_line_groups(db, orders)
    for order in orders:
        imported = groups[order.order_no]
        lines, multiple = _filter_lines(order, imported)
        if not multiple:
            if len(lines) == 1:
                line = lines[0]
                qty = positive_quantity(line.qty)
                result.append(copy_order(order, product_code=line.product_code or order.product_code,
                    product_name=line.product_name or order.product_name,
                    sku_code=line.sku_code or order.sku_code, sku=line.sku_name or order.sku,
                    qty=qty or 0, _quantity_unknown=qty is None, _quantity_only=False))
            elif not imported:
                result.append(order)  # Preserve the existing no-detail legacy policy.
            if lines or not imported:
                continue
        result.append(copy_order(order, product_code=UNALLOCATED_CODE, sku_code=UNALLOCATED_CODE,
            product_name=UNALLOCATED_NAME, sku=UNALLOCATED_NAME, qty=0,
            _quantity_only=False, _money_unallocated=True))
        for line in lines:
            qty = positive_quantity(line.qty)
            result.append(copy_order(order, product_code=line.product_code, product_name=line.product_name,
                sku_code=line.sku_code, sku=line.sku_name, qty=qty or 0,
                paid_amount=Decimal("0"), refund_amount=Decimal("0"),
                _quantity_only=True, _quantity_unknown=qty is None,
                _purchase_sub_order_no=line.sub_order_no))
    return result


def demand_projections(db, order, *, imported=None):
    if imported is None:
        imported = list(db.scalars(select(OrderDetail).where(OrderDetail.source == "import",
                                   OrderDetail.order_no == order.order_no)))
    lines, multiple = _filter_lines(order, imported)
    if not imported:
        return [order]
    result = []
    for line in lines:
        qty = positive_quantity(line.qty)
        result.append(copy_order(order, product_code=line.product_code or (order.product_code if not multiple else None),
            product_name=line.product_name, sku_code=line.sku_code, sku=line.sku_name,
            qty=qty or 0, _quantity_unknown=qty is None,
            _money_pending=multiple, _purchase_sub_order_no=line.sub_order_no,
            _line_status=line.line_status, _is_child_projection=True,
            is_custom=False if multiple else order.is_custom,
            paid_amount=Decimal("0") if multiple else order.paid_amount,
            refund_amount=Decimal("0") if multiple else order.refund_amount))
    return result
