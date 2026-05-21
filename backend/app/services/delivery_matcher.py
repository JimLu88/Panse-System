"""送货单行 → 订单 匹配 (业务需求: 模糊 + AI 兜底, 给百分比 + top-3).

输入: 一行 OCR 出来的明细 (item_name + spec + qty + amount + supplier 类型)
输出: 候选订单列表, 每个 {order_no, factory_order_no?, confidence, reason}
        confidence 0-100, 100 = 精确匹配 (单号或编码完整命中)

策略:
1. 数据源: FactoryOrder (面向供应商的工厂订单), 30 天滑动窗口默认
2. 主匹配 token-based fuzzy: SKU / product_code / customer_name 名字命中
3. 若头部候选 < 70 分 → 调 AI 做 tiebreaker (低优先级, 仅低置信场景用)
4. 返回 top_n 排序结果
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.order import FactoryOrder, Order
from app.services import settings_service
from app.services.ai_provider import AiUnavailable, build_provider
from app.services.match_service import _token_score


@dataclass
class MatchCandidate:
    order_no: str
    factory_order_no: Optional[str]
    confidence: Decimal  # 0-100
    method: str          # "exact" / "fuzzy" / "ai"
    reason: str
    customer_name: Optional[str] = None
    product_code: Optional[str] = None
    sku: Optional[str] = None
    qty: Optional[int] = None

    def to_json(self) -> dict:
        return {
            "order_no": self.order_no,
            "factory_order_no": self.factory_order_no,
            "confidence": float(self.confidence),
            "method": self.method,
            "reason": self.reason,
            "customer_name": self.customer_name,
            "product_code": self.product_code,
            "sku": self.sku,
            "qty": self.qty,
        }


# ----------------------------- 主入口 ---------------------------------- #


def match_line(
    db: Session,
    *,
    item_name: str,
    spec: str,
    qty: Decimal,
    delivery_date: Optional[date] = None,
    window_days: int = 30,
    top_n: int = 3,
    enable_ai_tiebreaker: bool = True,
) -> list[MatchCandidate]:
    """给一行送货单, 返回 top_n 个候选订单, 按置信度倒序."""
    haystack = f"{item_name} {spec}".strip().lower()
    if not haystack:
        return []

    # 1) 取候选: delivery_date 前后 window_days 内 + 不限 (兜底)
    candidates_pool = _gather_pool(db, delivery_date, window_days)
    if not candidates_pool:
        return []

    # 2) 评分
    scored: list[MatchCandidate] = []
    for fo in candidates_pool:
        hay_parts = [
            fo.sku or "", fo.product_code or "",
            (fo.platform_order_no or ""), (fo.factory_order_no or ""),
        ]
        hay = " ".join(p for p in hay_parts if p).lower()
        score = _token_score(haystack, hay) if hay else 0.0
        if score <= 0.05:
            continue
        confidence = Decimal(round(min(100.0, score * 100), 2))
        # 数量一致加分
        if fo.qty and Decimal(fo.qty) == qty:
            confidence = min(Decimal("100"), confidence + Decimal("5"))
        reasons = []
        if fo.sku and item_name.lower() in (fo.sku or "").lower():
            reasons.append("SKU 包含商品名")
        if spec and spec.lower() in (fo.sku or "").lower():
            reasons.append("规格命中 SKU")
        if not reasons:
            reasons.append(f"token 相似度 {score:.0%}")
        scored.append(MatchCandidate(
            order_no=fo.platform_order_no or fo.factory_order_no,
            factory_order_no=fo.factory_order_no,
            confidence=confidence,
            method="fuzzy",
            reason=" / ".join(reasons),
            product_code=fo.product_code,
            sku=fo.sku,
            qty=fo.qty,
        ))

    scored.sort(key=lambda c: c.confidence, reverse=True)
    top = scored[:top_n]

    # 3) AI 兜底: 头部低于 70 → 让 AI 在前 8 个里挑一个
    if enable_ai_tiebreaker and (not top or top[0].confidence < Decimal("70")):
        ai_pick = _ai_tiebreaker(db, item_name=item_name, spec=spec, qty=qty,
                                 pool=candidates_pool[:8])
        if ai_pick is not None:
            # 合并: AI 的候选放第一, 其他保留
            top = [ai_pick] + [c for c in top if c.order_no != ai_pick.order_no][: top_n - 1]
    return top


def _gather_pool(
    db: Session, delivery_date: Optional[date], window_days: int,
) -> list[FactoryOrder]:
    """优先取交付日期附近的工厂单, 没有再退到所有未结算单."""
    if delivery_date is not None:
        start = delivery_date - timedelta(days=window_days)
        end = delivery_date + timedelta(days=7)
        rows = db.execute(
            select(FactoryOrder).where(
                or_(
                    FactoryOrder.expected_delivery.between(start, end),
                    FactoryOrder.actual_delivery.between(start, end),
                    FactoryOrder.order_date.between(start, end),
                )
            ).limit(200)
        ).scalars().all()
        if rows:
            return list(rows)
    # 兜底: 最近 200 条
    return list(db.execute(
        select(FactoryOrder).order_by(FactoryOrder.id.desc()).limit(200)
    ).scalars().all())


# ----------------------------- AI 兜底 ---------------------------------- #


_AI_TIEBREAKER_SYSTEM = """你是供应商对账匹配助手。
给你一行供应商送货单 + 我们系统里的若干候选工厂订单,
判断送货单的这一行最有可能对应哪一张订单。
严格按 JSON 返回:
{ "best_factory_order_no": "...", "confidence": 0-100 整数, "reason": "一句话理由" }
如果都不像, best_factory_order_no 填 null, confidence 填 0。
不要任何解释文字, 仅输出 JSON。"""


def _ai_tiebreaker(
    db: Session, *, item_name: str, spec: str, qty: Decimal,
    pool: list[FactoryOrder],
) -> Optional[MatchCandidate]:
    if not pool:
        return None
    cfg = settings_service.get_ai_config(db, "diagnose")  # 用诊断模型, 不要 vision
    try:
        provider = build_provider(cfg)
    except AiUnavailable:
        return None

    options = [
        {
            "factory_order_no": fo.factory_order_no,
            "platform_order_no": fo.platform_order_no,
            "factory_name": fo.factory_name,
            "product_code": fo.product_code,
            "sku": fo.sku,
            "qty": fo.qty,
        }
        for fo in pool
    ]
    payload = {
        "delivery_line": {"item_name": item_name, "spec": spec, "qty": float(qty)},
        "candidates": options,
    }
    try:
        resp = provider.chat(
            system=_AI_TIEBREAKER_SYSTEM,
            user=json.dumps(payload, ensure_ascii=False),
            max_tokens=200,
        )
    except AiUnavailable:
        return None

    try:
        data = json.loads(_strip_fence(resp.text))
    except (json.JSONDecodeError, ValueError):
        return None
    best = data.get("best_factory_order_no")
    if not best:
        return None
    fo = next((f for f in pool if f.factory_order_no == best), None)
    if fo is None:
        return None
    conf = Decimal(int(data.get("confidence") or 0))
    return MatchCandidate(
        order_no=fo.platform_order_no or fo.factory_order_no,
        factory_order_no=fo.factory_order_no,
        confidence=conf,
        method="ai",
        reason=str(data.get("reason") or "AI 判定"),
        product_code=fo.product_code,
        sku=fo.sku,
        qty=fo.qty,
    )


def _strip_fence(text: str) -> str:
    import re
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE).strip()


# ----------------------------- 持久化 helper ---------------------------- #


def apply_candidates_to_line(line, candidates: list[MatchCandidate]) -> None:
    """把匹配结果写到 DeliveryNoteLine 上 (头部 = 当前选中)."""
    if not candidates:
        line.matched_order_no = None
        line.match_confidence = Decimal("0")
        line.match_method = "none"
        line.match_candidates = []
        return
    top = candidates[0]
    line.matched_order_no = top.order_no
    line.match_confidence = top.confidence
    line.match_method = top.method
    line.match_candidates = [c.to_json() for c in candidates]
