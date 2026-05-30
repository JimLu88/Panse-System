"""万师傅安装账单 / 物流费账单 CSV 导入。

万师傅列名 (兼容后台导出 + 我们的表头):
    日期/账单日期 / 订单号 / 服务类型 / 金额/扣款金额 / 状态 / 备注
物流列名:
    日期/账单日期 / 承运商/物流公司 / 运单号/快递单号 / 订单号 /
    重量/重量(kg) / 运费/费用/金额 / 备注

落库给 reconciliation_service.run_install_fee / run_logistics_fee 当应付口径。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.finance import LogisticsBill, WanshifuBill

_WANSHIFU_MAP = {
    "日期": "bill_date", "账单日期": "bill_date", "结算日期": "bill_date",
    "订单号": "order_no", "关联订单号": "order_no", "平台订单号": "order_no",
    "服务类型": "service_type", "类型": "service_type",
    "金额": "amount", "扣款金额": "amount", "结算金额": "amount", "费用": "amount",
    "状态": "status", "结算状态": "status",
    "备注": "remark",
}

_LOGISTICS_MAP = {
    "日期": "bill_date", "账单日期": "bill_date",
    "承运商": "carrier", "物流公司": "carrier", "快递公司": "carrier",
    "运单号": "tracking_no", "快递单号": "tracking_no", "物流单号": "tracking_no",
    "订单号": "order_no", "关联订单号": "order_no",
    "重量": "weight_kg", "重量(kg)": "weight_kg", "重量（kg）": "weight_kg",
    "运费": "freight_amount", "费用": "freight_amount", "金额": "freight_amount",
    "备注": "remark",
}


@dataclass
class BillImportReport:
    inserted: int = 0
    skipped_invalid: int = 0
    errors: list[str] = field(default_factory=list)


def _decimal(v: Any) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _date(v: Any) -> Optional[date]:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _rows(text: str, colmap: dict) -> list[dict]:
    reader = csv.DictReader(StringIO(text))
    out = []
    for raw in reader:
        rec: dict[str, Any] = {}
        for k, v in raw.items():
            field_name = colmap.get((k or "").strip())
            if field_name:
                rec[field_name] = v
        out.append(rec)
    return out


def import_wanshifu_csv(db: Session, text: str, *, import_job_id: Optional[int] = None) -> BillImportReport:
    """导入万师傅安装账单。金额缺失 / 无法解析的行跳过。"""
    rep = BillImportReport()
    for i, rec in enumerate(_rows(text, _WANSHIFU_MAP), start=2):
        amount = _decimal(rec.get("amount"))
        if amount is None:
            rep.skipped_invalid += 1
            continue
        db.add(WanshifuBill(
            bill_date=_date(rec.get("bill_date")),
            order_no=(rec.get("order_no") or None),
            service_type=(rec.get("service_type") or None),
            amount=amount,
            status=(rec.get("status") or None),
            remark=(rec.get("remark") or None),
            import_job_id=import_job_id,
        ))
        rep.inserted += 1
    db.flush()
    return rep


def import_logistics_csv(db: Session, text: str, *, import_job_id: Optional[int] = None) -> BillImportReport:
    """导入物流费月结账单。运费缺失 / 无法解析的行跳过。"""
    rep = BillImportReport()
    for rec in _rows(text, _LOGISTICS_MAP):
        freight = _decimal(rec.get("freight_amount"))
        if freight is None:
            rep.skipped_invalid += 1
            continue
        db.add(LogisticsBill(
            bill_date=_date(rec.get("bill_date")),
            carrier=(rec.get("carrier") or None),
            tracking_no=(rec.get("tracking_no") or None),
            order_no=(rec.get("order_no") or None),
            weight_kg=_decimal(rec.get("weight_kg")),
            freight_amount=freight,
            remark=(rec.get("remark") or None),
            import_job_id=import_job_id,
        ))
        rep.inserted += 1
    db.flush()
    return rep
