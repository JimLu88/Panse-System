"""询价/拍下：Jim 介入前先发可配置自动回复（实施计划 P3）。"""

from __future__ import annotations

from apps.core.ai.input_quality_gate import load_inquiry_templates
from apps.core.intent.classify import IntentSignals


def resolve_inquiry_auto_reply(intent: IntentSignals) -> str | None:
    price_tpl, order_tpl = load_inquiry_templates()
    if intent.order_placed:
        return order_tpl
    if intent.price_quote:
        return price_tpl
    return None
