"""微信/聚合 结算账单(billDetail)导入: 解析 xlsx → 按 支付流水号 upsert OrderSettlement。

billDetail 列: 入账时间 / 支付流水号 / 淘宝订单编号 / 入账类型 / 收入金额 / 支出金额 / 业务描述 / 备注
(这些导出文件的 dimension 标记不规范, 故全量加载 + 强制按列/行扫描, 不依赖 max_row/max_col。)
"""
from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.settlement import OrderSettlement


def _num(x) -> Decimal:
    if x is None or str(x).strip() == "":
        return Decimal("0")
    try:
        return Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _dt(x) -> Optional[datetime]:
    if not x:
        return None
    if isinstance(x, datetime):
        return x
    s = str(x).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def import_bill(db: Session, content: bytes, source: str = "wechat") -> dict:
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb.worksheets[0]

    # 找表头行 (含「支付流水号」)
    header_row = None
    col: dict[str, int] = {}
    for ri, row in enumerate(ws.iter_rows(min_row=1, max_row=15, min_col=1, max_col=15, values_only=True), start=1):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if any("支付流水号" in c or "流水号" in c for c in cells):
            header_row = ri
            for ci, c in enumerate(cells):
                if c:
                    col[c] = ci
            break
    if header_row is None:
        return {"error": "未找到表头(需含『支付流水号』列)", "inserted": 0, "updated": 0}

    def idx(*names) -> Optional[int]:
        for n in names:
            for k, v in col.items():
                if n in k:
                    return v
        return None

    i_time = idx("入账时间", "交易时间")
    i_pay = idx("支付流水号", "流水号")
    i_order = idx("淘宝订单编号", "订单编号", "商家订单号")
    i_type = idx("入账类型", "交易分类")
    i_in = idx("收入金额", "收入")
    i_out = idx("支出金额", "支出")
    i_desc = idx("业务描述", "商品说明")
    i_rmk = idx("备注")

    def g(row, i):
        return row[i] if (i is not None and i < len(row)) else None

    # 去重: 已入库的 pay_no 集合 + 本次 call 内已处理的 (同一支付流水号在多份重叠账单里只留一条)
    existing_pays = {p for (p,) in db.execute(select(OrderSettlement.pay_no)).all()}
    seen: dict[str, OrderSettlement] = {}
    inserted = updated = 0
    for row in ws.iter_rows(min_row=header_row + 1, max_row=50000, min_col=1, max_col=15, values_only=True):
        pay = g(row, i_pay)
        if not pay:
            continue
        pay = str(pay).strip()
        rec = seen.get(pay)
        if rec is None:
            if pay in existing_pays:
                rec = db.execute(
                    select(OrderSettlement).where(OrderSettlement.pay_no == pay)
                ).scalar_one()
                updated += 1
            else:
                rec = OrderSettlement(pay_no=pay, source=source)
                db.add(rec)
                existing_pays.add(pay)
                inserted += 1
            seen[pay] = rec
        rec.source = source
        rec.order_no = str(g(row, i_order)).strip() if g(row, i_order) else None
        rec.settle_time = _dt(g(row, i_time))
        rec.entry_type = str(g(row, i_type)).strip() if g(row, i_type) else None
        rec.income = _num(g(row, i_in))
        rec.expense = _num(g(row, i_out))
        rec.description = str(g(row, i_desc)).strip() if g(row, i_desc) else None
        rec.remark = str(g(row, i_rmk)).strip() if g(row, i_rmk) else None
    db.flush()
    return {"inserted": inserted, "updated": updated, "source": source}


def summary(db: Session) -> dict:
    rows = db.execute(select(OrderSettlement)).scalars().all()
    income = sum((r.income or Decimal("0")) for r in rows)
    expense = sum((r.expense or Decimal("0")) for r in rows)
    orders = len({r.order_no for r in rows if r.order_no})
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r.source] = by_source.get(r.source, 0) + 1
    return {
        "count": len(rows), "orders": orders,
        "income": float(income), "expense": float(expense), "net": float(income - expense),
        "by_source": by_source,
    }
