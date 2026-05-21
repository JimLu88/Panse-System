"""支付宝流水 ↔ 供应商送货单 自动对账 (业务需求 2).

输入: AlipayFlow 已被 smart_matching_service 标了 reconciliation_type='factory_payment'
输出: 每条流水尝试匹配到 1~N 张 DeliveryNote, 命中则:
        - flow.reconciliation_status = 'matched'
        - flow.related_order_no = note_no (单张) 或 多张以 ',' 拼接
        - notes[*].status = 'paid', paid_at = now, alipay_flow_no = flow.transaction_no

匹配策略 (按优先级):
    1) 精确单张匹配: 候选单据中找 amount == flow.amount (±0.01) 且未付
    2) 子集和合并付款: 同一供应商待付单据中, 找子集 sum == flow.amount (枚举上限 6 张)
    3) 多重合理解 → 标 needs_review, 不动数据
    4) 无候选 → 不动 (保持 open)

候选供应商判定:
    flow.counterparty 字符串 contains 任一 supplier.alipay_counterparty_keywords
    若 keywords 为空, 退到匹配 supplier.name 子串
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.supplier import DeliveryNote, Supplier


AMOUNT_TOL = Decimal("0.02")   # 金额浮动容差 (合并多张时存在四舍五入)
MAX_COMBO_NOTES = 6            # 一笔流水最多对应几张单据
DEFAULT_WINDOW_DAYS = 60       # 仅匹配此时间窗内的单据


@dataclass
class FlowMatch:
    flow_id: int
    flow_no: str
    flow_amount: Decimal
    flow_time: Optional[datetime]
    counterparty: Optional[str]
    supplier_id: Optional[int]
    supplier_name: Optional[str]
    matched_note_ids: list[int] = field(default_factory=list)
    matched_note_nos: list[str] = field(default_factory=list)
    decision: str = "skip"  # exact / combo / needs_review / no_supplier / no_candidates / skip
    reason: str = ""


@dataclass
class ReconcileResult:
    scanned: int
    matched_count: int       # decision in {exact, combo}
    needs_review: int
    no_supplier: int
    no_candidates: int
    skipped: int
    matches: list[FlowMatch]


# ----------------------------- 主入口 ---------------------------- #


def reconcile(
    db: Session,
    *,
    account: Optional[str] = None,
    since_days: int = 90,
    dry_run: bool = False,
) -> ReconcileResult:
    """业务需求 2: 一次性扫所有 factory_payment 且未对账的流水, 自动配单据."""
    since = datetime.now(timezone.utc) - timedelta(days=since_days)
    suppliers = list(db.execute(select(Supplier).where(Supplier.is_active.is_(True))).scalars())
    if not suppliers:
        return ReconcileResult(0, 0, 0, 0, 0, 0, [])

    flows_q = select(AlipayFlow).where(and_(
        AlipayFlow.reconciliation_type == "factory_payment",
        AlipayFlow.reconciliation_status == "open",
        AlipayFlow.transaction_time >= since,
    ))
    if account:
        flows_q = flows_q.where(AlipayFlow.account == account)
    flows = list(db.execute(flows_q).scalars())

    matches: list[FlowMatch] = []
    summary = {"exact": 0, "combo": 0, "needs_review": 0,
               "no_supplier": 0, "no_candidates": 0, "skipped": 0}

    for flow in flows:
        fm = _match_single_flow(db, flow, suppliers)
        matches.append(fm)
        summary[fm.decision] = summary.get(fm.decision, 0) + 1
        if not dry_run and fm.decision in ("exact", "combo"):
            _apply_match(db, flow, fm)

    if not dry_run:
        db.flush()

    return ReconcileResult(
        scanned=len(flows),
        matched_count=summary["exact"] + summary["combo"],
        needs_review=summary["needs_review"],
        no_supplier=summary["no_supplier"],
        no_candidates=summary["no_candidates"],
        skipped=summary["skipped"],
        matches=matches,
    )


# ----------------------------- 内部 ------------------------------ #


def _identify_supplier(flow: AlipayFlow, suppliers: list[Supplier]) -> Optional[Supplier]:
    """根据 counterparty 命中关键字 → 锁定一家供应商."""
    text = (flow.counterparty or "") + " " + (flow.counterparty_account or "")
    if not text.strip():
        return None
    text_lower = text.lower()
    best: Optional[Supplier] = None
    best_kw_len = 0
    for s in suppliers:
        keywords = (s.alipay_counterparty_keywords or []) or [s.name]
        for kw in keywords:
            if kw and kw.lower() in text_lower:
                # 选最长关键字命中 (更精确)
                if len(kw) > best_kw_len:
                    best = s
                    best_kw_len = len(kw)
    return best


def _candidate_notes(db: Session, supplier_id: int,
                     flow_time: Optional[datetime]) -> list[DeliveryNote]:
    """该供应商下未付清的单据 (status in pending_review/confirmed/billed)."""
    q = select(DeliveryNote).where(and_(
        DeliveryNote.supplier_id == supplier_id,
        DeliveryNote.status.in_(["pending_review", "confirmed", "billed"]),
        DeliveryNote.total_amount.isnot(None),
    ))
    # 时间窗收紧: 单据日期不晚于流水时间 + 7 天, 不早于 - WINDOW
    if flow_time is not None:
        start = (flow_time - timedelta(days=DEFAULT_WINDOW_DAYS)).date()
        end = (flow_time + timedelta(days=7)).date()
        q = q.where(and_(
            DeliveryNote.delivery_date >= start,
            DeliveryNote.delivery_date <= end,
        ))
    return list(db.execute(q.order_by(DeliveryNote.delivery_date)).scalars())


def _match_single_flow(
    db: Session, flow: AlipayFlow, suppliers: list[Supplier],
) -> FlowMatch:
    fm = FlowMatch(
        flow_id=flow.id, flow_no=flow.transaction_no,
        flow_amount=flow.amount, flow_time=flow.transaction_time,
        counterparty=flow.counterparty,
        supplier_id=None, supplier_name=None,
    )
    if flow.amount >= 0:
        fm.decision = "skipped"
        fm.reason = "支出流水才需要对账供应商单据 (amount>=0)"
        return fm

    abs_amount = -flow.amount  # 流水是负的

    supplier = _identify_supplier(flow, suppliers)
    if supplier is None:
        fm.decision = "no_supplier"
        fm.reason = f"counterparty {flow.counterparty!r} 未命中任何供应商关键字"
        return fm
    fm.supplier_id = supplier.id
    fm.supplier_name = supplier.name

    candidates = _candidate_notes(db, supplier.id, flow.transaction_time)
    if not candidates:
        fm.decision = "no_candidates"
        fm.reason = f"{supplier.name} 在时间窗内没有待付单据"
        return fm

    # 1) 精确单张
    exact_singles = [n for n in candidates if abs(n.total_amount - abs_amount) <= AMOUNT_TOL]
    if len(exact_singles) == 1:
        fm.decision = "exact"
        fm.matched_note_ids = [exact_singles[0].id]
        fm.matched_note_nos = [exact_singles[0].note_no or f"#{exact_singles[0].id}"]
        fm.reason = f"金额精确匹配 ¥{abs_amount}"
        return fm
    if len(exact_singles) > 1:
        # 多张同金额 → 让人工挑
        fm.decision = "needs_review"
        fm.matched_note_ids = [n.id for n in exact_singles]
        fm.matched_note_nos = [n.note_no or f"#{n.id}" for n in exact_singles]
        fm.reason = f"{len(exact_singles)} 张单据金额都等于 ¥{abs_amount}, 需手动选"
        return fm

    # 2) 子集和合并付款
    combos = _find_subset_sums(candidates, abs_amount)
    if len(combos) == 1:
        chosen = combos[0]
        fm.decision = "combo"
        fm.matched_note_ids = [n.id for n in chosen]
        fm.matched_note_nos = [n.note_no or f"#{n.id}" for n in chosen]
        fm.reason = f"{len(chosen)} 张单据合并 = ¥{abs_amount}"
        return fm
    if len(combos) > 1:
        fm.decision = "needs_review"
        unique_ids = sorted({n.id for combo in combos for n in combo})
        fm.matched_note_ids = unique_ids[:10]
        fm.matched_note_nos = [
            next(n.note_no or f"#{n.id}" for n in candidates if n.id == i)
            for i in unique_ids[:10]
        ]
        fm.reason = f"有 {len(combos)} 种合并方式都能凑成 ¥{abs_amount}, 需手动选"
        return fm

    fm.decision = "no_candidates"
    fm.reason = f"{supplier.name} 待付单据中没有 (或子集) 能凑成 ¥{abs_amount}"
    return fm


def _find_subset_sums(
    notes: list[DeliveryNote], target: Decimal,
) -> list[tuple[DeliveryNote, ...]]:
    """找出 notes 中所有合 == target 的子集 (2..MAX_COMBO_NOTES 张).

    notes 数量 > 12 时, 只尝试金额较接近 target 的前 12 张以避免组合爆炸。
    """
    if len(notes) > 12:
        notes = sorted(notes, key=lambda n: abs(n.total_amount - target))[:12]

    results: list[tuple[DeliveryNote, ...]] = []
    for k in range(2, min(MAX_COMBO_NOTES, len(notes)) + 1):
        for combo in itertools.combinations(notes, k):
            s = sum((n.total_amount for n in combo), Decimal("0"))
            if abs(s - target) <= AMOUNT_TOL:
                results.append(combo)
                # 找到 2 种就够判 needs_review 了, 避免过度枚举
                if len(results) >= 3:
                    return results
    return results


def _apply_match(db: Session, flow: AlipayFlow, fm: FlowMatch) -> None:
    """落盘: 流水 → matched, 单据 → paid + alipay_flow_no."""
    notes = list(db.execute(
        select(DeliveryNote).where(DeliveryNote.id.in_(fm.matched_note_ids))
    ).scalars())
    now = datetime.now(timezone.utc)
    for n in notes:
        n.status = "paid"
        n.paid_at = now
        n.alipay_flow_no = flow.transaction_no
        if n.reconciled_at is None:
            n.reconciled_at = now
    flow.reconciliation_status = "matched"
    flow.related_order_no = ",".join(fm.matched_note_nos)[:64]


# ----------------------------- 单条手动确认 ---------------------- #


def apply_manual_match(
    db: Session, *, flow_id: int, note_ids: list[int],
) -> FlowMatch:
    """业务需求: needs_review 场景, 用户选了 N 张单据手动确认."""
    flow = db.get(AlipayFlow, flow_id)
    if flow is None:
        raise ValueError(f"流水 {flow_id} 不存在")
    notes = list(db.execute(
        select(DeliveryNote).where(DeliveryNote.id.in_(note_ids))
    ).scalars())
    if not notes:
        raise ValueError("送货单列表为空")
    s = sum((n.total_amount or Decimal("0") for n in notes), Decimal("0"))
    if abs(s + flow.amount) > AMOUNT_TOL:  # flow.amount 是负的
        raise ValueError(
            f"金额对不上: 流水 ¥{-flow.amount} vs 单据合计 ¥{s}, 差 ¥{-flow.amount - s}"
        )
    fm = FlowMatch(
        flow_id=flow.id, flow_no=flow.transaction_no,
        flow_amount=flow.amount, flow_time=flow.transaction_time,
        counterparty=flow.counterparty,
        supplier_id=notes[0].supplier_id,
        supplier_name=db.get(Supplier, notes[0].supplier_id).name if notes[0].supplier_id else None,
        matched_note_ids=[n.id for n in notes],
        matched_note_nos=[n.note_no or f"#{n.id}" for n in notes],
        decision="combo" if len(notes) > 1 else "exact",
        reason="手动确认",
    )
    _apply_match(db, flow, fm)
    db.flush()
    return fm
