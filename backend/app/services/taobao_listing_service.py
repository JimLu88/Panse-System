"""淘宝商品导出 Excel 导入 (Task 5).

解析淘宝「商品导出」表 (每行一个 SKU), upsert 进 taobao_listings,
并按商家编码自动匹配系统内部 PricingSku.sku_code / product_code。
"""
from __future__ import annotations

import io
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import openpyxl
from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.taobao_listing import TaobaoListing

_log = logging.getLogger("panse.taobao_import")

# 目标字段 -> 可能的列名 (按优先级)
_ALIASES: dict[str, list[str]] = {
    "taobao_item_id": ["商品Id", "商品id", "商品ID", "宝贝ID", "宝贝Id"],
    "taobao_sku_id": ["skuId", "sku_id", "SKUID", "SKU ID", "skuid"],
    "title": ["宝贝标题", "商品标题", "标题"],
    "merchant_code": ["商家编码", "商家外部编码", "外部编码"],
    "sku_spec": ["销售属性", "属性对", "规格"],
    "category_name": ["类目名称", "类目"],
    "list_price": ["一口价"],
    "sku_price": ["价格(元)", "价格（元）", "价格", "sku价格"],
    "stock": ["库存(件)", "库存（件）", "库存", "数量"],
}


def _norm(s: Any) -> str:
    return str(s).strip() if s is not None else ""


def _find_header_row(rows: list[list], max_scan: int = 8) -> int:
    """找含「商品Id」的真正表头行 (0-indexed). 找不到返回 -1.

    淘宝导出第 2 行是合并的分组标题 (商品信息/SKU信息...), 通用嗅探会误判,
    所以这里专门定位含商品ID 列的那一行。
    """
    for i, row in enumerate(rows[:max_scan]):
        cells = [_norm(c) for c in row]
        if any(c in ("商品Id", "商品id", "商品ID") for c in cells):
            return i
    return -1


def _build_col_map(header: list[str]) -> dict[str, int]:
    """目标字段 -> 列下标."""
    out: dict[str, int] = {}
    norm_header = [_norm(h) for h in header]
    for field, aliases in _ALIASES.items():
        for alias in aliases:
            if alias in norm_header:
                out[field] = norm_header.index(alias)
                break
    return out


def _to_decimal(v: Any) -> Optional[Decimal]:
    s = _norm(v)
    if not s:
        return None
    try:
        return Decimal(s.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    s = _norm(v)
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_rows(file_bytes: bytes) -> tuple[list[dict], list[str]]:
    """解析文件 → (记录列表, 警告列表). 不写库."""
    warnings: list[str] = []
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    ws = wb.active
    all_rows = list(ws.iter_rows(values_only=True))
    if not all_rows:
        return [], ["表格为空"]

    hidx = _find_header_row(all_rows)
    if hidx < 0:
        return [], ["未找到含「商品Id」的表头行, 请确认这是淘宝商品导出表"]

    header = list(all_rows[hidx])
    col = _build_col_map(header)
    if "taobao_item_id" not in col:
        return [], ["缺少「商品Id」列"]

    records: list[dict] = []
    for r in all_rows[hidx + 1:]:
        item_id = _norm(r[col["taobao_item_id"]]) if col["taobao_item_id"] < len(r) else ""
        if not item_id:
            continue  # 跳过空行/残留行

        def cell(field: str) -> Any:
            idx = col.get(field)
            if idx is None or idx >= len(r):
                return None
            return r[idx]

        rec = {
            "taobao_item_id": item_id,
            "taobao_sku_id": _norm(cell("taobao_sku_id")) or None,
            "title": _norm(cell("title")) or None,
            "merchant_code": _norm(cell("merchant_code")) or None,
            "sku_spec": _norm(cell("sku_spec")) or None,
            "category_name": _norm(cell("category_name")) or None,
            "list_price": _to_decimal(cell("list_price")),
            "sku_price": _to_decimal(cell("sku_price")),
            "stock": _to_int(cell("stock")),
        }
        records.append(rec)

    if not records:
        warnings.append("未解析到任何商品行")
    return records, warnings


def import_listings(db: Session, file_bytes: bytes) -> dict:
    """解析 + upsert + 自动匹配. 返回统计."""
    records, warnings = parse_rows(file_bytes)
    if not records:
        return {"inserted": 0, "updated": 0, "matched": 0, "total": 0, "warnings": warnings}

    # 预载系统 SKU: sku_code / product_code -> product_code, 用于按商家编码匹配
    sku_index: dict[str, str] = {}      # sku_code -> product_code
    product_codes: set[str] = set()
    for s in db.query(PricingSku.sku_code, PricingSku.product_code).all():
        if s.sku_code:
            sku_index[s.sku_code] = s.product_code
        if s.product_code:
            product_codes.add(s.product_code)

    inserted = updated = matched = 0
    for rec in records:
        # 按商家编码自动匹配系统 SKU
        mc = rec.get("merchant_code")
        sku_code = product_code = None
        if mc:
            if mc in sku_index:
                sku_code, product_code = mc, sku_index[mc]
            elif mc in product_codes:
                product_code = mc
        rec["sku_code"] = sku_code
        rec["product_code"] = product_code
        rec["matched"] = bool(sku_code or product_code)
        if rec["matched"]:
            matched += 1

        existing = (
            db.query(TaobaoListing)
            .filter(
                TaobaoListing.taobao_item_id == rec["taobao_item_id"],
                TaobaoListing.taobao_sku_id == rec["taobao_sku_id"],
            )
            .first()
        )
        if existing:
            for k, v in rec.items():
                setattr(existing, k, v)
            updated += 1
        else:
            db.add(TaobaoListing(**rec))
            inserted += 1

    db.commit()
    _log.info("taobao import: +%d ~%d matched=%d", inserted, updated, matched)
    return {
        "inserted": inserted,
        "updated": updated,
        "matched": matched,
        "total": len(records),
        "warnings": warnings,
    }
