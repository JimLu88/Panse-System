"""导入后 AI 核查 + 运营分析 (用户需求 #8).

每次导入完成后:
    (a) 逻辑核查: 先用廉价 DB 查询撒网捞出"候选异常", 再交给 AI 判定哪些是真问题
        + 给严重度/说明, 写入 DataException ("异常"池)。AI 不可用时直接把确定性
        候选写入 (售价低于成本 / 负库存 等本来就是硬错误)。
    (b) 运营分析: 复用 briefing_service 产出一段经营状况文字, 返回给前端。

设计原则: AI 不可用 / 出错时静默降级, 绝不阻断导入主流程。
"""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services import ai_assistant, exception_service, settings_service
from app.services.ai_provider import AiUnavailable, build_provider

_logger = logging.getLogger("panse.post_import")

_LOGIC_SYSTEM = """你是畔色家具 ERP 的数据稽核员。给你一批从 Excel 刚导入的"候选异常"数据,
请判断每一条是否构成真正的业务逻辑错误 (不要被表面规则束缚, 结合常识判断),
并对确属问题的条目输出严格 JSON:

{
  "issues": [
    {"source_table": "表名", "source_pk": "主键", "exception_type": "简短类型",
     "severity": "info|warning|critical", "description": "一句话说明问题"}
  ]
}

只输出 JSON。没有真正问题就返回 {"issues": []}。最多 30 条。"""


def _f(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gather_candidates(db: Session) -> dict:
    """廉价撒网: 捞出可能有逻辑问题的行 (有上限, 不拖慢导入)。"""
    cands: dict[str, list] = {}

    # 1. 定价: 售价 < 成本 (毛亏)
    from app.models.pricing import PricingSku
    pricing_bad = []
    for p in db.execute(select(PricingSku).limit(500)).scalars():
        sell = p.daily_price if p.daily_price is not None else p.list_price
        cost = p.accounting_cost if p.accounting_cost is not None else p.physical_cost
        if sell is not None and cost is not None and Decimal(sell) < Decimal(cost):
            pricing_bad.append({"sku_code": p.sku_code, "sell": _f(sell), "cost": _f(cost)})
        if len(pricing_bad) >= 30:
            break
    if pricing_bad:
        cands["pricing_below_cost"] = pricing_bad

    # 2. 库存: 负的实物数 / 锁定数 > 实物数
    from app.models.inventory import PartInventory
    inv_bad = []
    for inv in db.execute(select(PartInventory).limit(800)).scalars():
        if inv.physical_qty is not None and Decimal(inv.physical_qty) < 0:
            inv_bad.append({"material_code": inv.material_code, "warehouse": inv.warehouse,
                            "physical_qty": _f(inv.physical_qty), "issue": "负库存"})
        elif (inv.locked_qty is not None and inv.physical_qty is not None
              and Decimal(inv.locked_qty) > Decimal(inv.physical_qty)):
            inv_bad.append({"material_code": inv.material_code, "warehouse": inv.warehouse,
                            "physical_qty": _f(inv.physical_qty),
                            "locked_qty": _f(inv.locked_qty), "issue": "锁定数超过实物数"})
        if len(inv_bad) >= 30:
            break
    if inv_bad:
        cands["inventory_anomaly"] = inv_bad

    # 3. 订单: 数量 <= 0
    from app.models.order import Order
    order_bad = []
    for o in db.execute(select(Order).where(Order.qty <= 0).limit(30)).scalars():
        order_bad.append({"order_no": o.order_no, "qty": o.qty})
    if order_bad:
        cands["order_qty_invalid"] = order_bad

    return cands


def _write_deterministic(db: Session, cands: dict) -> int:
    """AI 不可用时, 把硬错误候选直接写异常池。"""
    n = 0
    for sku in cands.get("pricing_below_cost", []):
        exception_service.record(
            db, source_table="pricing_sku", source_pk=sku["sku_code"],
            exception_type="pricing_below_cost", severity="warning",
            description=f"售价 {sku['sell']} 低于成本 {sku['cost']}",
            suggestion_action="view", context=sku,
        )
        n += 1
    for inv in cands.get("inventory_anomaly", []):
        exception_service.record(
            db, source_table="part_inventory",
            source_pk=f"{inv['warehouse']}|{inv['material_code']}",
            exception_type="inventory_anomaly", severity="warning",
            description=inv.get("issue", "库存异常"), suggestion_action="view", context=inv,
        )
        n += 1
    for o in cands.get("order_qty_invalid", []):
        exception_service.record(
            db, source_table="orders", source_pk=o["order_no"],
            exception_type="order_qty_invalid", severity="warning",
            description=f"订单数量异常: {o['qty']}", suggestion_action="view", context=o,
        )
        n += 1
    return n


def _ai_logic_check(db: Session, cands: dict, summary: dict) -> int:
    cfg = settings_service.get_ai_config(db, "diagnose")
    provider = build_provider(cfg)   # 调用方已确保 configured, 这里失败抛 AiUnavailable
    user_msg = json.dumps({"import_summary": summary, "candidates": cands},
                          ensure_ascii=False)
    resp = provider.chat(system=_LOGIC_SYSTEM, user=user_msg, max_tokens=1500)
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.text.strip(), flags=re.MULTILINE)
    try:
        data = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            return 0
        data = json.loads(cleaned[start:end + 1])
    issues = data.get("issues") or []
    n = 0
    for it in issues[:30]:
        if not isinstance(it, dict) or not it.get("description"):
            continue
        exception_service.record(
            db,
            source_table=str(it.get("source_table") or "import"),
            source_pk=(str(it["source_pk"]) if it.get("source_pk") is not None else None),
            exception_type=str(it.get("exception_type") or "ai_logic_check"),
            severity=str(it.get("severity") or "warning"),
            description=str(it["description"])[:500],
            suggestion_action="view",
            context={"ai": True},
        )
        n += 1
    return n


def run_after_import(db: Session, *, summary: Optional[dict] = None) -> dict:
    """导入后总入口。返回 {logic_issues, analysis, ai_used}。绝不抛异常。"""
    result: dict[str, Any] = {"logic_issues": 0, "analysis": None, "ai_used": False}
    summary = summary or {}
    try:
        cands = _gather_candidates(db)
    except Exception as e:  # pragma: no cover
        _logger.warning("候选异常采集失败: %s", e)
        cands = {}

    configured = ai_assistant.is_configured(db)
    if configured and cands:
        try:
            result["logic_issues"] = _ai_logic_check(db, cands, summary)
            result["ai_used"] = True
        except AiUnavailable:
            result["logic_issues"] = _write_deterministic(db, cands)
        except Exception as e:  # pragma: no cover
            _logger.warning("AI 逻辑核查失败, 退回确定性写入: %s", e)
            result["logic_issues"] = _write_deterministic(db, cands)
    elif cands:
        result["logic_issues"] = _write_deterministic(db, cands)

    # 运营分析 (复用每日简报生成器, 不推群)
    if configured:
        try:
            from app.services import briefing_service
            b = briefing_service.generate(db, push=False)
            result["analysis"] = b.content
            result["ai_used"] = True
        except Exception as e:  # pragma: no cover
            _logger.warning("运营分析生成失败: %s", e)

    db.flush()
    return result
