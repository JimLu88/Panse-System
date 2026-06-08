"""代付台账 CSV 导入 (补单佣金 / 补单快递 / 售后 的实际打款明细)。

列(自动识别): 打款日期 / 打款流水号 / 订单号 / 金额 / 收款方 / 备注。
按 pay_no 去重(批内 + DB); 无 pay_no 的行按 (日期,订单号,金额) 去重。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.prepay_ledger import PREPAY_CATEGORIES, PrepayLedger

_MAP = {
    "打款日期": "pay_date", "日期": "pay_date", "支付日期": "pay_date", "时间": "pay_date",
    "打款流水号": "pay_no", "流水号": "pay_no", "支付宝流水号": "pay_no", "交易号": "pay_no",
    "订单号": "order_no", "关联订单号": "order_no", "平台订单号": "order_no",
    "金额": "amount", "打款金额": "amount", "实付金额": "amount", "费用": "amount", "佣金": "amount",
    "收款方": "payee", "收款人": "payee", "对方": "payee",
    "备注": "remark",
}


@dataclass
class PrepayImportReport:
    inserted: int = 0
    skipped_invalid: int = 0
    skipped_duplicate: int = 0
    unmapped_columns: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _dec(v: Any) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _date(v: Any) -> Optional[date]:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:19]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y年%m月%d日"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def import_prepay_csv(db: Session, text: str, *, category: str) -> PrepayImportReport:
    rep = PrepayImportReport()
    if category not in PREPAY_CATEGORIES:
        rep.errors.append(f"未知台账类型 {category!r}; 允许: {list(PREPAY_CATEGORIES)}")
        return rep
    reader = csv.DictReader(StringIO(text))
    headers = reader.fieldnames or []
    rep.unmapped_columns = [h for h in headers if h and h.strip() and h.strip() not in _MAP]

    existing_pays = {p for (p,) in db.execute(
        select(PrepayLedger.pay_no).where(PrepayLedger.pay_no.isnot(None))
    ).all()}
    existing_keys = {
        (c, d, o, a) for c, d, o, a in db.execute(
            select(PrepayLedger.category, PrepayLedger.pay_date, PrepayLedger.order_no, PrepayLedger.amount)
            .where(PrepayLedger.category == category)
        ).all()
    }
    seen_pays: set = set()
    seen_keys: set = set()
    for raw in reader:
        rec = {}
        for k, v in raw.items():
            fld = _MAP.get((k or "").strip())
            if fld:
                rec[fld] = v
        amount = _dec(rec.get("amount"))
        if amount is None:
            rep.skipped_invalid += 1
            continue
        pay_no = (rec.get("pay_no") or "").strip() or None
        pay_date = _date(rec.get("pay_date"))
        order_no = (rec.get("order_no") or "").strip() or None
        if pay_no:
            if pay_no in existing_pays or pay_no in seen_pays:
                rep.skipped_duplicate += 1
                continue
            seen_pays.add(pay_no)
        else:
            key = (category, pay_date, order_no, amount)
            if key in existing_keys or key in seen_keys:
                rep.skipped_duplicate += 1
                continue
            seen_keys.add(key)
        db.add(PrepayLedger(
            category=category, pay_no=pay_no, order_no=order_no, pay_date=pay_date,
            amount=amount, payee=(rec.get("payee") or None), source="import",
            remark=(rec.get("remark") or None),
        ))
        rep.inserted += 1
    db.flush()
    return rep


def summary(db: Session) -> dict:
    rows = db.execute(
        select(PrepayLedger.category, func.count(PrepayLedger.id), func.coalesce(func.sum(PrepayLedger.amount), 0))
        .group_by(PrepayLedger.category)
    ).all()
    by_cat = {c: {"count": int(n), "amount": float(amt)} for c, n, amt in rows}
    total = db.execute(select(func.count(PrepayLedger.id))).scalar_one()
    return {"total": int(total or 0), "by_category": by_cat}
