# -*- coding: utf-8 -*-
"""打包费手写账单 OCR 预览结果 → 入库 + 配单 + 月度核算 (用户 2026-06-21)。

流程: api/screenshots.py /packing-bill/parse (vision OCR) → 前端人工复核 → /commit 调本服务。
- 配单: matched_order_no = OCR单号优先, 否则按客户名在订单库唯一匹配 (排除关闭单, 铁律)。
- 「改客户/不计入/作废」行 excluded=True, 不计入应付总额 (用户 C②: 自动剔除错误数据)。
- month_summary: 当月应付打包费 = Σ packing_fee(excluded=False); 与本子「合计」互核。
手写姓名 OCR 准确率有限 → 入库前必须人工在预览页复核, 本服务只做确认后入库。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import PackingBill
from app.models.order import Order
from app.services.sales_analytics import SETTLED_SALE_STATUSES

# 本子上常见的「不计入」批注 → excluded=True (用户 C②)
EXCLUDE_KEYWORDS = ("改客户", "不计入", "不算", "作废", "退了", "退货", "非本店",
                    "不是我们", "划掉", "取消", "删除")

MATCH_CN = {
    "order_no": "单号匹配", "name_unique": "客户名唯一", "multi": "多候选待人工",
    "none": "未能自动匹配", "manual": "人工指定",
}


def _dec(v: Any) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("¥", "").replace("元", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _date(v: Any) -> Optional[date]:
    if not v:
        return None
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _is_excluded(row: dict) -> tuple[bool, Optional[str]]:
    if row.get("excluded") is True:
        return True, (str(row.get("exclude_reason") or "本子标注不计入").strip() or "本子标注不计入")
    text = f"{row.get('exclude_reason') or ''} {row.get('note') or ''}"
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return True, kw
    return False, None


def _order_indices(db: Session):
    """已发货成交单 (排除关闭/未付款单) 的 order_no 集合 + 客户名→订单列表。"""
    rows = db.execute(
        select(Order.order_no, Order.customer_name)
        .where(Order.status.in_(SETTLED_SALE_STATUSES))
    ).all()
    valid_nos = set()
    by_name: dict[str, list[str]] = {}
    for o in rows:
        if o.order_no:
            valid_nos.add(o.order_no)
        if o.customer_name:
            by_name.setdefault(o.customer_name.strip(), []).append(o.order_no)
    return valid_nos, by_name


def _match_row(bill: PackingBill, valid_nos: set, by_name: dict) -> None:
    """给单行打包费账单配订单, 写 matched_order_no/match_method/match_note。"""
    if bill.order_no and bill.order_no in valid_nos:
        bill.matched_order_no = bill.order_no
        bill.match_method = "order_no"
        bill.match_note = None
        return
    name = (bill.customer_name or "").strip()
    cands = by_name.get(name, [])
    if name and len(cands) == 1:
        bill.matched_order_no = cands[0]
        bill.match_method = "name_unique"
        bill.match_note = None
    elif name and len(cands) > 1:
        bill.match_method = "multi"
        bill.match_note = f"客户「{name}」有多笔订单: {'/'.join(sorted(cands)[:5])}"
    else:
        bill.match_method = "none"
        bill.match_note = (f"订单库无客户「{name}」(手写名可能认错/主订单未导入)"
                           if name else "无客户名, 无法配单")


def commit_packing_parsed(db: Session, rows: list[dict], *, bill_month: Optional[str] = None,
                          source_image: Optional[str] = None,
                          import_job_id: Optional[int] = None) -> dict:
    """确认后的打包费账单行 → 入库 PackingBill, 逐行配单 + 自动剔除「不计入」行。

    去重: 同 (bill_month, customer_name, packing_fee, row_date) 已存在则跳过 (防重复发图翻倍)。
    """
    valid_nos, by_name = _order_indices(db)
    existing = {
        (m, c, f, d) for m, c, f, d in db.execute(
            select(PackingBill.bill_month, PackingBill.customer_name,
                   PackingBill.packing_fee, PackingBill.row_date)
        ).all()
    }
    seen: set = set()
    inserted = skipped = matched = excluded = 0
    for row in rows or []:
        fee = _dec(row.get("packing_fee"))
        name = (row.get("customer_name") or "").strip() or None
        if fee is None and not name:
            continue  # 完全空行
        rdate = _date(row.get("row_date"))
        key = (bill_month, name, fee, rdate)
        if key in existing or key in seen:
            skipped += 1
            continue
        seen.add(key)
        is_excl, reason = _is_excluded(row)
        conf = _dec(row.get("confidence"))
        bill = PackingBill(
            bill_month=bill_month, row_date=rdate, customer_name=name,
            order_no=(str(row.get("order_no")).strip() if row.get("order_no") else None),
            product=(str(row.get("product")).strip() if row.get("product") else None),
            packing_fee=fee, excluded=is_excl, exclude_reason=reason,
            confidence=(conf if conf is not None and 0 <= conf <= 1 else None),
            note=(str(row.get("note")).strip() if row.get("note") else None),
            source_image=source_image, import_job_id=import_job_id,
        )
        _match_row(bill, valid_nos, by_name)
        db.add(bill)
        inserted += 1
        if bill.matched_order_no:
            matched += 1
        if is_excl:
            excluded += 1
    db.flush()
    return {"inserted": inserted, "skipped": skipped, "matched": matched,
            "excluded": excluded, **month_summary(db, bill_month)}


def month_summary(db: Session, bill_month: Optional[str]) -> dict:
    """当月打包费核算: 应付 = Σ未剔除; 剔除额/未配单数 单列, 供与本子合计互核。"""
    stmt = select(PackingBill)
    if bill_month:
        stmt = stmt.where(PackingBill.bill_month == bill_month)
    bills = db.execute(stmt).scalars().all()
    payable = sum((b.packing_fee or Decimal("0")) for b in bills if not b.excluded)
    excl_amt = sum((b.packing_fee or Decimal("0")) for b in bills if b.excluded)
    return {
        "rows_total": len(bills),
        "payable_total": float(payable),                       # 应付打包费 (已剔除不计入)
        "excluded_total": float(excl_amt),                     # 被剔除金额
        "excluded_rows": sum(1 for b in bills if b.excluded),
        "unmatched_rows": sum(1 for b in bills if not b.matched_order_no and not b.excluded),
    }
