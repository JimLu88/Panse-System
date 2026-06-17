# -*- coding: utf-8 -*-
"""定制单核对 — 按订单备注「推演」工厂成本 (用户拍板 2026-06-17)。

分级混合 (规则先扛, 本地AI兜复杂, 85%最终兜底):
  1. 工厂实际成本 actual_cost → 覆盖, final, 置信高
  2. 定制加价 custom_surcharge → 基础BOM + 加价, 高
  3. 写明成本: 备注含「成本=X / 纯材料费X / 加价X」(必须有成本类关键词) → 取 X, 高
     ⚠️ 不再把「活动到手X元」(那是售价!) 当成本 —— 修了旧 bug
  4. 写明百分比: 备注含「成本X%」→ 实付×比例, 高
  5. 插座/配件: 备注含「插座×N」→ 物料库 AC-1007 × N, 高
  6. 本地 AI(qwen2.5vl): 复杂备注(尺寸/改材质等) 交本地大模型估算, 中
     —— 本地模型不可达(PC没开机) → 飞书报警 + 落 85% 兜底
  7. 85% 兜底(待人工): 实付 × 85% 作成本, **低置信标红**, 等人工/工厂报价覆盖

工厂成本(actual_cost)填入后全覆盖推演; 推演仅展示、不入账(点「写回推演」才写理论成本)。
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material
from app.models.order import Order
from app.services import settings_service
from app.services.data_quality_service import is_custom_order

SOCKET_MATERIAL_CODE = "AC-1007"            # 电力轨道插座
FALLBACK_RATE = Decimal("0.85")             # 85% 兜底 (用户拍板 2026-06-17)
# 本地 AI 配置 (默认指向 PC 上的 Ollama OpenAI-compat; PC=取数机 192.168.31.91)
AI_BASE_URL_KEY = "custom_reconcile_ai_base_url"
AI_MODEL_KEY = "custom_reconcile_ai_model"
_AI_BASE_DEFAULT = "http://192.168.31.91:11434/v1"
_AI_MODEL_DEFAULT = "qwen2.5vl:7b"


def _d(v) -> Optional[Decimal]:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def remark_text(o: Order) -> str:
    """合并三处备注: 商家备注(平台) + ERP人工备注 + 买家留言。"""
    parts = [getattr(o, f, None) for f in ("seller_memo", "remark", "buyer_message")]
    return " ".join(str(p) for p in parts if p).strip()


# ── 规则 resolvers: 返回 {cost, method, detail, source, confidence, final?} 或 None ──
def _r_actual(db, o, txt):
    if o.actual_cost is not None:
        return {"cost": _d(o.actual_cost), "method": "工厂成本(已覆盖)", "confidence": "high",
                "detail": "已填工厂实际成本, 推演作废", "source": "factory", "final": True}
    return None


def _r_surcharge(db, o, txt):
    if o.custom_surcharge is not None:
        base = _d(o.theoretical_cost) or Decimal("0")
        return {"cost": base + _d(o.custom_surcharge), "method": "定制加价+BOM", "confidence": "high",
                "detail": f"基础BOM {base} + 定制加价 {o.custom_surcharge}", "source": "surcharge"}
    return None


# 必须带成本类关键词 (不再匹配裸 ¥X / X元 —— 那些常是"活动到手"售价)
_COST_KW = re.compile(r"(?:成本|材料费|料费|纯材料|加价|定制费|工厂价)\s*[:：=]?\s*[¥￥]?\s*(\d+(?:\.\d+)?)")


def _r_cost_keyword(db, o, txt):
    m = _COST_KW.search(txt)
    if not m:
        return None
    amt = _d(m.group(1))
    if amt is None:
        return None
    return {"cost": amt, "method": "备注写明成本", "detail": f"备注成本 {amt}",
            "source": "amount", "confidence": "high"}


_PCT = re.compile(r"成本\s*[:：=]?\s*(\d+(?:\.\d+)?)\s*%|(\d+(?:\.\d+)?)\s*%\s*成本")


def _r_percent(db, o, txt):
    m = _PCT.search(txt)
    if not m:
        return None
    pct = _d(m.group(1) or m.group(2))
    if pct is None:
        return None
    paid = _d(o.paid_amount) or Decimal("0")
    cost = (paid * pct / Decimal("100")).quantize(Decimal("0.01"))
    return {"cost": cost, "method": "备注成本比例×实付", "detail": f"实付 {paid} × {pct}%",
            "source": "percent", "confidence": "high"}


_SOCKET_QTY = re.compile(r"插座\s*[x×\*]?\s*(\d+)")


def _r_socket(db, o, txt):
    if "插座" not in txt:
        return None
    m = _SOCKET_QTY.search(txt)
    qty = int(m.group(1)) if m else 1
    mat = db.execute(select(Material).where(Material.code == SOCKET_MATERIAL_CODE)).scalar_one_or_none()
    price = _d(mat.price) if (mat and mat.price is not None) else None
    if price is None:
        return None   # 无单价 → 让后面 AI/兜底接手
    return {"cost": price * qty, "method": "插座→AC-1007", "confidence": "high",
            "detail": f"AC-1007 单价 {price} × {qty}", "source": "socket"}


_RULE_RESOLVERS = [_r_actual, _r_surcharge, _r_cost_keyword, _r_percent, _r_socket]


def resolve_rules(db: Session, o: Order, txt: str) -> Optional[dict]:
    """只跑确定性规则 (不含 AI / 兜底); 命中返回结果, 否则 None。"""
    for fn in _RULE_RESOLVERS:
        r = fn(db, o, txt)
        if r is not None:
            return r
    return None


def fallback_85(o: Order) -> dict:
    """85% 兜底 (待人工): 实付×85% 作成本, 低置信标红 (用户拍板 2026-06-17)。"""
    paid = _d(o.paid_amount) or Decimal("0")
    return {"cost": (paid * FALLBACK_RATE).quantize(Decimal("0.01")),
            "method": "85%兜底(待人工核价)", "confidence": "low",
            "detail": f"实付 {paid} × 85% (粗估, 等工厂成本/人工覆盖)", "source": "fallback"}


# ── 本地 AI (qwen2.5vl) ──────────────────────────────────────────────────────
def build_ai(db: Session):
    """构造本地模型 provider (OpenAI 兼容/Ollama)。配置缺失返回 None。"""
    from app.services.ai_provider import build_provider
    base = settings_service.get(db, AI_BASE_URL_KEY, env_fallback=False) or _AI_BASE_DEFAULT
    model = settings_service.get(db, AI_MODEL_KEY, env_fallback=False) or _AI_MODEL_DEFAULT
    try:
        return build_provider({"provider": "openai", "api_key": "ollama", "model": model, "base_url": base})
    except Exception:  # noqa: BLE001
        return None


_AI_SYS = ("你是家具定制工厂成本估算助手。根据订单备注里的定制要求(改尺寸/改材质/追加部件等),"
           "估算这一单的工厂成本(人民币元, 只要数字)。只输出 JSON: {\"cost\": 数字 或 null, \"reason\": \"一句话\"}。"
           "完全无法估算就 cost 给 null。不要输出 JSON 以外的任何字符。")


def ai_estimate(provider, o: Order, txt: str) -> Optional[dict]:
    """调本地大模型估算。模型不可达 → 抛 AiUnavailable (上层据此飞书报警)。"""
    user = (f"备注: {txt}\n基础产品: {o.product_name or o.product_code or ''}\n"
            f"买家实付: {float(o.paid_amount or 0)} 元")
    resp = provider.chat(system=_AI_SYS, user=user, max_tokens=200)  # 不可达会抛 AiUnavailable
    m = re.search(r"\{.*\}", resp.text or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    cost = _d(d.get("cost"))
    if cost is None or cost <= 0:
        return None
    return {"cost": cost, "method": "本地AI估算", "confidence": "mid",
            "detail": f"AI: {str(d.get('reason') or '')[:40]}", "source": "ai"}


def _alert_pc_off(db: Session) -> None:
    """本地模型不可达(多半 PC 没开机) → 飞书报警 (用户拍板 2026-06-17)。"""
    msg = "⚠️ 定制单核对: 取数 PC / 本地模型(qwen2.5vl)不可达, 复杂定制单无法 AI 计算价格, 已暂用 85% 兜底。请开机或检查 Ollama。"
    try:
        from app.services import feishu_client
        chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
        if chat_id:
            feishu_client.send_text(db, chat_id, msg)
            return
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services import notify_service
        notify_service.notify(db, msg, level="urgent", title="畔色 ERP [定制单核对]")
    except Exception:  # noqa: BLE001
        pass


def resolve_cost(db: Session, o: Order, txt: Optional[str] = None) -> dict:
    """单单解析 (规则→85%兜底; 不含 AI, 给 apply_projected_cost 用)。"""
    if txt is None:
        txt = remark_text(o)
    return resolve_rules(db, o, txt) or fallback_85(o)


def _row(db: Session, o: Order, r: dict) -> dict:
    paid = _d(o.paid_amount) or Decimal("0")
    cost = r.get("cost")
    return {
        "order_id": o.id, "order_no": o.order_no,
        "product_name": o.product_name, "product_code": o.product_code,
        "sku": o.sku_code, "qty": o.qty, "status": o.status,
        "paid_amount": float(paid), "remark": remark_text(o),
        "actual_cost": float(o.actual_cost) if o.actual_cost is not None else None,
        "projected_cost": float(cost) if cost is not None else None,
        "method": r["method"], "detail": r["detail"], "source": r["source"],
        "confidence": r.get("confidence", "low"),
        "is_final": bool(r.get("final")),
        "projected_margin": float(paid - cost) if cost is not None else None,
        "needs_review": r.get("confidence") == "low",   # 低置信(85%兜底) → 标红待人工
    }


def list_custom_reconcile(db: Session, *, only_missing: bool = True, use_ai: bool = False) -> dict:
    """定制单核对清单。use_ai=True 时复杂单走本地大模型(慢, 按需点); 否则规则+85%兜底。"""
    from app.services.ai_provider import AiUnavailable
    provider = build_ai(db) if use_ai else None
    ai_dead = False
    ai_used = 0
    rows: list[dict] = []
    for o in db.query(Order).filter(
        Order.is_refill == False,                  # noqa: E712
        Order.status.notin_(["cancelled"]),
    ).all():
        if not is_custom_order(o):
            continue
        if only_missing and o.actual_cost is not None:
            continue
        txt = remark_text(o)
        r = resolve_rules(db, o, txt)
        if r is None and provider is not None and not ai_dead:
            try:
                r = ai_estimate(provider, o, txt)
                ai_used += 1
            except AiUnavailable:
                ai_dead = True
                _alert_pc_off(db)
        if r is None:
            r = fallback_85(o)
        rows.append(_row(db, o, r))
    rows.sort(key=lambda r: ({"low": 0, "mid": 1, "high": 2}.get(r["confidence"], 0), -(r["paid_amount"] or 0)))
    return {
        "rows": rows,
        "count": len(rows),
        "low_confidence_count": sum(1 for r in rows if r["needs_review"]),
        "ai_used": ai_used,
        "ai_unavailable": ai_dead,           # 本地模型不可达(已飞书报警)
        "ai_enabled": use_ai,
        "socket_material_code": SOCKET_MATERIAL_CODE,
        "fallback_rate": float(FALLBACK_RATE),
    }


def apply_projected_cost(db: Session, order_id: int) -> dict:
    """把推演成本写回 theoretical_cost (逐单确认; 工厂成本优先, 已有则拒绝)。"""
    o = db.get(Order, order_id)
    if o is None:
        return {"ok": False, "error": "订单不存在"}
    if o.actual_cost is not None:
        return {"ok": False, "error": "该单已有工厂实际成本, 无需写推演 (工厂成本优先)"}
    r = resolve_cost(db, o)
    cost = r.get("cost")
    if cost is None:
        return {"ok": False, "error": f"无法推演成本: {r['method']}"}
    o.theoretical_cost = cost
    db.commit()
    return {"ok": True, "order_no": o.order_no, "written_theoretical_cost": float(cost),
            "method": r["method"]}
