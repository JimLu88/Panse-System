"""支付宝流水智能核销 (plan §12.4 + 对账优化①)。

CSV 导入后 / 手动触发时跑一遍, 给 reconciliation_type 打标签。

三段匹配 (可靠度从高到低):
  1. 关联订单号: flow.related_order_no 命中
       - 收入 + 命中订单总表 order_no                 → customer_payment
       - 支出 + 命中工厂下单表 platform_order_no       → factory_payment
  2. 数据驱动对手方: 支出 + counterparty 命中库内真实工厂名 → factory_payment
  3. 关键字回退: 对手方/备注命中推广/物流/工资等关键字。

已有标的不动 (多半人工或上游标过)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.order import FactoryOrder, Order

_DIGITS = re.compile(r"\d{12,}")


def _order_key(raw: Optional[str]) -> Optional[str]:
    """从关联订单号里抽出可比对的订单号核心。

    支付宝里常带前缀和空格, 如 'T200P2701846635029001 070' → '2701846635029001070'。
    取去空格后最长的数字串 (>=12 位) 作为订单号。
    """
    if not raw:
        return None
    compact = re.sub(r"\s+", "", raw)
    runs = _DIGITS.findall(compact)
    if not runs:
        return None
    return max(runs, key=len)


PROMOTION_KEYS = ("推广", "淘宝商业", "现金消耗", "直通车", "万相台", "钻展")
LOGISTICS_KEYS = ("万师傅", "顺丰", "京东物流", "德邦", "圆通", "中通", "韵达", "申通")
SALARY_KEYS = ("工资", "薪资", "外包")
# 工厂名兜底关键字 (库里没工厂名时仍能粗匹配)
FACTORY_KEYS = ("家具", "工厂", "木业", "木器", "家居")

# ── 段 0: 用户写死规则 (2026-06-11 拍板, 永不再误归成采购/不再报异常) ──
# 理财申购/单次转入 = 支付宝余额⇄余额宝的账户内转移, 不是经营支出。
INTERNAL_TRANSFER_KEYS = (
    "理财申购", "理财赎回", "余额宝", "余利宝", "单次转入", "单次转出",
)
# 消费者体验提升计划服务费 = 淘宝官方按单代扣的平台服务费 (退货宝等, 按店铺类目/
# 规模/售后情况定价), 对手方多为「上海淘天商业管理有限公司」→ 平台费/经营。
PLATFORM_FEE_KEYS = (
    "消费者体验提升计划", "淘天商业", "平台服务费", "天猫服务费",
)
# 消费者保证金充值 → 平台保证金 (资产, 非费用)
PLATFORM_DEPOSIT_KEYS = ("消费者保证金", "保证金充值")


def _matches(text: Optional[str], keys: tuple[str, ...]) -> bool:
    if not text:
        return False
    return any(k in text for k in keys)


@dataclass
class _Lookups:
    order_nos: set[str] = field(default_factory=set)
    factory_platform_nos: set[str] = field(default_factory=set)
    factory_names: tuple[str, ...] = ()


def _build_lookups(db: Session) -> _Lookups:
    order_nos = {
        k for (o,) in db.execute(select(Order.order_no).where(Order.order_no.isnot(None))).all()
        if (k := _order_key(o))
    }
    fac_nos = {
        k for (n,) in db.execute(
            select(FactoryOrder.platform_order_no).where(FactoryOrder.platform_order_no.isnot(None))
        ).all()
        if (k := _order_key(n))
    }
    fac_names = tuple(sorted({
        n.strip() for (n,) in db.execute(
            select(FactoryOrder.factory_name).where(FactoryOrder.factory_name.isnot(None))
        ).all() if n and n.strip()
    }, key=len, reverse=True))  # 长名优先, 避免短名误命中
    return _Lookups(order_nos=order_nos, factory_platform_nos=fac_nos, factory_names=fac_names)


@dataclass
class MatchResult:
    total_scanned: int
    tagged: dict[str, int]   # category -> count
    untouched: int


def _classify(flow: AlipayFlow, lk: _Lookups) -> Optional[str]:
    if flow.reconciliation_type:  # 已经有标了, 不动
        return None
    amt = float(flow.amount or 0)
    has_ron = bool((flow.related_order_no or "").strip())
    ron = _order_key(flow.related_order_no)
    desc = " ".join(filter(None, [flow.counterparty, flow.remark, flow.transaction_type]))

    # 段 0: 用户写死的确定性规则, 优先于一切 (理财转移/平台代扣费/保证金)
    if _matches(desc, INTERNAL_TRANSFER_KEYS):
        return "internal_transfer"
    if _matches(desc, PLATFORM_DEPOSIT_KEYS):
        return "platform_deposit"
    if _matches(desc, PLATFORM_FEE_KEYS):
        return "platform_fee"

    # 段 1: 关联订单号直挂 (归一化后比对真实订单/工厂下单)
    if ron:
        if amt > 0 and ron in lk.order_nos:
            return "customer_payment"
        if amt < 0 and ron in lk.factory_platform_nos:
            return "factory_payment"

    if amt < 0:
        # 段 2: 数据驱动 — 对手方命中库内真实工厂名
        cp = (flow.counterparty or "").strip()
        if cp and any(name and (name in cp or cp in name) for name in lk.factory_names):
            return "factory_payment"
        # 段 3: 关键字回退
        if _matches(desc, FACTORY_KEYS):
            return "factory_payment"
        if _matches(desc, PROMOTION_KEYS):
            return "promotion"
        if _matches(desc, LOGISTICS_KEYS):
            return "logistics"
        if _matches(desc, SALARY_KEYS):
            return "salary"
    elif amt > 0 and has_ron:
        # 收入且带关联订单号 → 客户回款 (兜底, 即使订单未导入)
        return "customer_payment"
    return None


def run(db: Session, *, account: Optional[str] = None) -> MatchResult:
    """扫所有 reconciliation_type 为空的流水, 自动打标."""
    lk = _build_lookups(db)
    stmt = select(AlipayFlow).where(AlipayFlow.reconciliation_type.is_(None))
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    rows = db.execute(stmt).scalars().all()
    tagged: dict[str, int] = {}
    for r in rows:
        category = _classify(r, lk)
        if category:
            r.reconciliation_type = category
            tagged[category] = tagged.get(category, 0) + 1
    db.flush()
    return MatchResult(
        total_scanned=len(rows),
        tagged=tagged,
        untouched=len(rows) - sum(tagged.values()),
    )
