"""价格对账: 千牛现价 vs ERP 应有值 (只读, 不改任何数据)。

背景 (2026-07-15): 长期"改 ERP、千牛没同步", 全店大面积价格漂移。本服务把
「千牛现价 vs ERP 应有值」做成可复跑的对账, 输入一份千牛「商品导出/发布模板」xlsx。

维度:
- **标价**: 千牛 SKU 价 (发布模板「价格(元)」列) vs ERP 应有一口价 (=PricingSku.list_price=日常价÷0.75)。
- 券后价 (单品立减维度): 需千牛「已报券后价」导出, 待补 (见 reconcile 的 TODO)。

★踩过的坑 (务必守住):
- 发布模板有两个「商家编码」列: 商品级(短, 如 23250050202) 和 SKU级(长, 如 PPS2325005020232)。
  取 **SKU 级(最后一个)**。
- 也有两个价格列: col「一口价」= 商品级占位(2000/1000); **真正 SKU 价在「价格(元)」列**。别读错。
"""
from __future__ import annotations

import io
from decimal import Decimal
from typing import Optional

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo

_TOL = Decimal("1")  # 容差 1 元 (千牛价与 ERP 应有值差 >1 元即算漂移)
_PUBLISH_FALLBACK_PRICE_COL = 13  # 价格(元)  (1-indexed, 发布模板固定兜底)
_PUBLISH_FALLBACK_CODE_COL = 16   # SKU 级商家编码


def parse_qn_publish_export(file_bytes: bytes) -> dict[str, float]:
    """解析千牛「商品导出/发布模板」→ {SKU商家编码: 千牛SKU价}。
    优先按表头名定位「价格(元)」和(最后一个)「商家编码」列; 找不到用发布模板固定列兜底。"""
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb["发布模板"] if "发布模板" in wb.sheetnames else wb.worksheets[0]
    hrow = 3
    for r in range(1, min(8, ws.max_row) + 1):
        vals = [str(ws.cell(r, c).value or "") for c in range(1, 10)]
        if any(v in ("商品Id", "商品ID") for v in vals):
            hrow = r
            break
    c_price: Optional[int] = None
    c_code: Optional[int] = None
    for c in range(1, ws.max_column + 1):
        h = str(ws.cell(hrow, c).value or "").strip()
        if h in ("价格(元)", "价格（元）"):
            c_price = c
        if h == "商家编码":
            c_code = c  # 取最后一个 = SKU 级
    if not c_price or not c_code:
        c_price, c_code = _PUBLISH_FALLBACK_PRICE_COL, _PUBLISH_FALLBACK_CODE_COL
    out: dict[str, float] = {}
    for r in range(hrow + 1, ws.max_row + 1):
        code = ws.cell(r, c_code).value
        price = ws.cell(r, c_price).value
        if code and price not in (None, ""):
            try:
                out[str(code).strip()] = float(price)
            except (TypeError, ValueError):
                pass
    return out


def reconcile_list_price(db: Session, qn_map: dict[str, float]) -> dict:
    """千牛 SKU 价 vs ERP 应有一口价(list_price)。返回漂移清单 + 统计。"""
    promo = {p.sku_code: p for p in db.execute(select(PricingSkuPromo)).scalars()}
    mism = []
    matched = 0
    for s in db.execute(select(PricingSku)).scalars():
        if s.is_custom_placeholder or not s.list_price:
            continue
        qp = qn_map.get(s.sku_code)
        if qp is None:
            continue
        ep = float(s.list_price)
        if abs(Decimal(str(ep)) - Decimal(str(qp))) > _TOL:
            pr = promo.get(s.sku_code)
            d = round(ep - qp, 2)
            mism.append({
                "taobao_item_id": str((pr.taobao_item_id if pr else "") or ""),
                "sku_code": s.sku_code,
                "sku_name": (s.sku or "")[:40],
                "qn_price": round(qp, 2),
                "erp_should": round(ep, 2),
                "diff": d,
                "direction": "千牛偏低,要抬" if d > 0 else "千牛偏高,要降",
            })
        else:
            matched += 1
    mism.sort(key=lambda m: -abs(m["diff"]))
    return {
        "mismatches": mism,
        "mismatch_count": len(mism),
        "matched": matched,
        "qn_total": len(qn_map),
    }


def reconcile(db: Session, file_bytes: bytes) -> dict:
    """完整对账: 解析千牛导出 → 标价对账。(TODO 券后价维度需千牛「已报券后价」导出)。"""
    qn = parse_qn_publish_export(file_bytes)
    res = reconcile_list_price(db, qn)
    res["parsed_qn_sku"] = len(qn)
    return res


def build_fix_xlsx(db: Session, file_bytes: bytes) -> io.BytesIO:
    """标价返修表: 把漂移 SKU 的「正确一口价(=ERP list_price)」列出, 供千牛「商品价格批量编辑」改回。"""
    res = reconcile(db, file_bytes)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "标价返修"
    ws.append(["淘宝链接ID", "SKU商家编码", "SKU名", "千牛现价", "正确一口价(改成这个)", "差", "方向"])
    for m in res["mismatches"]:
        ws.append([m["taobao_item_id"], m["sku_code"], m["sku_name"],
                   m["qn_price"], m["erp_should"], m["diff"], m["direction"]])
    ws.freeze_panes = "A2"
    for col, w in zip("ABCDEFG", [15, 18, 30, 12, 18, 9, 13]):
        ws.column_dimensions[col].width = w
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio
