"""支付宝流水 CSV 导入。

约定列名（支付宝标准导出 + 我们的 Excel 表 9a 都用这些）：
    交易时间 / 交易流水号 / 交易类型 / 交易对象 / 交易账户 / 收支金额 /
    关联订单号 / 余额 / 核销状态 / 核销类型 / 备注
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow

COLUMN_MAP = {
    "交易时间": "transaction_time",
    "交易流水号": "transaction_no",
    "流水号": "transaction_no",
    "交易类型": "transaction_type",
    "交易对象": "counterparty",
    "对方账户名称": "counterparty",
    "交易账户": "counterparty_account",
    "对方账号": "counterparty_account",
    "收支金额": "amount",
    "金额": "amount",
    "关联订单号": "related_order_no",
    "商户订单号": "related_order_no",
    "余额": "balance",
    "核销状态": "reconciliation_status",
    "核销类型": "reconciliation_type",
    "备注": "remark",
}


@dataclass
class AlipayImportReport:
    inserted: int = 0
    skipped_duplicate: int = 0
    skipped_invalid: int = 0
    errors: list[str] = field(default_factory=list)


def _decimal(v: Any) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _datetime(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def import_alipay_csv(db: Session, csv_text: str, *, account: str) -> AlipayImportReport:
    report = AlipayImportReport()
    reader = csv.DictReader(StringIO(csv_text))
    field_map: dict[str, str] = {}
    for raw in reader.fieldnames or []:
        norm = (raw or "").strip()
        if norm in COLUMN_MAP:
            field_map[raw] = COLUMN_MAP[norm]

    if "transaction_no" not in field_map.values():
        report.errors.append("CSV 缺少『交易流水号』列")
        return report
    if "amount" not in field_map.values():
        report.errors.append("CSV 缺少『收支金额』列")
        return report

    seen: set[str] = set()
    for row in reader:
        payload: dict[str, Any] = {}
        for raw, fld in field_map.items():
            payload[fld] = row.get(raw)
        tx_no = (payload.get("transaction_no") or "").strip()
        amount = _decimal(payload.get("amount"))
        if not tx_no or amount is None:
            report.skipped_invalid += 1
            continue
        if tx_no in seen:
            report.skipped_duplicate += 1
            continue
        if db.execute(
            select(AlipayFlow.id).where(
                AlipayFlow.account == account, AlipayFlow.transaction_no == tx_no
            )
        ).first():
            report.skipped_duplicate += 1
            seen.add(tx_no)
            continue
        seen.add(tx_no)
        db.add(AlipayFlow(
            account=account,
            transaction_no=tx_no,
            transaction_time=_datetime(payload.get("transaction_time")),
            transaction_type=payload.get("transaction_type"),
            counterparty=payload.get("counterparty"),
            counterparty_account=payload.get("counterparty_account"),
            amount=amount,
            related_order_no=payload.get("related_order_no"),
            balance=_decimal(payload.get("balance")),
            reconciliation_status=payload.get("reconciliation_status") or "open",
            reconciliation_type=payload.get("reconciliation_type"),
            remark=payload.get("remark"),
        ))
        report.inserted += 1
    db.commit()
    return report
