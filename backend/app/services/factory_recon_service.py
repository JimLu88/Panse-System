"""工厂逐单对账 — 基于导入的工厂侧对账单 (factory_recon_items)。

口径 (用户确认): 「价格」= 工厂结算价 = 应付工厂的成本。
逐月对账: 应付(Σ结算价) ↔ 实付(支付宝 reconciliation_type='factory_payment' 支出)。
对不上 → 逐单查, 在条目上「填原因做平」(resolved + settle_reason, 记 谁/何时)。
当某月所有差异条目都已填原因, 该月状态 = explained(已归因做平)。

与 factory_reconciliation_service 的区别: 那个是 表6 FactoryOrder 的 (工厂×周期) 汇总;
本服务是工厂侧对账单逐行明细 + 行级做平 (也是手工差异归因的雏形)。
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.factory_recon_item import FactoryReconItem
from app.models.finance import AlipayFlow
from app.models.order import FactoryOrder, Order

_TOLERANCE = Decimal("5")   # 应付 vs 实付 对平容差 (元)


def preview_from_orders(db: Session, *, limit: int = 2000) -> dict:
    """工厂对账单未导入时, 用我方「工厂下单」数据生成逐单预估 (只读, 不写 FactoryReconItem)。

    应付 = 工厂账单额(factory_bill_amount); 缺则退回该订单理论成本(预估)。让用户在工厂对账单
    没到之前, 也能逐单看「我方应付」, 待工厂对账单到了再正式核对。用户拍板 2026-06-17:
    系统不能凭空造工厂的对账单价, 但我方下单数据本就有, 拿来预估总比空页强。
    """
    fos = db.execute(
        select(FactoryOrder).order_by(FactoryOrder.order_date.desc().nulls_last())
    ).scalars().all()
    # 理论成本兜底: 按淘宝单号取 Order.theoretical_cost
    theo: dict[str, Decimal] = {}
    pono_list = [f.platform_order_no for f in fos if f.platform_order_no]
    if pono_list:
        for no, tc in db.execute(
            select(Order.order_no, Order.theoretical_cost).where(Order.order_no.in_(pono_list))
        ).all():
            if tc is not None:
                theo[no] = tc
    rows = []
    for fo in fos[:limit]:
        payable = fo.factory_bill_amount
        src = "工厂账单额"
        if payable is None and fo.platform_order_no and fo.platform_order_no in theo:
            payable = theo[fo.platform_order_no]
            src = "订单理论成本(预估)"
        rows.append({
            "factory_order_no": fo.factory_order_no,
            "platform_order_no": fo.platform_order_no,
            "internal_order_no": fo.internal_order_no,
            "factory_name": fo.factory_name,
            "payable": float(payable) if payable is not None else None,
            "payable_source": src if payable is not None else "未知(无账单额/无理论成本)",
            "order_date": fo.order_date.isoformat() if fo.order_date else None,
        })
    total_payable = sum((r["payable"] or 0) for r in rows)
    return {
        "total": len(fos), "rows": rows, "total_payable": round(total_payable, 2),
        "note": "我方工厂下单数据生成的逐单预估(非工厂对账单)。应付优先取工厂账单额, "
                "缺则取订单理论成本。工厂正式对账单导入后以对账单为准。",
    }


def _month_key(d) -> Optional[str]:
    return f"{d.year}-{d.month:02d}" if d else None


def _paid_by_month(db: Session) -> dict[str, Decimal]:
    """支付宝 factory_payment 支出按月汇总 (实付)。"""
    rows = db.execute(
        select(AlipayFlow.transaction_time, AlipayFlow.amount)
        .where(AlipayFlow.reconciliation_type == "factory_payment")
    ).all()
    out: dict[str, Decimal] = {}
    for t, amt in rows:
        key = _month_key(t) if t else "(无日期)"
        out[key] = out.get(key, Decimal("0")) + abs(Decimal(amt or 0))
    return out


def summary(db: Session) -> dict:
    """逐月对账汇总: 应付/实付/差额/已归因, + 全局合计 + 覆盖。"""
    items = db.execute(select(FactoryReconItem)).scalars().all()
    paid_by_month = _paid_by_month(db)

    by_month: dict[str, dict] = {}
    for it in items:
        key = _month_key(it.order_date) or "(无日期)"
        m = by_month.setdefault(key, {
            "period": key, "items_total": 0, "items_resolved": 0,
            "billed": Decimal("0"), "resolved_amount": Decimal("0"),
        })
        m["items_total"] += 1
        m["billed"] += it.settle_price or Decimal("0")
        if it.resolved:
            m["items_resolved"] += 1
            m["resolved_amount"] += it.settle_price or Decimal("0")

    rows = []
    tot_billed = tot_paid = Decimal("0")
    for key in sorted(set(by_month) | set(paid_by_month), reverse=True):
        m = by_month.get(key, {"period": key, "items_total": 0, "items_resolved": 0,
                               "billed": Decimal("0"), "resolved_amount": Decimal("0")})
        billed = m["billed"]
        paid = paid_by_month.get(key, Decimal("0"))
        diff = paid - billed
        open_items = m["items_total"] - m["items_resolved"]
        if abs(diff) <= _TOLERANCE:
            status = "balanced"
        elif m["items_total"] > 0 and open_items == 0:
            status = "explained"     # 全部差异已填原因做平(残差=已解释的扣减/减免合计)
        else:
            status = "diff"          # 待归因
        rows.append({
            "period": key,
            "items_total": m["items_total"],
            "items_resolved": m["items_resolved"],
            "items_open": open_items,
            "billed": float(billed),
            "paid": float(paid),
            "diff": float(diff),
            "status": status,
        })
        tot_billed += billed
        tot_paid += paid

    return {
        "total_items": len(items),
        "total_billed": float(tot_billed),
        "total_paid": float(tot_paid),
        "total_diff": float(tot_paid - tot_billed),
        "resolved_items": sum(1 for it in items if it.resolved),
        "months": rows,
    }


def list_items(
    db: Session, *, period: Optional[str] = None, status: Optional[str] = None,
    q: Optional[str] = None, limit: int = 500, offset: int = 0,
) -> dict:
    """逐单明细列表。status: resolved / open; period: YYYY-MM; q: 订单号/客户/详情 关键词。"""
    stmt = select(FactoryReconItem).order_by(
        FactoryReconItem.order_date.desc().nulls_last(), FactoryReconItem.id.desc()
    )
    if status == "resolved":
        stmt = stmt.where(FactoryReconItem.resolved.is_(True))
    elif status == "open":
        stmt = stmt.where(FactoryReconItem.resolved.is_(False))
    rows = db.execute(stmt).scalars().all()

    def _match(it: FactoryReconItem) -> bool:
        if period and (_month_key(it.order_date) or "(无日期)") != period:
            return False
        if q:
            hay = " ".join(str(x or "") for x in (
                it.order_no, it.extra_order_no1, it.extra_order_no2,
                it.customer_info, it.detail, it.doc_no))
            if q not in hay:
                return False
        return True

    filtered = [it for it in rows if _match(it)]
    total = len(filtered)
    page = filtered[offset:offset + limit]
    return {
        "total": total,
        "rows": [{
            "id": it.id, "source_sheet": it.source_sheet, "doc_no": it.doc_no,
            "order_no": it.order_no, "extra_order_no1": it.extra_order_no1,
            "extra_order_no2": it.extra_order_no2, "detail": it.detail, "qty": it.qty,
            "settle_price": float(it.settle_price or 0),
            "customer_info": it.customer_info,
            "order_date": it.order_date.isoformat() if it.order_date else None,
            "ship_date": it.ship_date.isoformat() if it.ship_date else None,
            "remark": it.remark, "resolved": it.resolved,
            "settle_reason": it.settle_reason, "resolved_by": it.resolved_by,
            "resolved_at": it.resolved_at.isoformat() if it.resolved_at else None,
        } for it in page],
    }


RESOLUTION_KINDS = ("漏单", "价差", "运费", "补偿", "其他")


def split_item(db: Session, item_id: int, *, parts: list[dict],
               actor: Optional[str] = None) -> dict:
    """Plan L5: 把一条差异行拆成多条归因子行。Σ 子行金额必须 = 原行金额 (Decimal 校验)。

    parts: [{"amount": "120.00", "resolution_kind": "价差", "remark": "..."}]
    """
    it = db.get(FactoryReconItem, item_id)
    if it is None:
        raise ValueError(f"工厂对账条目不存在: {item_id}")
    if it.parent_item_id is not None:
        raise ValueError("子行不能再拆分")
    if not parts or len(parts) < 2:
        raise ValueError("拆分至少要两条")
    total = Decimal("0")
    cleaned: list[tuple[Decimal, str, Optional[str]]] = []
    for p in parts:
        try:
            amt = Decimal(str(p.get("amount")))
        except Exception:
            raise ValueError(f"拆分金额不是数字: {p.get('amount')!r}")
        kind = (p.get("resolution_kind") or "").strip()
        if kind not in RESOLUTION_KINDS:
            raise ValueError(f"归因必须是 {'/'.join(RESOLUTION_KINDS)} 之一: {kind!r}")
        cleaned.append((amt, kind, (p.get("remark") or None)))
        total += amt
    if total != Decimal(it.settle_price or 0):
        raise ValueError(f"拆分金额合计 {total} ≠ 原行金额 {it.settle_price}, 必须打平")
    children = []
    for amt, kind, remark in cleaned:
        child = FactoryReconItem(
            source_sheet=it.source_sheet, doc_no=it.doc_no, order_no=it.order_no,
            detail=it.detail, qty=it.qty, settle_price=amt,
            customer_info=it.customer_info, order_date=it.order_date,
            ship_date=it.ship_date, remark=remark,
            parent_item_id=it.id, resolution_kind=kind, source="split",
        )
        db.add(child)
        children.append(child)
    # 原行标记已做平 (被拆分), 金额保留供追溯
    it.resolved = True
    it.settle_reason = f"已拆分为 {len(children)} 条归因子行"
    it.resolved_by = actor
    it.resolved_at = datetime.now(timezone.utc)
    db.flush()
    return {"parent_id": it.id, "children": [c.id for c in children]}


def confirm_item(db: Session, item_id: int, *, resolution_kind: str,
                 actor: Optional[str] = None) -> dict:
    """Plan L5: 确认一条差异行的归因。全部确认后调用方可触发期间重算。"""
    it = db.get(FactoryReconItem, item_id)
    if it is None:
        raise ValueError(f"工厂对账条目不存在: {item_id}")
    kind = (resolution_kind or "").strip()
    if kind not in RESOLUTION_KINDS:
        raise ValueError(f"归因必须是 {'/'.join(RESOLUTION_KINDS)} 之一: {kind!r}")
    it.resolution_kind = kind
    it.confirmed_by = actor
    it.confirmed_at = datetime.now(timezone.utc)
    if not it.resolved:
        it.resolved = True
        it.settle_reason = it.settle_reason or f"确认归因: {kind}"
        it.resolved_by = actor
        it.resolved_at = it.confirmed_at
    db.flush()
    return {"id": it.id, "resolution_kind": it.resolution_kind,
            "confirmed_at": it.confirmed_at.isoformat()}


def resolve(db: Session, item_id: int, *, reason: str, actor: Optional[str] = None,
            resolved: bool = True) -> dict:
    """对某条工厂结算行「填原因做平」(或撤销)。reason=扣减/减免/差异原因。"""
    it = db.get(FactoryReconItem, item_id)
    if it is None:
        raise ValueError(f"工厂对账条目不存在: {item_id}")
    if resolved:
        if not (reason or "").strip():
            raise ValueError("做平必须填写原因")
        it.resolved = True
        it.settle_reason = reason.strip()
        it.resolved_by = actor
        it.resolved_at = datetime.now(timezone.utc)
    else:
        it.resolved = False
        it.settle_reason = None
        it.resolved_by = None
        it.resolved_at = None
    db.flush()
    return {"id": it.id, "resolved": it.resolved, "settle_reason": it.settle_reason}


# ── #6 工厂账单挂已取消单 → 改挂同客户其它有效单 (用户 2026-06-24) ───────────────
# 异常 factory_bill_on_dead_order 已由 data_quality.scan_factory_bill_on_dead_order 报出;
# 这里提供「同客户候选 + 确定重新匹配」: 把该已取消单上的全部工厂账单行 order_no 改到选中的
# 有效订单, 并把工厂成本(actual_cost)从已取消单挪到目标单, 然后销该异常。
_DEAD_STATUSES = {"cancelled", "closed", "trade_closed", "refunded"}


def _is_dead_order(o: Order) -> bool:
    """与 scan_factory_bill_on_dead_order 同口径: 已取消 或 全额退款(退款≥实付×0.99 且实付>0)。"""
    if (o.status or "") == "cancelled":
        return True
    paid = Decimal(str(o.paid_amount or 0))
    refund = Decimal(str(o.refund_amount or 0))
    return paid > 0 and refund >= paid * Decimal("0.99")


def _zh_bigrams(s: str) -> set:
    """只保留中文字符后的 2-gram 集合 (剔除数字/标点噪声, 适配中文产品名匹配)。"""
    z = re.sub(r"[^一-鿿]", "", s or "")
    return {z[i:i + 2] for i in range(len(z) - 1)} if len(z) >= 2 else ({z} if z else set())


def _text_sim(a: str, b: str) -> float:
    """中文产品名相似度 = 2-gram Jaccard。比 difflib 更能凸显共有的关键词(如「箱体床」)。"""
    A, B = _zh_bigrams(a), _zh_bigrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def _bills_on(db: Session, order_no: str) -> list[FactoryReconItem]:
    return db.execute(
        select(FactoryReconItem).where(FactoryReconItem.order_no == order_no)
    ).scalars().all()


def _recompute_actual_cost(db: Session, o: Order) -> None:
    """按当前挂在该订单号上的工厂账单行 Σ结算价 重算 actual_cost(无则置空)。"""
    s = db.execute(
        select(func.coalesce(func.sum(FactoryReconItem.settle_price), 0))
        .where(FactoryReconItem.order_no == o.order_no)
    ).scalar() or Decimal("0")
    o.actual_cost = s if Decimal(str(s)) > 0 else None


def dead_order_rematch_candidates(db: Session, cancelled_order_no: str, *, limit: int = 5) -> dict:
    """工厂账单挂在已取消单时, 找【同客户】其它有效单作重新匹配候选,
    按 产品名↔账单详情 相似度(difflib) + 下单时间近 排序, 取前 limit 个。"""
    cancelled = db.execute(
        select(Order).where(Order.order_no == cancelled_order_no)
    ).scalar_one_or_none()
    bills = _bills_on(db, cancelled_order_no)
    bill_total = sum((b.settle_price or Decimal("0")) for b in bills)
    bill_detail = " ".join((b.detail or "") for b in bills).strip()
    out = {"cancelled_order_no": cancelled_order_no,
           "customer_name": (cancelled.customer_name if cancelled else None),
           "bill_total": float(bill_total), "bill_detail": bill_detail[:80],
           "candidates": [], "note": None}
    if cancelled is None:
        out["note"] = "已取消单不在系统里"
        return out
    cust = (cancelled.customer_name or "").strip()
    if not cust:
        out["note"] = "已取消单无客户姓名, 无法按同客户搜索 — 请人工指定目标订单号"
        return out
    others = db.execute(
        select(Order).where(Order.customer_name == cust,
                            Order.order_no != cancelled_order_no)
    ).scalars().all()
    scored = []
    for o in others:
        if _is_dead_order(o):
            continue
        prod = o.product_name or ""
        score = _text_sim(bill_detail, prod) if bill_detail and prod else 0.0
        scored.append((score, o.order_date or date.min, o))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out["candidates"] = [{
        "order_no": o.order_no, "status": o.status,
        "order_date": o.order_date.isoformat() if o.order_date else None,
        "product_name": o.product_name, "sku_code": o.sku_code,
        "paid_amount": float(o.paid_amount or 0),
        "has_actual_cost": o.actual_cost is not None,
        "match_pct": round(score * 100),
    } for score, _d, o in scored[:limit]]
    if not out["candidates"]:
        out["note"] = "同客户无其它有效单 — 请人工核实(可能工厂未真做或货款另算)"
    return out


def rematch_dead_order_bills(db: Session, cancelled_order_no: str, new_order_no: str,
                             *, actor: Optional[str] = None) -> dict:
    """把已取消单上的全部工厂账单行改挂到目标(同客户)有效单, 挪成本, 并销该异常。"""
    cancelled = db.execute(
        select(Order).where(Order.order_no == cancelled_order_no)).scalar_one_or_none()
    new = db.execute(
        select(Order).where(Order.order_no == new_order_no)).scalar_one_or_none()
    if cancelled is None:
        raise ValueError(f"已取消单不存在: {cancelled_order_no}")
    if new is None:
        raise ValueError(f"目标订单不存在: {new_order_no}")
    if new.order_no == cancelled.order_no:
        raise ValueError("目标订单不能是原单本身")
    if _is_dead_order(new):
        raise ValueError(f"目标订单 {new_order_no} 也是已取消/全额退款单, 不能挂载")
    c_cust = (cancelled.customer_name or "").strip()
    n_cust = (new.customer_name or "").strip()
    if c_cust and n_cust and c_cust != n_cust:
        raise ValueError(f"目标订单客户「{n_cust}」与原单客户「{c_cust}」不一致, 拒绝挂载")
    bills = _bills_on(db, cancelled_order_no)
    if not bills:
        raise ValueError(f"已取消单 {cancelled_order_no} 上没有工厂账单行")
    stamp = datetime.now(timezone.utc)
    note = (f"[重新匹配 {stamp.date()}] 原挂已取消单 {cancelled_order_no} → {new_order_no}"
            + (f" (by {actor})" if actor else ""))
    moved = Decimal("0")
    for b in bills:
        b.order_no = new_order_no
        b.remark = ((b.remark + " | ") if b.remark else "") + note
        b.remark = b.remark[:2000]
        moved += (b.settle_price or Decimal("0"))
    db.flush()   # 关键: autoflush=False, 必须先把 order_no 改动落库, 重算 Σ 才看得到新归属
    # 成本随账单走: 重算两单 actual_cost (原单挪空→None, 目标单=其名下账单 Σ)
    _recompute_actual_cost(db, cancelled)
    _recompute_actual_cost(db, new)
    # 销该已取消单对应的 factory_bill_on_dead_order 异常
    from app.models.exception import DataException
    excs = db.execute(
        select(DataException).where(
            DataException.exception_type == "factory_bill_on_dead_order",
            DataException.status == "open")
    ).scalars().all()
    closed = 0
    now_s = datetime.now().isoformat(timespec="seconds")
    for e in excs:
        ctx_no = (e.context or {}).get("order_no") if e.context else None
        if ctx_no == cancelled_order_no or str(e.source_pk) == str(cancelled.id):
            e.status = "resolved"
            e.resolved_by = actor or "系统(重新匹配)"
            e.resolved_at = now_s
            closed += 1
    db.flush()
    return {"moved_bills": len(bills), "moved_amount": float(moved),
            "cancelled_order_no": cancelled_order_no, "new_order_no": new_order_no,
            "new_actual_cost": float(new.actual_cost or 0), "closed_exceptions": closed}
