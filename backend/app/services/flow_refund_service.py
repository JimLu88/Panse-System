"""退款对识别 — 把「一收一支、等额同关联订单号」的流水对标记为退款, 而非重复。

规则:
  同一 related_order_no (或对手方+金额) 下出现方向相反、金额相等的两条流水
  → 收入侧标 reconciliation_type="refund_in"
  → 支出侧标 reconciliation_type="refund_out"
  两条都标 reconciliation_status="matched"

不删数据、不合并流水, 只打标。打标后:
  - 支出侧被 create_aftersales_from_flows 识别为售后流水 (route: refund_out 含 "refund")
  - data_quality_service 把 refund_in/refund_out 对排除出「重复流水」告警
"""
from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow

_logger = logging.getLogger("panse.flow_refund")

_CENTS = Decimal("0.01")


def detect_refunds(db: Session) -> int:
    """扫描支付宝流水, 识别退款对并打标。返回识别到的退款对数量。

    退款对判定条件 (同时满足):
      1. 同一 related_order_no (非空)
      2. 两条流水金额绝对值相等, 且方向相反 (一正一负)
      3. 双方均未被标记为 refund_in/refund_out
    """
    flows = db.execute(select(AlipayFlow)).scalars().all()

    # 按 related_order_no 分组
    by_order: dict[str, list[AlipayFlow]] = {}
    for f in flows:
        if not f.related_order_no:
            continue
        if (f.reconciliation_type or "").startswith("refund_"):
            continue  # 已处理
        by_order.setdefault(f.related_order_no, []).append(f)

    pairs_found = 0
    for order_no, group in by_order.items():
        incomes = [f for f in group if (f.amount or 0) > 0]
        expenses = [f for f in group if (f.amount or 0) < 0]
        used_income_ids: set[int] = set()
        used_expense_ids: set[int] = set()

        for inc in incomes:
            inc_abs = abs(inc.amount or Decimal("0")).quantize(_CENTS)
            for exp in expenses:
                if exp.id in used_expense_ids:
                    continue
                exp_abs = abs(exp.amount or Decimal("0")).quantize(_CENTS)
                if inc_abs == exp_abs and inc_abs > 0:
                    # 配对成功
                    inc.reconciliation_type = "refund_in"
                    inc.reconciliation_status = "matched"
                    exp.reconciliation_type = "refund_out"
                    exp.reconciliation_status = "matched"
                    used_income_ids.add(inc.id)
                    used_expense_ids.add(exp.id)
                    pairs_found += 1
                    break

    db.flush()
    _logger.info("退款对识别: 发现 %d 对退款流水", pairs_found)
    return pairs_found
