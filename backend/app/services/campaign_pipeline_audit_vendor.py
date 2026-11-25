"""Pinned read-only audit from the handed-off Web-Agent pipeline.
No planner, browser, CLI, credential or dated ERP snapshot is imported.
Source SHA256: 494a727840a0d6a598cf3f25b33bbdddea26f9bdb9840fbc535b5d1705527194
"""
from __future__ import annotations
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any
import openpyxl

def D(v: Any) -> Decimal | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "-":
        return None
    return Decimal(s)

def _detect_template_columns(ws) -> dict[str, int]:
    cols: dict[str, int] = {}
    for c in range(1, ws.max_column + 1):
        h = str(ws.cell(3, c).value or "")
        if "输入报名商品的商品ID" in h or c == 1:
            cols.setdefault("item_id", c)
        if "商品名称" in h and c <= 3:
            cols["item_name"] = c
        if "活动的状态" in h:
            cols["status"] = c
        if "SKUID" in h:
            cols["sku_id"] = c
        if "SKU规格名称" in h or "SKU名称" in h:
            cols["sku_name"] = c
        if "线上库存" in h:
            cols["stock"] = c
        if "最低标价" in h:
            cols["min_tag"] = c
        if "最低普惠券后价" in h:
            cols["min_coupon"] = c
        if "当前活动普惠券后价" in h:
            cols["curr_coupon"] = c
        if "最低折扣要求" in h:
            cols["min_rate"] = c
        if "本次活动报名的商品价格" in h:
            cols["decl_price"] = c
        if "发货时效" in h or "发货时间" in h:
            cols["ship_time"] = c
        if "官方立减报名折扣" in h or "填写官方立减折扣" in h:
            cols["disc_rate"] = c
        if "官方立减减钱金额" in h:
            cols["disc_cut"] = c
        # Listing price (一口价) is usually col 7 in reference templates
        if c == 7 and "参考" in h and "最低标价" not in h:
            cols["listing"] = c
    return cols

def audit_enrolled_against_erp(enrolled_path: Path, erp_map: dict[tuple[str, str], dict[str, Any]], cfg: CampaignConfig) -> dict[str, Any]:
    wb = openpyxl.load_workbook(enrolled_path, data_only=True)
    ws = wb.active
    # Integration-only resource guard; original audit semantics retained below.
    if ws.max_row > 20003 or ws.max_column > 256:
        wb.close()
        raise ValueError('Workbook dimensions exceed audit limits')
    cols = _detect_template_columns(ws)
    tol = Decimal(cfg.tolerance_yuan)

    items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    curr_id = ""
    curr_name = ""
    curr_st = ""
    for r in range(4, ws.max_row + 1):
        iv = ws.cell(r, cols["item_id"]).value
        if iv and str(iv).strip():
            curr_id = str(iv).strip()
            curr_name = str(ws.cell(r, cols.get("item_name", 2)).value or "").strip()
            curr_st = str(ws.cell(r, cols.get("status", 4)).value or "").strip()
        sid = str(ws.cell(r, cols["sku_id"]).value or "").strip()
        sname = str(ws.cell(r, cols.get("sku_name", 6)).value or "").strip()
        items[curr_id].append({
            "item_id": curr_id,
            "item_name": curr_name,
            "status": curr_st,
            "sku_id": sid,
            "sku_name": sname,
            "decl_price": ws.cell(r, cols["decl_price"]).value,
            "curr_coupon": ws.cell(r, cols.get("curr_coupon", 10)).value,
            "min_coupon": ws.cell(r, cols.get("min_coupon", 9)).value,
        })

    item_statuses = dict(Counter(v[0]["status"] for v in items.values()))
    sku_statuses = dict(Counter(s["status"] for v in items.values() for s in v))

    checked = 0
    mismatches: list[dict[str, Any]] = []
    for iid, skus in items.items():
        if skus[0]["status"] != "已发布设定":
            continue
        for s in skus:
            erp = erp_map.get((iid, s["sku_id"]))
            if not erp or erp.get("custom") or not erp.get(cfg.erp_target_mode):
                continue
            cc = D(s["curr_coupon"])
            et = D(erp.get(cfg.erp_target_mode))
            if cc is None or et is None:
                continue
            checked += 1
            diff = cc - et
            if diff > Decimal("0") or diff < -tol:
                mismatches.append({
                    "item_id": iid, "sku_id": s["sku_id"], "sku_name": s["sku_name"],
                    "curr_coupon": str(cc), "erp_target": str(et), "diff": str(diff),
                })

    wb.close()
    return {
        "enrolled_items": len(items),
        "enrolled_skus": sum(len(v) for v in items.values()),
        "item_statuses": item_statuses,
        "sku_statuses": sku_statuses,
        "anomaly_items": [iid for iid, skus in items.items() if skus[0]["status"] != "已发布设定"],
        "erp_checked_skus": checked,
        "erp_mismatches": mismatches,
        "erp_mismatches_count": len(mismatches),
    }
