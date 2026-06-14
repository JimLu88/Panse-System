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
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow

_logger = logging.getLogger("panse.flow_refund")

_CENTS = Decimal("0.01")
_MAX_TIME = datetime.max  # 缺失 transaction_time 的流水排序时垫底


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
        # 评审财务#3: 等额盲配加两道护栏(不丢现有正确对):
        #   1) 按时间排序, 配对结果稳定(不再依赖数据库行序的偶然顺序)
        #   2) 两侧都有时间戳时, 退款(支出)必须发生在付款(收入)当天或之后; 缺时间则放行(回退原行为)
        def _t(f):  # 缺失时间排最后, 不参与排序比较时视为最大
            return f.transaction_time or _MAX_TIME
        incomes = sorted([f for f in group if (f.amount or 0) > 0], key=_t)
        expenses = sorted([f for f in group if (f.amount or 0) < 0], key=_t)
        used_income_ids: set[int] = set()
        used_expense_ids: set[int] = set()

        for inc in incomes:
            inc_abs = abs(inc.amount or Decimal("0")).quantize(_CENTS)
            for exp in expenses:
                if exp.id in used_expense_ids:
                    continue
                # 退款必须不早于付款 (两侧时间都在时才校验; 任一缺失则回退原等额规则)
                if inc.transaction_time and exp.transaction_time \
                        and exp.transaction_time < inc.transaction_time:
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
