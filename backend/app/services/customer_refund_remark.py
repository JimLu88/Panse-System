"""Deterministic customer refund remarks; never infer a refund from an amount alone."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata

from app.services.order_no_normalizer import resolve_platform_order_no

REFUND_WORDS = ("差价", "退款", "退货", "退钱", "退回货款", "退还货款", "价格保护", "价保")
AFTERSALES_WORDS = ("售后", "赔付", "补偿", "赔偿")
ORDER_NUMBER = re.compile(r"(?<![0-9])[0-9]{18,19}(?![0-9])")
MONEY = re.compile(r"(?<![0-9.])([0-9]{1,9}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*元|[¥￥]\s*([0-9]+(?:\.[0-9]{1,2})?)")


def expense_category(flow):
    if Decimal(str(flow.amount or 0)) >= 0:
        return None
    text = unicodedata.normalize('NFKC', ' '.join(filter(None, [flow.remark, flow.transaction_type])))
    # Negative/planned instructions are not evidence of a paid customer refund.
    if re.search(r"(?:不|无需|取消|未)(?:退差价|退款|补偿|赔付|赔偿|退钱)", text):
        return None
    if any(word in text for word in REFUND_WORDS):
        return 'refund'
    if any(word in text for word in AFTERSALES_WORDS):
        return 'aftersales'
    return None


@dataclass(frozen=True)
class Resolution:
    category: str | None
    order_no: str | None = None
    issue: str | None = None


def resolve(flow):
    if re.search(r"(?:不|无需|取消|未)(?:退差价|退款|补偿|赔付|赔偿|退钱)", flow.remark or ''):
        return Resolution(None, issue='备注是否实际退款不明确，待核对')
    category = expense_category(flow)
    if not category:
        return Resolution(None)
    remark = unicodedata.normalize('NFKC', flow.remark or '')
    candidates = set(ORDER_NUMBER.findall(remark))
    candidates.update(ORDER_NUMBER.findall(flow.platform_order_no or ''))
    for raw in (flow.platform_order_no, flow.related_order_no):
        known = resolve_platform_order_no(raw)
        if known:
            candidates.add(known)
    if len(candidates) > 1:
        return Resolution(category, issue='多个或冲突订单号，待核对')
    amounts = {Decimal((a or b).replace(',', '')) for a, b in MONEY.findall(remark)}
    if amounts and amounts != {abs(Decimal(str(flow.amount)))}:
        return Resolution(category, issue='备注金额与支付宝实际流水不一致，待核对')
    if not candidates:
        return Resolution(category, issue='未找到唯一完整订单号，待核对')
    return Resolution(category, next(iter(candidates)))


def fill_order(flow):
    """Only fill the derived platform column; retain raw merchant reference and remark."""
    result = resolve(flow)
    if result.order_no and not (flow.platform_order_no or '').strip():
        flow.platform_order_no = result.order_no
    return result
