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

from app.models.order import Order
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
    # 淘宝导出有两个「商家编码」列: 第1个=14位产品编码, 第2个(SKU级)=16位SKU编码。
    # 上面的 index() 只取到第1个, 这里把第2个单独记到 merchant_code_sku。
    mc_positions = [
        i for i, h in enumerate(norm_header)
        if h in ("商家编码", "商家外部编码", "外部编码")
    ]
    if mc_positions:
        out["merchant_code"] = mc_positions[0]
        if len(mc_positions) >= 2:
            out["merchant_code_sku"] = mc_positions[-1]
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
    # read_only=False: 淘宝商品导出文件的 worksheet dimensions 缓存缺失, read_only 模式会误读成
    # 1列0行空表 → 找不到表头。非 read_only 会重算真实维度(实测 18列/几百行)。文件不大, 不需省内存。
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=False, data_only=True)
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
            # 第2个「商家编码」= 16位SKU编码 (SKU级); 仅供匹配, import 时 pop 掉再建模型
            "sku_code_raw": _norm(cell("merchant_code_sku")) or None,
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


def import_listings(db: Session, file_bytes: bytes, shop: Optional[str] = None) -> dict:
    """解析 + upsert + 自动匹配. 返回统计. shop=店铺(畔色店/孚格店), 用于分店统计."""
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
        # 第2个商家编码=16位SKU编码 优先匹配; 回退到第1个(14位产品编码)
        sku_raw = rec.pop("sku_code_raw", None)
        mc = rec.get("merchant_code")
        sku_code = product_code = None
        if sku_raw:
            sku_code = sku_raw
            product_code = sku_index.get(sku_raw) or (mc if mc in product_codes else None)
        elif mc:
            if mc in sku_index:
                sku_code, product_code = mc, sku_index[mc]
            elif mc in product_codes:
                product_code = mc
        rec["sku_code"] = sku_code
        rec["product_code"] = product_code
        rec["matched"] = bool(product_code) or (bool(sku_code) and sku_code in sku_index)
        if rec["matched"]:
            matched += 1
        if shop:  # 仅在指定店铺时写入, 避免重导(不带店铺)清空已有 shop
            rec["shop"] = shop

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


# ── 订单归属解析 (Task 6): 用对应表把订单的 skuId/商家编码 反查到 SKU编码/产品编码/店铺 ──
def build_resolver(db: Session) -> dict:
    """预载对应表, 返回多键索引。

    三个键, 优先级 skuId > 16位SKU编码 > 14位产品编码:
      by_sku_id[taobao_sku_id]   -> {sku_code, product_code, shop}
      by_sku_code[sku_code]      -> 同上 (16位商家编码精确)
      by_product[product_code]   -> 同上 (产品级兜底)
    值里 shop 来自对应表导入时记录的店铺, 供订单分店归属。
    """
    by_sku_id: dict[str, dict] = {}
    by_sku_code: dict[str, dict] = {}
    by_product: dict[str, dict] = {}
    rows = db.query(
        TaobaoListing.taobao_sku_id,
        TaobaoListing.sku_code,
        TaobaoListing.product_code,
        TaobaoListing.shop,
    ).all()
    for r in rows:
        info = {"sku_code": r.sku_code, "product_code": r.product_code, "shop": r.shop}
        if r.taobao_sku_id:
            by_sku_id.setdefault(r.taobao_sku_id, info)
        if r.sku_code:
            by_sku_code.setdefault(r.sku_code, info)
        if r.product_code:
            by_product.setdefault(r.product_code, info)
    return {"by_sku_id": by_sku_id, "by_sku_code": by_sku_code, "by_product": by_product}


def resolve_line(
    resolver: dict,
    *,
    sku_id: Optional[str] = None,
    merchant_code: Optional[str] = None,
) -> Optional[dict]:
    """按 skuId(精确) → 16位商家编码 → 14位产品编码 顺序反查; 命中返回 dict, 否则 None。"""
    if sku_id and sku_id in resolver["by_sku_id"]:
        return resolver["by_sku_id"][sku_id]
    if merchant_code:
        if merchant_code in resolver["by_sku_code"]:
            return resolver["by_sku_code"][merchant_code]
        if merchant_code in resolver["by_product"]:
            return resolver["by_product"][merchant_code]
    return None


def backfill_orders(db: Session, *, only_missing_shop: bool = True) -> dict:
    """一键回填 (Task 9): 用当前对应表给已导入订单补 店铺/产品编码/SKU编码。

    针对「先导了订单、后补全对应表」的历史订单 —— 按订单现有 sku_code(商家编码)
    反查对应表, 只填空字段(不覆盖已有值)。only_missing_shop=True 时仅扫 shop 为空的订单。
    返回 {scanned, updated}。
    """
    resolver = build_resolver(db)
    q = db.query(Order)
    if only_missing_shop:
        q = q.filter(Order.shop.is_(None))
    scanned = updated = 0
    for o in q.all():
        scanned += 1
        hit = resolve_line(resolver, sku_id=None, merchant_code=o.sku_code)
        if not hit:
            continue
        changed = False
        if not o.shop and hit.get("shop"):
            o.shop = hit["shop"]; changed = True
        if not o.product_code and hit.get("product_code"):
            o.product_code = hit["product_code"]; changed = True
        if not o.sku_code and hit.get("sku_code"):
            o.sku_code = hit["sku_code"]; changed = True
        if changed:
            updated += 1
    db.commit()
    return {"scanned": scanned, "updated": updated}
