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
_AI_MODEL_DEFAULT = "qwen2.5vl:7b"   # agent OCR 默认(视觉); 文本推理另用 _AI_MODEL(qwen3.5:9b)


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


# 必须带成本类关键词 (不再匹配裸 ¥X / X元 —— 那些常是"活动到手"售价)。
# 连接词放宽: 成本算100 / 成本设置为20 / 成本为0 / 成本=200 都要能取到 (审计修 2026-06-17)。
_COST_KW = re.compile(
    r"(?:成本|材料费|料费|纯材料|加价|定制费|工厂价)\s*(?:设置为|设为|算|为|是|约|大概|[:：=])?\s*[¥￥]?\s*(\d+(?:\.\d+)?)"
)


def _r_cost_keyword(db, o, txt):
    m = _COST_KW.search(txt)
    if not m:
        return None
    amt = _d(m.group(1))
    if amt is None or amt < 0:
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


# 数量: "插座×3" / "插座 3" (数字在后) 或 "3个插座" / "2插座" (数字在前) 都要取到 (审计修 2026-06-17)
_SOCKET_QTY = re.compile(r"插座\s*[x×\*]?\s*(\d+)|(\d+)\s*个?\s*插座")
# 大件关键词: 备注里有这些 → 不是纯插座追加, 别只算插座(会严重低估), 交 AI/85% 兜底
_BIG_ITEM_KW = ("柜", "桌", "床", "灯带", "岩板", "玻璃", "水管", "移门", "背板", "抽屉", "大板")


def _r_socket(db, o, txt):
    if "插座" not in txt:
        return None
    if any(k in txt for k in _BIG_ITEM_KW):
        return None   # 插座与大件混在一起 → 非纯插座单, 不能只算 ¥63 (审计修 2026-06-17)
    m = _SOCKET_QTY.search(txt)
    qty = int(m.group(1) or m.group(2)) if m else 1
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
    """AI 经取数 agent(:8500, 已 LAN 可达+token) 代理本机 Ollama, 免 NAS→PC:11434 防火墙。
    agent 在线返回 True, 否则 None (复杂单直接落 85% 兜底)。"""
    from app.services import web_agent_service
    try:
        return True if web_agent_service.health(db).get("online") else None
    except Exception:  # noqa: BLE001
        return None


# 文本数值推理任务模型 (用户 2026-06-27 本地升级): 旧 qwen2.5:7b-instruct → qwen3.5:9b。
# ⚠ qwen3.5 是思考型: 必须 think=False(由取数 agent /api/ai/chat 关思考)否则答案落 thinking、content 空。
# 实测 qwen3.5:9b+think=False 干净返回 JSON(9.6s)。(qwen3-vl 思考停不下来不适合, 故文本仍用纯文本 qwen3.5)
_AI_MODEL = "qwen3.5:9b"
_AI_TEMPERATURE = 0.2

_AI_SYS = ("你是家具定制工厂成本估算助手。给你「该产品基础款工厂成本」作锚点时, 你【必须】给出一个数字成本, 绝不允许返回 null。"
           "做法: 以基础款成本为起点, 按客户定制改动加减: 改大尺寸/换更贵材质(樱桃木>榉木, 洞石岩板>白岩板)→上浮10%~60%; "
           "追加灯带/插座/封口/玻璃→每项加几十到几百元。即使信息不全, 也要在基础款成本附近给一个合理估值。"
           "只有当完全没有给基础款锚点且备注无任何可估信息时才允许 null。"
           "只输出 JSON: {\"cost\": 数字, \"reason\": \"一句话\"}。不要输出 JSON 以外的字符。")


def _base_cost_hint(db: Session, o: Order) -> Optional[str]:
    """该订单产品的基础款工厂成本参考(给 AI 当锚点): 优先定价表 factory_cost, 兜底同款已填 actual_cost。"""
    from app.models.pricing import PricingSku
    pc = o.product_code
    codes: set = set()
    if pc:
        codes.add(pc)
        if pc.startswith("P") and not pc.startswith("PPS"):
            codes.add("PPS" + pc[1:])
        elif pc.startswith("PPS"):
            codes.add("P" + pc[3:])
    costs: list[float] = []
    if codes:
        for s in db.execute(select(PricingSku).where(PricingSku.product_code.in_(codes))).scalars().all():
            if s.factory_cost is not None and float(s.factory_cost) > 0:
                costs.append(float(s.factory_cost))
        if not costs:   # 兜底: 同款其它订单已填的工厂实际成本
            for (ac,) in db.execute(select(Order.actual_cost).where(
                    Order.product_code.in_(codes), Order.actual_cost.isnot(None))).all():
                if ac and float(ac) > 0:
                    costs.append(float(ac))
    if not costs:
        return None
    lo, hi, typ = min(costs), max(costs), sorted(costs)[len(costs) // 2]
    if lo == hi:
        return f"该产品基础款工厂成本约 ¥{typ:.0f}"
    return f"该产品基础款工厂成本约 ¥{typ:.0f} (同款各规格 ¥{lo:.0f}~¥{hi:.0f})"


def ai_estimate(db: Session, o: Order, txt: str) -> Optional[dict]:
    """经 agent 调本机大模型估算。agent/模型不可达 → 抛 AiUnavailable (上层据此飞书报警)。"""
    from app.services import web_agent_service
    from app.services.ai_provider import AiUnavailable
    hint = _base_cost_hint(db, o)
    user = (f"产品: {o.product_name or o.product_code or ''}\n"
            + (f"{hint}\n" if hint else "")
            + f"客户定制要求(备注): {txt}\n"
            f"买家实付: {float(o.paid_amount or 0)} 元\n"
            "请基于基础款成本 + 上面的定制改动, 估算这个定制单的工厂成本。")
    resp = web_agent_service._post(db, "/api/ai/chat",
                                   {"system": _AI_SYS, "user": user, "max_tokens": 200,
                                    "model": _AI_MODEL, "temperature": _AI_TEMPERATURE}, timeout=130)
    if not resp.get("ok"):
        raise AiUnavailable(resp.get("error", "本地模型/agent 不可达"))
    m = re.search(r"\{.*\}", resp.get("text") or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    cost = _d(d.get("cost"))
    if cost is None or cost <= 0:
        return None
    # 防"成本>实付"假亏: 补差价/定金/小额补拍单的真实成本在主单上, AI 锚定基础款成本会严重高估本单
    # (实测见 实付¥18→AI¥3950 这类)。只采信"低于 85% 兜底"的 AI 估值(AI 只能把成本往下修并给依据);
    # 否则维持 85% 兜底(标红待人工), 让人去核 —— 不把不靠谱的高估当成本入账。
    paid = _d(o.paid_amount) or Decimal("0")
    if paid > 0 and cost >= paid * FALLBACK_RATE:
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


def _display_resolve(db: Session, o: Order, txt: str) -> dict:
    """页面展示口径: 规则优先; 规则没中时显示已写回的 theoretical_cost(85% 或 AI), 否则现算 85%。
    (AI 走后台『AI 重算兜底』写回 theoretical_cost, 列表读它 —— 避免同步跑 AI 超时 504)。"""
    r = resolve_rules(db, o, txt)
    if r is not None:
        return r
    tc = o.theoretical_cost
    if tc is not None:
        paid = _d(o.paid_amount) or Decimal("0")
        is_85 = abs(float(tc) - round(float(paid) * float(FALLBACK_RATE), 2)) < 0.5
        if is_85:
            return {"cost": _d(tc), "method": "85%兜底(待人工核价)", "confidence": "low",
                    "detail": "实付×85% (粗估, 等工厂成本/人工/AI覆盖)", "source": "fallback"}
        return {"cost": _d(tc), "method": "本地AI估算(已写回)", "confidence": "mid",
                "detail": "AI 估算并写回; 工厂成本到位后覆盖", "source": "ai"}
    return fallback_85(o)


def list_custom_reconcile(db: Session, *, only_missing: bool = True) -> dict:
    """定制单核对清单。规则 + 已写回(85%/AI)展示; AI 估算走后台写回 (避免同步超时)。

    打开页面即自动把缺成本单的推演写回 theoretical_cost(规则→85%), 无需逐单点"写回"
    (用户拍板 2026-06-17: 去掉写回按钮, 推演本就是预算价, 实际工厂成本到位后自动覆盖)。
    auto_backfill 只填空缺、不覆盖已有规则/AI 推演, 幂等可重入。
    """
    auto_backfill_custom_costs(db, use_ai=False)
    rows: list[dict] = []
    for o in db.query(Order).filter(
        Order.is_refill == False,                  # noqa: E712
        Order.status.notin_(["cancelled"]),
    ).all():
        if not is_custom_order(o):
            continue
        if only_missing and o.actual_cost is not None:
            continue
        rows.append(_row(db, o, _display_resolve(db, o, remark_text(o))))
    rows.sort(key=lambda r: ({"low": 0, "mid": 1, "high": 2}.get(r["confidence"], 0), -(r["paid_amount"] or 0)))
    return {
        "rows": rows,
        "count": len(rows),
        "low_confidence_count": sum(1 for r in rows if r["needs_review"]),
        "ai_count": sum(1 for r in rows if r["source"] == "ai"),
        "socket_material_code": SOCKET_MATERIAL_CODE,
        "fallback_rate": float(FALLBACK_RATE),
    }


def auto_backfill_custom_costs_bg(use_ai: bool = True) -> dict:
    """后台跑 (自带 session): 给「AI 重算兜底」按钮用, 避免同步 45 单跑 AI 超时 504。"""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        return auto_backfill_custom_costs(db, use_ai=use_ai)
    finally:
        db.close()


def auto_backfill_custom_costs(db: Session, *, use_ai: bool = False) -> dict:
    """自动给「缺成本依据」的定制单写推演成本到 theoretical_cost (规则→[AI]→85%兜底)。

    工厂实际成本(actual_cost)/定制加价(custom_surcharge) 已有的不动 (它们更权威);
    写到 theoretical_cost 后, 全系统会计成本(order_financials)自动用上 —— 即"和所有核算做钩子"。
    用户拍板 2026-06-17: 实时同步里自动跑, 不用手点; AI 默认关(慢/依赖PC), 复杂单先落 85% 兜底。
    """
    from app.services.ai_provider import AiUnavailable
    provider = build_ai(db) if use_ai else None
    ai_dead = False
    filled = 0
    for o in db.query(Order).filter(
        Order.is_refill == False,                  # noqa: E712
        Order.status.notin_(["cancelled"]),
    ).all():
        if not is_custom_order(o):
            continue
        if o.actual_cost is not None or o.custom_surcharge is not None:
            continue   # 已有权威成本依据, 不动
        txt = remark_text(o)
        r = resolve_rules(db, o, txt)
        if r is None and provider is not None and not ai_dead:
            try:
                r = ai_estimate(db, o, txt)
            except AiUnavailable:
                ai_dead = True
                _alert_pc_off(db)
        if r is None:
            # 兜底只填空缺, 绝不覆盖已有的更可信推演(规则/AI 之前写的)。
            # 否则每次取数同步(use_ai=False)都会把 AI 估值打回 85%。规则/AI(r 非 None)仍照常写。
            if o.theoretical_cost is not None:
                continue
            r = fallback_85(o)
        cost = r.get("cost")
        if cost is not None and cost >= 0:
            o.theoretical_cost = cost   # 定制单推演成本写回 → 喂给全系统会计成本
            filled += 1
    db.commit()
    return {"filled": filled, "ai_unavailable": ai_dead}


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
