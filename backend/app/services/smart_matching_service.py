"""支付宝流水智能核销 (plan §12.4)。

CSV 导入后自动跑一遍，根据流水的对手方 / 备注 / 关联订单号尝试给
reconciliation_type 打标签。
匹配规则:
  - amount < 0 + 对手方含 ‘家具/工厂’ 关键字          → factory_payment
  - amount < 0 + 备注/对手方含 ‘推广/淘宝/直通车/万相台’ → promotion
  - amount < 0 + 对手方含 ‘万师傅/物流/快递’           → logistics
  - amount > 0 + 有 related_order_no                  → customer_payment
  - 备注含 ‘工资/薪资’                                 → salary
其它保持原状（多半已经被人工或上游标过了）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow


FACTORY_KEYS = ("家具", "工厂", "博冠", "木业")
PROMOTION_KEYS = ("推广", "淘宝商业", "现金消耗", "直通车", "万相台", "钻展")
LOGISTICS_KEYS = ("万师傅", "顺丰", "京东物流", "德邦", "圆通", "中通", "韵达", "申通")
SALARY_KEYS = ("工资", "薪资", "外包")


def _matches(text: Optional[str], keys: tuple[str, ...]) -> bool:
    if not text:
        return False
    return any(k in text for k in keys)


@dataclass
class MatchResult:
    total_scanned: int
    tagged: dict[str, int]   # category -> count
    untouched: int


def _classify(flow: AlipayFlow) -> Optional[str]:
    if flow.reconciliation_type:  # 已经有标了，不动
        return None
    desc = " ".join(filter(None, [flow.counterparty, flow.remark, flow.transaction_type]))
    amt = float(flow.amount or 0)

    if amt < 0:
        if _matches(desc, FACTORY_KEYS):
            return "factory_payment"
        if _matches(desc, PROMOTION_KEYS):
            return "promotion"
        if _matches(desc, LOGISTICS_KEYS):
            return "logistics"
        if _matches(desc, SALARY_KEYS):
            return "salary"
    elif amt > 0:
        if flow.related_order_no:
            return "customer_payment"
    return None


def run(db: Session, *, account: Optional[str] = None) -> MatchResult:
    """扫所有 reconciliation_type 为空的流水, 自动打标."""
    stmt = select(AlipayFlow).where(AlipayFlow.reconciliation_type.is_(None))
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    rows = db.execute(stmt).scalars().all()
    tagged: dict[str, int] = {}
    for r in rows:
        category = _classify(r)
        if category:
            r.reconciliation_type = category
            tagged[category] = tagged.get(category, 0) + 1
    db.flush()
    return MatchResult(
        total_scanned=len(rows),
        tagged=tagged,
        untouched=len(rows) - sum(tagged.values()),
    )
