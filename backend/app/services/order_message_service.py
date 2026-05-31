"""粘贴消息转订单变更 (Feature 8).

功能:
- parse_change: 从自然语言文本提取订单变更信息 (AI 优先, 正则兜底)
- apply_change: 将变更应用到指定订单并记录事件
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.services import ai_assistant, order_event_service
from app.services.ai_assistant import AiUnavailable

_logger = logging.getLogger("panse.order_message")

# 允许修改的安全字段
_SAFE_FIELDS = {
    "product_code", "product_name", "sku", "qty", "ship_date",
    "carrier", "tracking_no", "remark", "customer_name",
    "customer_address", "paid_amount",
}

_PARSE_SYSTEM_EXTRA = """你还需要处理"粘贴消息转订单变更"的场景。
当用户提供一段操作消息时，请提取以下信息并以 JSON 格式返回（不要有其他文字）：
{
  "order_no": "订单号（12-20位数字，或中文订单号）",
  "changes": {"字段名": "新值", ...},
  "confidence": 0.0-1.0,
  "raw_text": "原始文本"
}
可识别的字段名（使用英文键名）：
- product_code: 产品编码
- product_name: 产品名称
- sku: SKU规格
- qty: 数量（整数）
- ship_date: 发货日期（YYYY-MM-DD格式）
- carrier: 物流公司
- tracking_no: 快递单号
- remark: 备注
- customer_name: 客户姓名
- customer_address: 客户地址
- paid_amount: 实付金额（数字）

如果信息不足, 返回 confidence < 0.5 并在对应字段留空。"""


def parse_change(db: Session, text: str) -> dict:
    """从自然语言文本中提取订单变更信息.

    优先用 AI 解析; AI 不可用时 regex 兜底提取订单号.

    返回:
        {order_no, changes, confidence, raw_text, ai_available}
    """
    user_msg = f"请解析以下操作消息，提取订单变更信息，只返回 JSON：\n\n{text}"

    try:
        ai = ai_assistant._call_ai(
            db, kind="diagnose",
            user_message=user_msg,
            extra_system=_PARSE_SYSTEM_EXTRA,
            max_tokens=600,
        )
        # 尝试解析 JSON
        # AI 可能在 JSON 前后有其他文本, 提取 {...}
        match = re.search(r'\{.*\}', ai.text, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            return {
                "order_no": parsed.get("order_no"),
                "changes": parsed.get("changes", {}),
                "confidence": parsed.get("confidence", 0.5),
                "raw_text": text,
                "ai_available": True,
            }
        # JSON 解析失败, 做简单提取
        _logger.warning("AI 返回内容无法解析为 JSON: %s", ai.text[:200])
        return {
            "order_no": _extract_order_no(text),
            "changes": {},
            "confidence": 0.3,
            "raw_text": text,
            "ai_available": True,
        }
    except Exception as e:
        _logger.info("AI 不可用或解析失败, 使用 regex 兜底: %s", e)
        return {
            "order_no": _extract_order_no(text),
            "changes": {},
            "confidence": 0.2,
            "raw_text": text,
            "ai_available": False,
        }

    # unreachable but satisfies linter
    return {
        "order_no": _extract_order_no(text),
            "changes": {},
            "confidence": 0.1,
            "raw_text": text,
            "ai_available": False,
        }


def _extract_order_no(text: str) -> Optional[str]:
    """正则兜底: 找12-20位连续数字 (淘宝/拼多多等主流平台订单号格式)。"""
    # 不用 \b 因为中文字符不是 word boundary
    m = re.search(r'(?<!\d)(\d{12,20})(?!\d)', text)
    if m:
        return m.group(1)
    return None


def apply_change(
    db: Session,
    order_id: int,
    changes: dict,
    actor: str = "operator",
) -> Order:
    """将 changes 字典应用到订单, 记录事件, 返回更新后的 Order.

    只允许修改 _SAFE_FIELDS 中定义的字段.
    注意: 调用方负责 db.commit().
    """
    o = db.get(Order, order_id)
    if o is None:
        raise ValueError(f"Order {order_id} not found")

    applied: dict = {}
    skipped: list[str] = []

    for field_name, new_value in changes.items():
        if field_name not in _SAFE_FIELDS:
            skipped.append(field_name)
            _logger.warning("跳过不允许修改的字段: %s", field_name)
            continue

        old_value = getattr(o, field_name, None)

        # 类型转换
        if field_name == "qty" and new_value is not None:
            try:
                new_value = int(new_value)
            except (ValueError, TypeError):
                skipped.append(field_name)
                continue
        elif field_name == "paid_amount" and new_value is not None:
            try:
                from decimal import Decimal
                new_value = Decimal(str(new_value))
            except Exception:
                skipped.append(field_name)
                continue
        elif field_name == "ship_date" and new_value is not None:
            try:
                from datetime import date
                if isinstance(new_value, str):
                    new_value = date.fromisoformat(new_value)
            except Exception:
                skipped.append(field_name)
                continue

        setattr(o, field_name, new_value)
        applied[field_name] = {"old": str(old_value), "new": str(new_value)}

    if applied:
        summary_parts = [f"{k}: {v['old']} → {v['new']}" for k, v in applied.items()]
        order_event_service.record(
            db,
            order_id=order_id,
            kind="field_change",
            summary=f"消息变更: {'; '.join(summary_parts)}",
            actor=actor,
            context={"applied": applied, "skipped": skipped},
        )

    db.flush()
    return o
