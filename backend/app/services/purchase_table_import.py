# -*- coding: utf-8 -*-
"""配件采购 Excel/CSV 表格导入核心 (网页上传与飞书文件共用)。

列名自动映射; 普通采购沿用旧键，客户明细按客户/规格/加工等完整内容及出现次数防重。
"""
from __future__ import annotations

import csv as _csv
from collections import defaultdict, deque
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
    "材质及说明": "material_name",
    "规格": "spec",
    "尺寸(mm)": "spec", "尺寸（mm）": "spec",
    "姓名": "customer_name", "加工": "processing",
    "数量": "qty",
    "单价": "unit_price",
    "金额": "amount", "总价": "amount", "合计": "amount", "总金额": "amount",
    "价格": "amount",
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


def _detail_remark(rec: dict) -> str:
    # 姓名是采购对应客户，不是供应商，也不能凭姓名关联平台订单。
    return "\n".join(
        label + str(rec.get(field) or "").strip()
        for field, label in (("customer_name", "姓名："), ("processing", "加工："), ("remark", "备注："))
        if str(rec.get(field) or "").strip()
    )


def _detail_key(when, supplier, name, amount, spec, qty, remark):
    return (when, supplier, name, amount, (spec or "").strip(), qty, (remark or "").strip())


def import_purchases_table_core(db: Session, raw: bytes, filename: Optional[str]) -> dict:
    """解析表格并写入采购记录 (多 sheet 全读)。

    重复策略 (用户拍板 2026-06-11): 同 (日期+供应商+名称+金额) 已存在 →
    不再跳过, 而是用新行的非空字段更新旧记录 (最新表格优先), 计入 updated。
    """
    from app.api.purchases import _next_purchase_no
    from app.services import import_clean, tabular
    from app.services.bill_import_service import _date as _parse_date

    stored = db.execute(select(PartPurchase)).scalars().all()
    existing: dict[tuple, PartPurchase] = {
        (r.purchase_date, r.supplier, r.material_name, r.total_amount): r
        for r in stored if r.purchase_type != "客户明细表导入"
    }
    # 客户明细采用独立身份口径，不与旧的粗粒度采购键混用。
    # 消耗导入前快照中的同内容记录：同文件内两条完全相同的真实采购也保留，重传时逐条抵扣。
    detail_existing = defaultdict(deque)
    for r in stored:
        if r.purchase_type == "客户明细表导入":
            detail_existing[_detail_key(r.purchase_date, r.supplier, r.material_name,
                                       r.total_amount, r.spec, r.qty, r.remark)].append(r)
    inserted = updated = skipped_invalid = skipped_duplicate = 0
    unmapped_all: set[str] = set()
    for _sheet, text in tabular.to_csv_texts(raw, filename):
        if not text.strip():
            continue
        reader = _csv.DictReader(StringIO(text))
        norm_fields = {_norm_header(h): h for h in (reader.fieldnames or [])}
        detailed = any(h in norm_fields for h in ("材质及说明", "姓名", "加工"))
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
            qty = _dec(rec.get("qty")) or _D("1")
            remark = _detail_remark(rec) if detailed else (rec.get("remark") or None)
            key = (when, supplier, name, amount)
            if detailed:
                dkey = _detail_key(when, supplier, name, amount, rec.get("spec"), qty, remark)
                if detail_existing[dkey]:
                    detail_existing[dkey].popleft()
                    skipped_duplicate += 1
                    continue
            elif key in existing:
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
                remark=remark,
                purchase_type="客户明细表导入" if detailed else "表格导入",
                payment_status="paid",
            )
            db.add(row)
            db.flush()
            if not detailed:
                existing[key] = row
            inserted += 1
    db.commit()
    # 导入后【自动匹配】(用户 2026-06-27): 料号→分类 + 备注订单号→related_order_no(→汇总进 actual_parts)
    # + 匿名付款码采购按人名改挂供应商。失败不让导入整体失败。
    auto: dict = {}
    if inserted or updated:
        try:
            from app.services import accessory_capture_service
            cap = accessory_capture_service.run_capture(db, apply=True)
            auto = {"material_matched": cap["material_match"]["matched"],
                    "order_linked": cap["order_link"]["linked"],
                    "supplier_relabeled": cap["supplier_relabel"]["relabeled"]}
        except Exception:  # noqa: BLE001 - 自动匹配失败不阻断导入
            db.rollback()
            auto = {"error": "自动匹配已跳过(导入仍成功)"}
    unmapped = sorted(unmapped_all)
    auto_msg = (f"; 自动匹配 料号{auto.get('material_matched', 0)}/订单{auto.get('order_linked', 0)}"
                if auto and "error" not in auto else "")
    return {"inserted": inserted, "updated": updated, "skipped_duplicate": skipped_duplicate,
            "skipped_invalid": skipped_invalid, "unmapped_columns": unmapped, "auto_match": auto,
            "message": (f"导入 {inserted} 条, 更新 {updated} 条采购记录"
                        + (f", 重复 {skipped_duplicate} 条" if skipped_duplicate else "") + auto_msg
                        + (f", 未识别列: {','.join(unmapped)}" if unmapped else ""))}
