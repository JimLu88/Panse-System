"""异常自动诊断 (Phase 10, 业务需求 8.4).

业务: 异常面板出现一个新异常 → AI 自动:
    1. 拉相关上下文 (订单 / 库存 / 流水)
    2. 给出 "最可能原因 + 建议处置" 文本
    3. (可选) 给一键修复 action

公开:
    diagnose(db, exception_id) -> {analysis, suggested_actions}
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.exception import DataException
from app.services import settings_service
from app.services.ai_provider import AiUnavailable, build_provider

_logger = logging.getLogger("panse.exception_diagnosis")


_SYSTEM_PROMPT = """你是畔色 ERP 异常诊断助手。
给你一条 DataException 记录 + 相关上下文 JSON, 输出 JSON:
{
  "analysis": "最可能原因 (100 字内)",
  "suggested_actions": [
    {"label": "动作名", "kind": "manual_fix" | "view" | "run_scanner", "url": "..."}
  ],
  "severity_recommended": "info" | "warning" | "error"
}
不要造数据, 只基于给你的上下文判断."""


def _gather_context(db: Session, exc: DataException) -> dict:
    """根据 exception.source_table 拉相关行作为上下文."""
    ctx = {
        "exception_type": exc.exception_type,
        "source_table": exc.source_table,
        "source_pk": exc.source_pk,
        "current_severity": exc.severity,
        "description": exc.description,
        "context": exc.context,
    }
    if exc.source_table == "orders" and exc.source_pk:
        from app.models.order import Order
        from sqlalchemy import select
        o = db.execute(select(Order).where(Order.order_no == exc.source_pk)).scalar_one_or_none()
        if o:
            ctx["order"] = {
                "id": o.id, "status": o.status, "qty": o.qty,
                "paid_amount": float(o.paid_amount or 0),
                "is_historical": o.is_historical,
            }
    return ctx


def diagnose(db: Session, exception_id: int) -> dict:
    exc = db.get(DataException, exception_id)
    if exc is None:
        raise ValueError(f"exception {exception_id} 不存在")
    cfg = settings_service.get_ai_config(db, "diagnose")
    try:
        provider = build_provider(cfg)
    except AiUnavailable as e:
        return {
            "analysis": f"AI 未配置, 跳过诊断 ({e}).",
            "suggested_actions": [],
            "severity_recommended": exc.severity,
        }
    ctx = _gather_context(db, exc)
    try:
        resp = provider.chat(
            system=_SYSTEM_PROMPT,
            user=json.dumps(ctx, ensure_ascii=False),
            max_tokens=600,
        )
    except AiUnavailable as e:
        return {
            "analysis": f"AI 调用失败: {e}",
            "suggested_actions": [],
            "severity_recommended": exc.severity,
        }
    # 容错: AI 不返 JSON 时 fallback
    try:
        import re
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.text.strip(), flags=re.M)
        data = json.loads(text)
    except Exception:
        return {
            "analysis": resp.text[:500],
            "suggested_actions": [],
            "severity_recommended": exc.severity,
        }
    return data
