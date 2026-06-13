# -*- coding: utf-8 -*-
"""配件采购 Excel/CSV 表格导入核心 (网页上传与飞书文件共用)。

列名自动映射; 同 (日期, 供应商, 名称, 金额) 防重。
"""
from __future__ import annotations

import csv as _csv
from decimal import Decimal as _D, InvalidOperation
from io import StringIO
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import PartPurchase

TABLE_MAP = {
    "供应商": "supplier", "店铺": "supplier", "对手方": "supplier",
    "购买日期": "purchase_date", "日期": "purchase_date", "采购日期": "purchase_date",
    "配件名称": "material_name", "名称": "material_name", "商品名称": "material_name", "品名": "material_name",
    "规格": "spec",
    "数量": "qty",
    "单价": "unit_price",
    "金额": "amount", "总价": "amount", "合计": "amount", "总金额": "amount",
    "快递单号": "tracking_no", "运单号": "tracking_no", "物流单号": "tracking_no",
    "备注": "remark",
}


def _dec(v):
    if v is None or str(v).strip() == "":
        return None
    try:
        return _D(str(v).replace(",", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _norm_header(h: Optional[str]) -> str:
    """列名归一 (用户拍板): 去半角/全角空格、去 BOM — "配件 名称"/"配件　名称" 都能认。"""
    return (h or "").replace(" ", "").replace("　", "").replace("﻿", "").strip()


def import_purchases_table_core(db: Session, raw: bytes, filename: Optional[str]) -> dict:
    """解析表格并写入采购记录 (多 sheet 全读)。

    重复策略 (用户拍板 2026-06-11): 同 (日期+供应商+名称+金额) 已存在 →
    不再跳过, 而是用新行的非空字段更新旧记录 (最新表格优先), 计入 updated。
    """
    from app.api.purchases import _next_purchase_no
    from app.services import import_clean, tabular
    from app.services.bill_import_service import _date as _parse_date

    existing: dict[tuple, PartPurchase] = {
        (r.purchase_date, r.supplier, r.material_name, r.total_amount): r
        for r in db.execute(select(PartPurchase)).scalars().all()
    }
    inserted = updated = skipped_invalid = 0
    unmapped_all: set[str] = set()
    for _sheet, text in tabular.to_csv_texts(raw, filename):
        if not text.strip():
            continue
        reader = _csv.DictReader(StringIO(text))
        norm_fields = {_norm_header(h): h for h in (reader.fieldnames or [])}
        unmapped_all.update(h for h in norm_fields if h and h not in TABLE_MAP)
        for raw_row in reader:
            rec = {}
            for k, v in raw_row.items():
                field = TABLE_MAP.get(_norm_header(k))
                if field:
                    rec[field] = v
            name = (rec.get("material_name") or "").strip()
            amount = _dec(rec.get("amount")) or _dec(rec.get("unit_price"))
            if not name or amount is None:
                skipped_invalid += 1
                continue
            when = _parse_date(rec.get("purchase_date"))
            supplier = (rec.get("supplier") or "").strip() or None
            key = (when, supplier, name, amount)
            if key in existing:
                # 用新行的非空字段补/覆盖旧记录 (最新数据优先)
                old = existing[key]
                changed = False
                for f, v in (("spec", rec.get("spec")),
                             ("qty", _dec(rec.get("qty"))),
                             ("unit_price", _dec(rec.get("unit_price"))),
                             ("tracking_no", import_clean.clean_no(rec.get("tracking_no")))):
                    if v not in (None, "") and getattr(old, f) != v:
                        setattr(old, f, v)
                        changed = True
                if changed:
                    updated += 1
                continue
            qty = _dec(rec.get("qty")) or _D("1")
            row = PartPurchase(
                purchase_no=_next_purchase_no(db),
                supplier=supplier,
                purchase_date=when,
                material_name=name,
                spec=(rec.get("spec") or None),
                qty=qty,
                unit_price=_dec(rec.get("unit_price")),
                amount=amount,
                total_amount=amount,
                tracking_no=import_clean.clean_no(rec.get("tracking_no")),
                purchase_type="表格导入",
                payment_status="paid",
            )
            db.add(row)
            db.flush()
            existing[key] = row
            inserted += 1
    db.commit()
    unmapped = sorted(unmapped_all)
    return {"inserted": inserted, "updated": updated, "skipped_duplicate": 0,
            "skipped_invalid": skipped_invalid, "unmapped_columns": unmapped,
            "message": (f"导入 {inserted} 条, 更新 {updated} 条采购记录"
                        + (f", 未识别列: {','.join(unmapped)}" if unmapped else ""))}
