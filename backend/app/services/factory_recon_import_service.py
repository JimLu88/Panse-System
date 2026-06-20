"""工厂侧对账单 xlsx 导入 → factory_recon_items (逐单工厂结算明细)。

数据源: 工厂侧对账单 xlsx, 多 sheet。表头在第 2 行:
    单号 | 订单号 | 追加订单号1 | (追加订单号2) | 备注 | 详情 | 数量 | 价格 | 客户信息 | 下单时间 | 发货时间 | (备注)
「价格」= 工厂结算价 = 我们付给工厂的成本 (用户确认口径)。
日期是 Excel 序列号 (base 1899-12-30), 也可能是 datetime / 文本(如"未发货") → 文本转 None。

去重: 按 (source_sheet, order_no, settle_price) 批内 + DB; 无 order_no 行按 (sheet, doc_no, detail, settle_price)。
导入后顺带按 订单号(含追加) 回填 Order.actual_cost (best-effort, 支撑 WS2 工厂口径利润)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.factory_recon_item import FactoryReconItem
from app.models.order import Order

# 表头别名 → 字段
_HEADER_MAP = {
    "单号": "doc_no",
    "订单号": "order_no",
    "追加订单号1": "extra_order_no1", "追加订单号一": "extra_order_no1",
    "追加订单号2": "extra_order_no2", "追加订单号二": "extra_order_no2",
    "详情": "detail",
    "数量": "qty",
    "价格": "settle_price", "结算价": "settle_price", "工厂价": "settle_price",
    "客户信息": "customer_info", "客户": "customer_info",
    "下单时间": "order_date", "下单日期": "order_date",
    "发货时间": "ship_date", "发货日期": "ship_date",
    "备注": "remark",
}

_EXCEL_EPOCH = date(1899, 12, 30)  # Excel 序列号基准 (含 1900 闰年 bug 的惯例基准)


@dataclass
class FactoryReconImportReport:
    inserted: int = 0
    skipped_invalid: int = 0
    skipped_duplicate: int = 0
    backfilled_cost: int = 0
    sheets: list = field(default_factory=list)
    unmapped_columns: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _norm_header(h: Any) -> str:
    return str(h).replace(" ", "").replace("　", "").strip() if h is not None else ""


def _to_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _dec(v: Any) -> Optional[Decimal]:
    if v is None or str(v).strip() == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").replace("¥", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _to_date(v: Any) -> Optional[date]:
    """Excel 序列号 / datetime / 文本 → date; 无法解析(如"未发货")→ None。"""
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):
        try:
            return _EXCEL_EPOCH + timedelta(days=int(v))
        except (ValueError, OverflowError):
            return None
    s = str(v).strip()
    if s.isdigit():
        try:
            return _EXCEL_EPOCH + timedelta(days=int(s))
        except (ValueError, OverflowError):
            return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _dedup_key(sheet, doc_no, order_no, detail, customer, order_date, qty, price):
    """统一去重键(跨批稳定):
    - 有订单号 → 订单号最权威(同 sheet 同价判重);
    - 否则有单号 → 单号+详情+价;
    - 都没有 → 详情+客户+下单日+数量+价 的组合键(避免同价同详情误判重而丢行)。
    """
    if order_no:
        return ("o", sheet, order_no, price)
    if doc_no:
        return ("d", sheet, doc_no, detail, price)
    return ("x", sheet, detail, customer, order_date, qty, price)


def _find_header_row(rows: list[tuple]) -> int:
    """找到含「订单号」「价格」的表头行 (通常第 2 行, 0-based=1)。找不到默认第 2 行。"""
    for idx, row in enumerate(rows[:6]):
        cells = {_norm_header(c) for c in row}
        if "订单号" in cells and ("价格" in cells or "数量" in cells):
            return idx
    return 1


def import_factory_recon_xlsx(db: Session, content: bytes) -> FactoryReconImportReport:
    """解析工厂侧对账单 xlsx (所有 sheet), 写入 factory_recon_items 并回填 Order.actual_cost。"""
    import openpyxl  # 延迟导入 (与 settlement_import_service 一致)

    rep = FactoryReconImportReport()
    try:
        wb = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)
    except Exception as e:  # pragma: no cover
        rep.errors.append(f"无法读取 xlsx: {e}")
        return rep

    # 现有去重键 (统一键)
    existing_keys: set = {
        _dedup_key(it.source_sheet, it.doc_no, it.order_no, it.detail,
                   it.customer_info, it.order_date, it.qty, it.settle_price)
        for it in db.execute(select(FactoryReconItem)).scalars().all()
    }

    seen_keys: set = set()
    unmapped: set = set()
    new_order_ids: set = set()   # 本次导入新出现的订单号(含追加) — 仅回填这些, 避免跨批/跨表重复累加

    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        hdr_idx = _find_header_row(rows)
        header = rows[hdr_idx]
        # 列 → 字段 (位置映射, 兼容重复"备注")
        col_fields: list[tuple[int, str]] = []
        for ci, h in enumerate(header):
            nh = _norm_header(h)
            fld = _HEADER_MAP.get(nh)
            if fld:
                col_fields.append((ci, fld))
            elif nh:
                unmapped.add(nh)

        sheet_inserted = 0
        for row in rows[hdr_idx + 1:]:
            rec: dict[str, Any] = {}
            remarks: list[str] = []
            for ci, fld in col_fields:
                val = row[ci] if ci < len(row) else None
                if fld == "remark":
                    rv = _to_str(val)
                    if rv:
                        remarks.append(rv)
                else:
                    rec[fld] = val

            order_no = _to_str(rec.get("order_no"))
            doc_no = _to_str(rec.get("doc_no"))
            settle_price = _dec(rec.get("settle_price"))
            detail = _to_str(rec.get("detail"))
            customer = _to_str(rec.get("customer_info"))
            order_date = _to_date(rec.get("order_date"))
            qty_d = _dec(rec.get("qty"))
            qty = int(qty_d) if qty_d is not None else 1
            # 整行空 / 无价格且无单号 → 跳过 (合计行/空行)
            if settle_price is None and not order_no and not doc_no:
                continue
            if settle_price is None:
                rep.skipped_invalid += 1
                continue

            key = _dedup_key(ws.title, doc_no, order_no, detail, customer, order_date, qty, settle_price)
            if key in existing_keys or key in seen_keys:
                rep.skipped_duplicate += 1
                continue
            seen_keys.add(key)

            item = FactoryReconItem(
                source_sheet=ws.title,
                doc_no=doc_no,
                order_no=order_no,
                extra_order_no1=_to_str(rec.get("extra_order_no1")),
                extra_order_no2=_to_str(rec.get("extra_order_no2")),
                detail=detail,
                qty=qty,
                settle_price=settle_price,
                customer_info=customer,
                order_date=order_date,
                ship_date=_to_date(rec.get("ship_date")),
                remark="; ".join(remarks) if remarks else None,
                source="import",
            )
            db.add(item)
            rep.inserted += 1
            sheet_inserted += 1
            for no in (order_no, item.extra_order_no1, item.extra_order_no2):
                if no:
                    new_order_ids.add(no)
        rep.sheets.append({"sheet": ws.title, "inserted": sheet_inserted})

    db.flush()
    rep.unmapped_columns = sorted(unmapped)
    rep.backfilled_cost = backfill_order_actual_cost(db, restrict_to=new_order_ids or None)
    db.flush()
    return rep


def backfill_order_actual_cost(db: Session, restrict_to: Optional[set] = None) -> int:
    """按 订单号(含追加1/2) 把工厂结算价回填到 Order.actual_cost (仅填空, best-effort)。

    一个订单可能拆多行(追加单) → 按订单号汇总 settle_price 求和后回填。
    restrict_to: 只处理这些订单号(本次导入新出现的), 避免跨批/跨表对同一订单重复累加。
    """
    stmt = select(FactoryReconItem)
    if restrict_to is not None:
        if not restrict_to:
            return 0
        ids = list(restrict_to)
        stmt = stmt.where(or_(
            FactoryReconItem.order_no.in_(ids),
            FactoryReconItem.extra_order_no1.in_(ids),
            FactoryReconItem.extra_order_no2.in_(ids),
        ))
    items = db.execute(stmt).scalars().all()
    # 一行结算 = 一件家具的木作价, 只算一次成本。一物多单(主单+追加号)→ 归到该结算组里
    # 第一个真实存在的订单, 不给主+追加各加一遍(否则成本翻倍, 2026-06-21 修)。0 价(退货)跳过。
    cands: set = set()
    for it in items:
        for no in (it.order_no, it.extra_order_no1, it.extra_order_no2):
            if (no or "").strip():
                cands.add(no.strip())
    existing = {
        o.order_no: o for o in db.execute(
            select(Order).where(Order.order_no.in_(list(cands)))
        ).scalars().all()
    } if cands else {}
    sums: dict[str, Decimal] = {}
    for it in items:
        if not it.settle_price or it.settle_price <= 0:
            continue
        primary = next(
            (n.strip() for n in (it.order_no, it.extra_order_no1, it.extra_order_no2)
             if (n or "").strip() and n.strip() in existing
             and (restrict_to is None or n.strip() in restrict_to)),
            None,
        )
        if primary:
            sums[primary] = sums.get(primary, Decimal("0")) + it.settle_price
    filled = 0
    for ono, total in sums.items():
        o = existing.get(ono)
        if o is not None and o.actual_cost is None:
            o.actual_cost = total
            filled += 1
    return filled
