"""导入「券后价历史线」— 千牛报名结果表/批量操作反馈 → PricingSkuPromo.coupon_floor_price。

★为什么要这张表 (2026-07-16 报名价体系重构):
  平台报名校验 = 活动【券后价】≤ 校验期内最低普惠券后价(记 L)。而 L = **上一轮真实到手**
  (实证: 樱桃木AA柱2.1米 L=9459.18 == 当时大促到手 9459.18, 一分不差)。
  旧「叠加法」下 真实到手 = 活动价×(1−比例) − 单品立减, 比"上一场活动价"低整整一刀立减 →
  只靠 enrolled_floor_price(上一场活动价) 封顶【挡不住】这条真线 → 2026-07-16 88VIP 60品报名
  42 失败(142行券后线)。**L 只能从平台回执学**, 系统自己算不出来。

与 enrolled_floor_import_service 的分工 (别混用):
  - enrolled_floor_import_service → enrolled_floor_price = 上一场【活动价】(报名价维度), 直接封顶报名价;
  - 本 service                    → coupon_floor_price   = 最低【普惠券后价】(到手维度), 封顶【名义券后】,
    即报名价 ≤ L ÷ (1−该场比例) (见 data_export_service._coupon_floor_cap)。

输入表 = 报名结果/失败回执(逐 SKU 一行), 需含「商品SKU ID」与「平台历史线/最低普惠券后价」两列。
列名按 _SID_ALIASES / _LINE_ALIASES 模糊匹配(千牛各场次表头不完全一致)。
匹配: SKUID ↔ promo.taobao_sku_id(或 alt); 重复导入取 min —— **线只会更低不会抬高**(平台线单调向下)。
"""
from __future__ import annotations

import io
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pricing_ext import PricingSkuPromo

# 千牛各场次/各版本表头不一致 → 模糊匹配(命中即用)。顺序=优先级。
_SID_ALIASES = ("商品SKU ID", "商品SKUID", "SKUID", "SKU ID", "sku_id")
_LINE_ALIASES = ("平台历史线(最低普惠券后)", "平台历史线", "最低普惠券后价", "历史最低普惠券后价",
                 "校验期最低普惠券后价", "最低券后价")


def _find_col(header: "list[str]", aliases: "tuple[str, ...]") -> Optional[int]:
    for a in aliases:                       # 先精确
        if a in header:
            return header.index(a)
    for a in aliases:                       # 再包含
        for i, h in enumerate(header):
            if h and a in h:
                return i
    return None


def import_from_xlsx_bytes(db: Session, raw: bytes, sheet: Optional[str] = None,
                           header_row: int = 0) -> dict:
    """读表 → 回填 coupon_floor_price。header_row: 表头所在行(0-based); 数据从其下一行起。
    返回统计 dict(不静默: 未匹配 SKUID 抽样回报)。"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb[sheet] if (sheet and sheet in wb.sheetnames) else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if len(rows) <= header_row + 1:
        return {"ok": False, "error": "表里没有数据行"}
    header = [str(c).strip() if c is not None else "" for c in rows[header_row]]
    i_sid = _find_col(header, _SID_ALIASES)
    i_line = _find_col(header, _LINE_ALIASES)
    if i_sid is None or i_line is None:
        return {"ok": False, "error": f"没找到 SKUID/券后线 列; 实际表头: {[h for h in header if h][:12]}"}

    line_by_sid: "dict[str, Decimal]" = {}
    for row in rows[header_row + 1:]:
        if not row or i_sid >= len(row) or row[i_sid] is None:
            continue
        sid = str(row[i_sid]).strip()
        if not sid or i_line >= len(row) or row[i_line] is None:
            continue
        try:
            line = Decimal(str(row[i_line]))
        except Exception:  # noqa: BLE001
            continue
        if line <= 0:
            continue
        if sid not in line_by_sid or line < line_by_sid[sid]:   # 同SKUID多行取最低(从严)
            line_by_sid[sid] = line

    promos = db.execute(select(PricingSkuPromo)).scalars().all()
    updated = matched = 0
    changes: "list[dict]" = []
    unmatched = set(line_by_sid)
    for p in promos:
        ids = [str(p.taobao_sku_id).strip()] if p.taobao_sku_id else []
        ids += [str(a).strip() for a in (p.alt_taobao_sku_ids or []) if a]
        hit = next((i for i in ids if i in line_by_sid), None)
        if hit is None:
            continue
        matched += 1
        unmatched.discard(hit)
        new = line_by_sid[hit]
        old = p.coupon_floor_price
        if old is None or new < old:        # ★线只降不抬(平台线单调向下)
            p.coupon_floor_price = new
            updated += 1
            changes.append({"sku_code": p.sku_code, "taobao_sku_id": hit,
                            "old": float(old) if old is not None else None, "new": float(new)})
    db.commit()
    return {"ok": True, "file_rows": len(line_by_sid), "matched_sku": matched,
            "updated": updated, "changes": changes[:50],
            "unmatched_count": len(unmatched), "unmatched_sample": sorted(unmatched)[:10]}
