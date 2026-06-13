"""经营支出「自动配流水」工具 (用户拍板 2026-06-11 闭环审核建议).

给缺 alipay_flow_no 的 日常经营 / 人员外包 / 品牌营销 记录, 按
|金额| 相等 + 日期窗口 在支付宝支出流水里找配对, 回填流水号。

与 alipay_flow_router_service 的区别 (那边是「归类流水」一键全跑的一环):
  - 只在窗口内 **唯一命中** 才回填; 同金额多笔候选 → 列为待人工, 绝不猜最近的
  - 覆盖品牌营销 (router 没做)
  - 回填记 field_changes 留痕, 修改档案可回溯
  - 返回 配上/多候选/没找到 三类计数 + 明细, 前端可直接展示

幂等: 只填空 alipay_flow_no; 已被任何业务表引用的流水不再当候选。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.marketing import (
    AfterSales,
    BrandMarketing,
    DailyOperation,
    OutsourcingExpense,
    PromotionFlow,
)
from app.models.order import PartPurchase
from app.services import field_change_service

# 日期窗口缺省 ±N 天; system_settings.expense_match_window_days 可覆盖
DEFAULT_WINDOW_DAYS = 10


@dataclass
class ExpenseMatchResult:
    matched: dict[str, int] = field(default_factory=dict)    # 表名 → 配上条数
    ambiguous: int = 0                                       # 多候选, 留人工
    unmatched: int = 0                                       # 窗口内没找到
    details: list[str] = field(default_factory=list)         # 前 50 条人话明细


def _window_days(db: Session) -> int:
    try:
        from app.services import settings_service
        raw = settings_service.get(db, "expense_match_window_days", env_fallback=False)
        if raw:
            return max(1, int(str(raw).strip()))
    except Exception:  # pragma: no cover - 配置读取失败用缺省
        pass
    return DEFAULT_WINDOW_DAYS


def _q2(v) -> Decimal:
    return Decimal(str(abs(v))).quantize(Decimal("0.01"))


def _referenced_flow_nos(db: Session) -> set[str]:
    """已被任何业务表引用的流水号 — 不再当候选, 防一条流水配两笔支出。"""
    out: set[str] = set()
    for model in (PartPurchase, PromotionFlow, OutsourcingExpense, DailyOperation,
                  AfterSales, BrandMarketing):
        for no in db.execute(select(model.alipay_flow_no)).scalars().all():
            if no:
                out.add(no)
    return out


def _expense_flow_index(db: Session, used: set[str]) -> dict[Decimal, list[AlipayFlow]]:
    """支出流水 (amount<0) 按 |金额| 索引, 剔除已引用/期初调整。"""
    idx: dict[Decimal, list[AlipayFlow]] = {}
    flows = db.execute(
        select(AlipayFlow).where(
            AlipayFlow.amount < 0,
            AlipayFlow.reconciliation_status != "opening_balance",
        )
    ).scalars().all()
    for f in flows:
        if not f.transaction_no or f.transaction_no in used:
            continue
        idx.setdefault(_q2(f.amount), []).append(f)
    return idx


def _candidates(idx, used: set[str], amount, when: Optional[date], window: int) -> list[AlipayFlow]:
    if amount is None or Decimal(str(amount)) <= 0:
        return []
    out = []
    for f in idx.get(_q2(amount), []):
        if f.transaction_no in used:
            continue
        if when and f.transaction_time:
            if abs((f.transaction_time.date() - when).days) > window:
                continue
        out.append(f)
    return out


def match_expense_flows(db: Session, *, actor: Optional[str] = None) -> ExpenseMatchResult:
    window = _window_days(db)
    used = _referenced_flow_nos(db)
    idx = _expense_flow_index(db, used)
    res = ExpenseMatchResult(matched={"日常经营": 0, "人员外包": 0, "品牌营销": 0})

    # (表名, 记录列表, 取金额, 取日期, 行标签)
    sources = [
        ("日常经营",
         db.execute(select(DailyOperation).where(
             DailyOperation.alipay_flow_no.is_(None))).scalars().all(),
         lambda r: r.amount, lambda r: r.record_date,
         lambda r: f"日常#{r.id} {r.item or ''}".strip()),
        ("人员外包",
         db.execute(select(OutsourcingExpense).where(
             OutsourcingExpense.alipay_flow_no.is_(None))).scalars().all(),
         lambda r: r.amount, lambda r: r.payment_date,
         lambda r: f"外包#{r.id} {r.payee or ''}".strip()),
        ("品牌营销",
         db.execute(select(BrandMarketing).where(
             BrandMarketing.alipay_flow_no.is_(None))).scalars().all(),
         lambda r: r.actual_spend, lambda r: r.payment_date,
         lambda r: f"品牌#{r.id} {r.project_name or ''}".strip()),
    ]
    table_names = {"日常经营": "daily_operations", "人员外包": "outsourcing_expenses",
                   "品牌营销": "brand_marketing"}

    for src, rows, get_amount, get_date, get_label in sources:
        for r in sorted(rows, key=lambda x: (get_date(x) or date.min, x.id)):
            amount, when = get_amount(r), get_date(r)
            if amount is None or Decimal(str(amount)) <= 0:
                continue
            cands = _candidates(idx, used, amount, when, window)
            label = get_label(r)
            if len(cands) == 1:
                f = cands[0]
                r.alipay_flow_no = f.transaction_no
                used.add(f.transaction_no)
                res.matched[src] += 1
                field_change_service.record(
                    db, table=table_names[src], pk=r.id, field="alipay_flow_no",
                    old=None, new=f.transaction_no, actor=actor or "自动配流水",
                    source="web", row_label=label, field_label="支付宝流水号",
                )
                if len(res.details) < 50:
                    when_s = f.transaction_time.date().isoformat() if f.transaction_time else "?"
                    res.details.append(
                        f"[配上] {label} ¥{_q2(amount)} → 流水 {f.transaction_no} ({when_s})")
            elif len(cands) > 1:
                res.ambiguous += 1
                if len(res.details) < 50:
                    res.details.append(
                        f"[多候选] {label} ¥{_q2(amount)} 同金额流水 {len(cands)} 笔, 请人工指认")
            else:
                res.unmatched += 1
    db.flush()
    return res
