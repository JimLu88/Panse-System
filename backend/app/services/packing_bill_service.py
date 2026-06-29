# -*- coding: utf-8 -*-
"""打包费手写账单 OCR 预览结果 → 入库 + 配单 + 月度核算 (用户 2026-06-21)。

流程: api/screenshots.py /packing-bill/parse (vision OCR) → 前端人工复核 → /commit 调本服务。
- 配单: matched_order_no = OCR单号优先, 否则按客户名在订单库唯一匹配 (排除关闭单, 铁律)。
- 「改客户/不计入/作废」行 excluded=True, 不计入应付总额 (用户 C②: 自动剔除错误数据)。
- month_summary: 当月应付打包费 = Σ packing_fee(excluded=False); 与本子「合计」互核。
手写姓名 OCR 准确率有限 → 入库前必须人工在预览页复核, 本服务只做确认后入库。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import PackingBill
from app.models.order import Order
from app.services.sales_analytics import SETTLED_SALE_STATUSES

# 本子上常见的「不计入」批注 → excluded=True (用户 C②)
EXCLUDE_KEYWORDS = ("改客户", "不计入", "不算", "作废", "退了", "退货", "非本店",
                    "不是我们", "划掉", "取消", "删除")

MATCH_CN = {
    "order_no": "单号匹配", "name_unique": "客户名唯一", "name_addr": "姓名在地址(宽松)",
    "multi": "多候选待人工", "none": "未能自动匹配", "manual": "人工指定",
}

_PROV_RE = re.compile(r"^(北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|"
                      r"山东|河南|湖北|湖南|广东|广西|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|"
                      r"西藏|宁夏|新疆|香港|澳门)")


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


def _is_excluded(row: dict) -> tuple[bool, Optional[str]]:
    if row.get("excluded") is True:
        return True, (str(row.get("exclude_reason") or "本子标注不计入").strip() or "本子标注不计入")
    text = f"{row.get('exclude_reason') or ''} {row.get('note') or ''}"
    for kw in EXCLUDE_KEYWORDS:
        if kw in text:
            return True, kw
    return False, None


def _order_indices(db: Session):
    """已发货成交单 (排除关闭/未付款单) 的 order_no 集合 + 客户名→订单列表。"""
    rows = db.execute(
        select(Order.order_no, Order.customer_name)
        .where(Order.status.in_(SETTLED_SALE_STATUSES))
    ).all()
    valid_nos = set()
    by_name: dict[str, list[str]] = {}
    for o in rows:
        if o.order_no:
            valid_nos.add(o.order_no)
        if o.customer_name:
            by_name.setdefault(o.customer_name.strip(), []).append(o.order_no)
    return valid_nos, by_name


def _match_row(bill: PackingBill, valid_nos: set, by_name: dict) -> None:
    """给单行打包费账单配订单, 写 matched_order_no/match_method/match_note。"""
    if bill.order_no and bill.order_no in valid_nos:
        bill.matched_order_no = bill.order_no
        bill.match_method = "order_no"
        bill.match_note = None
        return
    name = (bill.customer_name or "").strip()
    cands = by_name.get(name, [])
    if name and len(cands) == 1:
        bill.matched_order_no = cands[0]
        bill.match_method = "name_unique"
        bill.match_note = None
    elif name and len(cands) > 1:
        bill.match_method = "multi"
        bill.match_note = f"客户「{name}」有多笔订单: {'/'.join(sorted(cands)[:5])}"
    else:
        bill.match_method = "none"
        bill.match_note = (f"订单库无客户「{name}」(手写名可能认错/主订单未导入)"
                           if name else "无客户名, 无法配单")


def commit_packing_parsed(db: Session, rows: list[dict], *, bill_month: Optional[str] = None,
                          declared_total: Optional[float] = None,
                          source_image: Optional[str] = None,
                          import_job_id: Optional[int] = None) -> dict:
    """确认后的打包费账单行 → 入库 PackingBill, 逐行配单 + 自动剔除「不计入」行。

    去重: 同 (bill_month, customer_name, packing_fee, row_date) 已存在则跳过 (防重复发图翻倍)。
    declared_total: 手写本上的「合计」数字; 与系统应付对不上(>¥0.5)→ 挂异常待人工核对
    (用户 2026-06-21: 加总不对就让系统挂异常)。
    """
    valid_nos, by_name = _order_indices(db)
    existing = {
        (m, c, f, d) for m, c, f, d in db.execute(
            select(PackingBill.bill_month, PackingBill.customer_name,
                   PackingBill.packing_fee, PackingBill.row_date)
        ).all()
    }
    seen: set = set()
    inserted = skipped = matched = excluded = 0
    for row in rows or []:
        fee = _dec(row.get("packing_fee"))
        name = (row.get("customer_name") or "").strip() or None
        if fee is None and not name:
            continue  # 完全空行
        rdate = _date(row.get("row_date"))
        key = (bill_month, name, fee, rdate)
        if key in existing or key in seen:
            skipped += 1
            continue
        seen.add(key)
        is_excl, reason = _is_excluded(row)
        conf = _dec(row.get("confidence"))
        bill = PackingBill(
            bill_month=bill_month, row_date=rdate, customer_name=name,
            order_no=(str(row.get("order_no")).strip() if row.get("order_no") else None),
            product=(str(row.get("product")).strip() if row.get("product") else None),
            packing_fee=fee, excluded=is_excl, exclude_reason=reason,
            confidence=(conf if conf is not None and 0 <= conf <= 1 else None),
            note=(str(row.get("note")).strip() if row.get("note") else None),
            source_image=source_image, import_job_id=import_job_id,
        )
        _match_row(bill, valid_nos, by_name)
        db.add(bill)
        inserted += 1
        if bill.matched_order_no:
            matched += 1
        if is_excl:
            excluded += 1
    db.flush()
    # 配单后回填订单的 实际打包费分量, 供 physical_cost 用实际替预估 (用户 2026-06-21)
    try:
        from app.services import order_fee_actual_service
        order_fee_actual_service.sync_fee_components(db)
    except Exception:  # noqa: BLE001
        pass
    summary = month_summary(db, bill_month)
    # 本子合计 vs 系统应付对不上 → 挂异常 (用户 2026-06-21: 加总不对就让系统挂异常)
    mismatch = None
    if declared_total is not None:
        try:
            dt = Decimal(str(declared_total))
            diff = dt - Decimal(str(summary["payable_total"]))
            if abs(diff) > Decimal("0.5"):
                mismatch = float(diff)
                from app.services import exception_service
                exception_service.record(
                    db, source_table="packing_bills", source_pk=bill_month,
                    exception_type="packing_total_mismatch", severity="warning",
                    description=(f"打包费账单 {bill_month or '(未填账期)'}: 本子合计 ¥{dt} 与系统"
                                 f"应付 ¥{summary['payable_total']} 差 ¥{diff}, 请核对手写本逐行金额。"),
                    suggestion_action="review_packing_rows",
                    context={"bill_month": bill_month, "declared_total": float(dt),
                             "payable_total": summary["payable_total"], "diff": float(diff)},
                )
                db.flush()
        except (InvalidOperation, ValueError):
            pass
    return {"inserted": inserted, "skipped": skipped, "matched": matched,
            "excluded": excluded, "total_mismatch": mismatch, **summary}


def month_summary(db: Session, bill_month: Optional[str]) -> dict:
    """当月打包费核算: 应付 = Σ未剔除; 剔除额/未配单数 单列, 供与本子合计互核。"""
    stmt = select(PackingBill)
    if bill_month:
        stmt = stmt.where(PackingBill.bill_month == bill_month)
    bills = db.execute(stmt).scalars().all()
    payable = sum((b.packing_fee or Decimal("0")) for b in bills if not b.excluded)
    excl_amt = sum((b.packing_fee or Decimal("0")) for b in bills if b.excluded)
    return {
        "rows_total": len(bills),
        "payable_total": float(payable),                       # 应付打包费 (已剔除不计入)
        "excluded_total": float(excl_amt),                     # 被剔除金额
        "excluded_rows": sum(1 for b in bills if b.excluded),
        "unmatched_rows": sum(1 for b in bills if not b.matched_order_no and not b.excluded),
    }


def _note_province(note: Optional[str]) -> Optional[str]:
    """从打包备注(我存成『省份 产品…』)抽省份首词, 给宽松匹配交叉验证。"""
    if not note:
        return None
    m = _PROV_RE.match(note.strip())
    return m.group(1) if m else None


_UNSET = object()


def update_row(
    db: Session,
    bill_id: int,
    *,
    customer_name=_UNSET,
    packing_fee=_UNSET,
    matched_order_no=_UNSET,
    excluded=_UNSET,
    note=_UNSET,
    bill_month=_UNSET,
    rematch: bool = False,
) -> Optional[PackingBill]:
    """手动编辑一行打包费账单 (用户 2026-06-24): 改客户名/打包费/手动配单/改账期。
    - 传 matched_order_no 非空 → 人工指定配单 (match_method='manual'); 传空字符串 → 清空配单退回自动。
    - 传 bill_month 非空 → 改账期 (手写本错填月份/OCR错识别月份时挪正确账期, 用户 2026-06-29)。
    - rematch=True 且当前未配单 → 按(可能改过的)客户名自动重配一次, 让"改对名字就能配上"。"""
    b = db.get(PackingBill, bill_id)
    if b is None:
        return None
    if customer_name is not _UNSET:
        b.customer_name = (customer_name or "").strip() or None
    if packing_fee is not _UNSET:
        b.packing_fee = _dec(packing_fee)
    if bill_month is not _UNSET and bill_month:
        b.bill_month = str(bill_month).strip()
    if excluded is not _UNSET:
        b.excluded = bool(excluded)
        if not excluded:
            b.exclude_reason = None
    if note is not _UNSET:
        b.note = note
    if matched_order_no is not _UNSET:
        mo = (matched_order_no or "").strip()
        if mo:
            b.matched_order_no = mo
            b.order_no = mo
            b.match_method = "manual"
            b.match_note = None
        else:
            b.matched_order_no = None
            b.match_method = None
            b.match_note = None
    if rematch and not b.matched_order_no and not b.excluded:
        valid_nos, by_name = _order_indices(db)
        _match_row(b, valid_nos, by_name)
    db.flush()
    return b


def match_candidates(db: Session, bill_id: int, limit: int = 5,
                     name_override: Optional[str] = None) -> list[dict]:
    """按客户名相似度给一行打包费账单列候选订单(供人工下拉选), 按匹配度高→低排序取前 N。
    name_override: 传入则用它(而非已存客户名)算相似度 —— 让"改了名字还没保存"也能即时看候选。
    (用户 2026-06-24: 自动配会配错/配到多单, 改成下拉自选, 按相似百分比排序)"""
    import difflib
    b = db.get(PackingBill, bill_id)
    if b is None:
        return []
    name = ((name_override if name_override is not None else b.customer_name) or "").strip()
    if not name:
        return []
    rows = db.execute(
        select(Order.order_no, Order.customer_name, Order.product_name, Order.paid_amount, Order.order_date)
        .where(Order.status.in_(SETTLED_SALE_STATUSES))
    ).all()
    scored = []
    for o in rows:
        cn = (o.customer_name or "").strip()
        if not o.order_no or not cn:
            continue
        if cn == name:
            score = 1.0
        elif name in cn or cn in name:
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, name, cn).ratio()
        scored.append((score, o.order_no, cn, o.product_name, o.paid_amount, o.order_date))
    scored.sort(key=lambda x: (-x[0], str(x[5] or ""), x[1]))
    out = []
    for score, ono, cn, prod, paid, odate in scored[:limit]:
        out.append({
            "order_no": ono,
            "customer_name": cn,
            "product_name": prod,
            "paid_amount": float(paid) if paid is not None else None,
            "order_date": str(odate) if odate else None,
            "score": round(float(score), 3),
        })
    return out


def rematch_packing_bills(db: Session, *, loose: bool = True) -> dict:
    """对未配单(且非剔除/非人工)的打包费行重跑配单。loose=True 多一档:
    客户名(≥2字)出现在订单收货地址里 + 备注省份也对上 → 唯一才配
    (应对 订单存买家昵称、打包本写客户真名 的错位)。返回 {matched, multi, none}。"""
    valid_nos, by_name = _order_indices(db)
    addr_orders = []
    if loose:
        addr_orders = db.execute(
            select(Order.order_no, Order.customer_name, Order.customer_address)
            .where(Order.status.in_(SETTLED_SALE_STATUSES))
        ).all()
    bills = db.execute(
        select(PackingBill).where(
            PackingBill.matched_order_no.is_(None),
            PackingBill.excluded == False,  # noqa: E712
            (PackingBill.match_method.is_(None)) | (PackingBill.match_method != "manual"),
        )
    ).scalars().all()
    counts = {"matched": 0, "multi": 0, "none": 0}
    for b in bills:
        _match_row(b, valid_nos, by_name)
        if not b.matched_order_no and loose and b.customer_name and len(b.customer_name.strip()) >= 2:
            nm = b.customer_name.strip()
            prov = _note_province(b.note)
            cand = {o.order_no for o in addr_orders if o.customer_address and nm in o.customer_address
                    and (not prov or prov in o.customer_address)}
            if len(cand) == 1:
                b.matched_order_no = next(iter(cand))
                b.match_method = "name_addr"
                b.match_note = None
            elif len(cand) > 1:
                b.match_method = "multi"
                b.match_note = f"客户「{nm}」地址命中多单待人工"
        if b.matched_order_no:
            counts["matched"] += 1
        elif b.match_method == "multi":
            counts["multi"] += 1
        else:
            counts["none"] += 1
    db.flush()
    try:
        from app.services import order_fee_actual_service
        order_fee_actual_service.sync_fee_components(db)
    except Exception:  # noqa: BLE001
        pass
    return counts
