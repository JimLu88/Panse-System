"""截图 OCR 解析结果 → 直接入库 (供飞书机器人 采购单/工厂对账 一键 parse→入库)。

与 api/screenshots.py 的网页 commit 区别:
  - 网页: parse → 前端预览/人工编辑 → commit(带冲突确认 UI);
  - 这里: 飞书发图 → parse → 直接入库(无人工编辑), 故用更保守的"仅插新、按主键跳过重复"。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import FactoryReconciliation
from app.models.order import PartPurchase


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


def commit_purchase_parsed(db: Session, parsed: dict) -> dict:
    """parse_purchase_invoice 的结果(含 purchase.lines)→ PartPurchase 多行。按 purchase_no 跳过重复。"""
    p = (parsed or {}).get("purchase") or {}
    lines = p.get("lines") or []
    base_no = (p.get("purchase_no") or "").strip() or f"FSP{int(datetime.now().timestamp())}"
    supplier = p.get("supplier_name") or p.get("supplier")
    pdate = _date(p.get("purchase_date"))
    inserted = skipped = 0
    for idx, line in enumerate(lines, start=1):
        name = (line.get("material_name") or line.get("name") or "").strip()
        if not name:
            continue
        pp_no = f"{base_no}_{idx}" if len(lines) > 1 else base_no
        if db.execute(select(PartPurchase.id).where(PartPurchase.purchase_no == pp_no)).first():
            skipped += 1
            continue
        qty = _dec(line.get("qty")) or Decimal("1")
        unit_price = _dec(line.get("unit_price"))
        amount = _dec(line.get("amount"))
        if amount is None and unit_price is not None:
            amount = (unit_price * qty).quantize(Decimal("0.01"))
        db.add(PartPurchase(
            purchase_no=pp_no, supplier=supplier, purchase_date=pdate,
            material_code=line.get("material_code"), material_name=name,
            spec=line.get("spec"), qty=qty, unit_price=unit_price, amount=amount,
            tracking_no=p.get("tracking_no"),
            freight=_dec(p.get("freight")), total_amount=_dec(p.get("total_amount")),
        ))
        inserted += 1
    db.flush()
    return {"inserted": inserted, "skipped": skipped, "supplier": supplier,
            "warnings": p.get("warnings") or []}


def commit_factory_recon_parsed(db: Session, parsed: dict) -> dict:
    """parse_factory_reconciliation 的结果(rows)→ FactoryReconciliation 多行 (diff=账单-已付)。

    去重: 同 (工厂, 账期起, 账期止, 账单额, 已付额) 已存在则跳过, 防重复发图造成对账金额翻倍。
    """
    rows = (parsed or {}).get("rows") or []
    inserted = skipped = 0
    for row in rows:
        fname = (row.get("factory_name") or "").strip()
        if not fname:
            continue
        ps = _date(row.get("period_start"))
        pe = _date(row.get("period_end"))
        bill = _dec(row.get("bill_amount"))
        paid = _dec(row.get("paid_amount"))
        exists = db.execute(
            select(FactoryReconciliation.id).where(
                FactoryReconciliation.factory_name == fname,
                FactoryReconciliation.period_start == ps,
                FactoryReconciliation.period_end == pe,
                FactoryReconciliation.bill_amount == bill,
                FactoryReconciliation.paid_amount == paid,
            )
        ).first()
        if exists:
            skipped += 1
            continue
        diff = (bill - paid) if (bill is not None and paid is not None) else Decimal("0")
        db.add(FactoryReconciliation(
            factory_name=fname, period_start=ps, period_end=pe,
            order_amount=_dec(row.get("order_amount")),
            bill_amount=bill, paid_amount=paid, diff_amount=diff,
            alipay_flow_no=row.get("alipay_flow_no"), remark=row.get("remark"),
            status="open",
        ))
        inserted += 1
    db.flush()
    return {"inserted": inserted, "skipped": skipped, "warnings": parsed.get("ocr_warnings") or []}
