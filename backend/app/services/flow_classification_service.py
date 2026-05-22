"""未核销流水 AI 辅助归类 (Phase 11, P4-24).

业务: "未核销异常池" 里的流水, AI 看对方+金额+备注, 给出 "可能是 X 类型" 的猜测.
不自动入账, 只是辅助用户标注.

调用:
    classify_flow(db, flow_id) -> {kind, confidence, reason, suggested_actions}
    batch_classify(db, limit) -> [{flow_id, ...}]
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.services import settings_service
from app.services.ai_provider import AiUnavailable, build_provider

_logger = logging.getLogger("panse.flow_classify")


_SYSTEM_PROMPT = """你是畔色家具 ERP 财务流水分类助手。
看一条支付宝流水 (对方 / 金额 / 备注 / 类型), 判断它最可能是哪一类业务, 输出 JSON:
{
  "kind": "factory_payment | promotion_recharge | logistics | salary | refund | platform_fee | sample | personal | unknown",
  "confidence": 0.0-1.0,
  "reason": "判断依据 (50 字内)",
  "suggested_actions": ["建议手动核销到 X 单"]
}
规则:
- 工厂相关 (家具厂/木业/五金/物流厂) → factory_payment
- 推广 (淘宝/直通车/小红书/抖音/巨量) → promotion_recharge
- 顺丰/中通/京东/极兔/德邦/物流 → logistics
- 公司+人名 / 工资 → salary
- 支付宝服务费 / 平台扣款 → platform_fee
- 拿不准 → unknown + 低 confidence
仅输出 JSON, 不要解释."""


def classify_flow(db: Session, flow_id: int) -> dict:
    flow = db.get(AlipayFlow, flow_id)
    if flow is None:
        return {"kind": "unknown", "confidence": 0, "reason": "流水不存在",
                "suggested_actions": []}
    cfg = settings_service.get_ai_config(db, "diagnose")
    try:
        provider = build_provider(cfg)
    except AiUnavailable:
        # Fallback: 简单关键字规则
        return _rule_based_classify(flow)
    payload = {
        "transaction_no": flow.transaction_no,
        "amount": float(flow.amount or 0),
        "counterparty": flow.counterparty,
        "transaction_type": flow.transaction_type,
        "remark": flow.remark,
    }
    try:
        resp = provider.chat(
            system=_SYSTEM_PROMPT,
            user=json.dumps(payload, ensure_ascii=False),
            max_tokens=300,
        )
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.text.strip(), flags=re.M)
        data = json.loads(text)
    except (AiUnavailable, json.JSONDecodeError, ValueError) as e:
        _logger.warning("AI 流水分类失败 %s, 退化为规则: %s", flow_id, e)
        return _rule_based_classify(flow)
    return data


def _rule_based_classify(flow: AlipayFlow) -> dict:
    """无 AI 时的退化: 关键字匹配."""
    cp = (flow.counterparty or "") + " " + (flow.remark or "")
    cp_lower = cp.lower()

    rules = [
        (["工厂", "木业", "五金", "板厂", "木作"], "factory_payment"),
        (["顺丰", "中通", "京东物流", "极兔", "德邦", "运费"], "logistics"),
        (["直通车", "万相台", "巨量", "推广", "小红书", "抖音"], "promotion_recharge"),
        (["工资", "薪资", "薪酬", "奖金"], "salary"),
        (["平台服务费", "扣款", "佣金"], "platform_fee"),
        (["样品", "打样"], "sample"),
        (["退款", "退货"], "refund"),
    ]
    for keywords, kind in rules:
        if any(k in cp for k in keywords):
            return {
                "kind": kind, "confidence": 0.7,
                "reason": f"规则匹配关键字: {[k for k in keywords if k in cp][0]}",
                "suggested_actions": [],
            }
    return {
        "kind": "unknown", "confidence": 0.2,
        "reason": "对方/备注没识别到关键字, 建议人工核销",
        "suggested_actions": ["手动归类"],
    }


def batch_classify(db: Session, *, days: int = 7, limit: int = 50) -> list[dict]:
    """批量给最近 N 天的 open 流水分类."""
    from datetime import date, timedelta
    cutoff = date.today() - timedelta(days=days)
    rows = db.execute(
        select(AlipayFlow).where(
            AlipayFlow.reconciliation_status == "open",
            AlipayFlow.transaction_time >= cutoff,
        ).limit(limit)
    ).scalars().all()
    out = []
    for f in rows:
        result = classify_flow(db, f.id)
        result["flow_id"] = f.id
        result["amount"] = float(f.amount or 0)
        result["counterparty"] = f.counterparty
        out.append(result)
    return out
