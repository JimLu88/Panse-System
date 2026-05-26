"""订单 CSV 导入 (plan §10 Phase 3：淘宝 CSV)。

最小实现：列名 → 字段映射；订单编号重复跳过；新订单默认 pending_payment。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order

# 列名别名表：CSV 头 (规范化后) → Order 字段
COLUMN_ALIASES: dict[str, str] = {
    "平台": "platform",
    "订单编号": "order_no",
    "订单号": "order_no",
    "是否补单": "is_refill",
    "下单日期": "order_date",
    "下单时间": "order_date",
    "客户姓名": "customer_name",
    "客户": "customer_name",
    "联系电话": "customer_phone",
    "手机": "customer_phone",
    "收货地址": "customer_address",
    "地址": "customer_address",
    "发货日期": "ship_date",
    "产品编码": "product_code",
    "产品名称": "product_name",
    "sku": "sku",
    "是否定制": "is_custom",
    "数量": "qty",
    "物流公司": "carrier",
    "物流单号": "tracking_no",
    "实付金额": "paid_amount",
    "应付金额": "paid_amount",
}


@dataclass
class ImportReport:
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    errors: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def _to_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_bool(v: Any) -> bool:
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"是", "yes", "y", "true", "1", "✓"}


def _to_int(v: Any, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _to_decimal(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def import_orders_from_csv(db: Session, csv_text: str) -> ImportReport:
    report = ImportReport()
    reader = csv.DictReader(StringIO(csv_text))

    # 标准化表头
    field_map: dict[str, str] = {}
    for raw in reader.fieldnames or []:
        norm = (raw or "").strip().lower()
        for alias_key, field_name in COLUMN_ALIASES.items():
            if alias_key.lower() == norm:
                field_map[raw] = field_name
                break

    if "order_no" not in field_map.values():
        report.errors.append("CSV 缺少『订单编号』列，无法导入")
        return report

    seen_in_batch: set[str] = set()
    for line_no, row in enumerate(reader, start=2):
        payload: dict[str, Any] = {}
        for raw_col, field_name in field_map.items():
            payload[field_name] = row.get(raw_col)

        order_no = (payload.get("order_no") or "").strip() if payload.get("order_no") else ""
        if not order_no:
            report.skipped_invalid += 1
            continue

        # 重复跳过（DB 里已有 or 本次 CSV 已经处理过）
        if order_no in seen_in_batch or db.execute(
            select(Order.id).where(Order.order_no == order_no)
        ).first():
            report.skipped_duplicate += 1
            seen_in_batch.add(order_no)
            continue
        seen_in_batch.add(order_no)

        order = Order(
            platform=(payload.get("platform") or "淘宝").strip(),
            order_no=order_no,
            is_refill=_to_bool(payload.get("is_refill")),
            order_date=_to_date(payload.get("order_date")),
            ship_date=_to_date(payload.get("ship_date")),
            customer_name=payload.get("customer_name"),
            customer_phone=payload.get("customer_phone"),
            customer_address=payload.get("customer_address"),
            product_code=payload.get("product_code"),
            product_name=payload.get("product_name"),
            sku=payload.get("sku"),
            is_custom=_to_bool(payload.get("is_custom")),
            qty=_to_int(payload.get("qty"), default=1),
            carrier=payload.get("carrier"),
            tracking_no=payload.get("tracking_no"),
            paid_amount=_to_decimal(payload.get("paid_amount")),
            status="pending_payment",
        )
        db.add(order)
        report.inserted += 1

    db.commit()
    return report
