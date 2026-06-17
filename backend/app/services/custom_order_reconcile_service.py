# -*- coding: utf-8 -*-
"""定制单核对 — 按订单备注「推演」一个工厂成本 (用户拍板 2026-06-17)。

只作推演展示, 一旦填了工厂实际成本 (actual_cost), 推演被工厂成本全覆盖。

成本解析做成**可插拔 resolver 链** + **预留外部 API 钩子**:
真实备注可能比简单规则复杂得多, 复杂的交给可配置的外部服务
(setting `custom_reconcile_api_url`, 未来可接定制报价 v1.1 / LLM) 处理;
没接通就落到「需系统运算(1.1)」由人工/系统再算。

resolver 链 (第一个返回非 None 即用):
  1. 工厂实际成本 actual_cost → 覆盖, final
  2. 定制加价 custom_surcharge → 基础BOM + 加价
  3. 备注含「插座」→ 物料库 AC-1007 单价 × 数量
  4. 备注含百分比 (如 30%) → 实付 × 比例
  5. 备注含金额 (成本=200 / ¥200 / 200元) → 该金额
  6. 预留外部 API → 复杂备注交外部服务
  7. 兜底 → 需系统运算(1.1)
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material
from app.models.order import Order
from app.services import settings_service
from app.services.data_quality_service import is_custom_order

SOCKET_MATERIAL_CODE = "AC-1007"            # 电力轨道插座
API_URL_KEY = "custom_reconcile_api_url"    # 预留: 复杂备注外部解析服务 URL


def _dec(v) -> Optional[Decimal]:
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        return None


def remark_text(o: Order) -> str:
    """合并三处备注: 商家备注(平台) + ERP人工备注 + 买家留言。"""
    parts = [getattr(o, f, None) for f in ("seller_memo", "remark", "buyer_message")]
    return " ".join(str(p) for p in parts if p).strip()


# ── resolvers: 返回 {cost, method, detail, source, final?} 或 None 放行 ──────────
def _r_actual(db, o, txt):
    if o.actual_cost is not None:
        return {"cost": _dec(o.actual_cost), "method": "工厂成本(已覆盖)",
                "detail": "已填工厂实际成本, 推演作废", "source": "factory", "final": True}
    return None


def _r_surcharge(db, o, txt):
    if o.custom_surcharge is not None:
        base = _dec(o.theoretical_cost) or Decimal("0")
        return {"cost": base + _dec(o.custom_surcharge), "method": "定制加价+BOM",
                "detail": f"基础BOM {base} + 定制加价 {o.custom_surcharge}", "source": "surcharge"}
    return None


_SOCKET_QTY = re.compile(r"插座\s*[x×\*]?\s*(\d+)")


def _r_socket(db, o, txt):
    if "插座" not in txt:
        return None
    m = _SOCKET_QTY.search(txt)
    qty = int(m.group(1)) if m else 1
    mat = db.execute(select(Material).where(Material.code == SOCKET_MATERIAL_CODE)).scalar_one_or_none()
    price = _dec(mat.price) if (mat and mat.price is not None) else None
    if price is None:
        return {"cost": None, "method": "插座→AC-1007",
                "detail": f"物料库无 {SOCKET_MATERIAL_CODE} 单价, 请先到物料单价库补价", "source": "socket"}
    return {"cost": price * qty, "method": "插座→AC-1007",
            "detail": f"AC-1007 单价 {price} × {qty}", "source": "socket"}


_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _r_percent(db, o, txt):
    m = _PCT.search(txt)
    if not m:
        return None
    pct = _dec(m.group(1))
    if pct is None:
        return None
    paid = _dec(o.paid_amount) or Decimal("0")
    cost = (paid * pct / Decimal("100")).quantize(Decimal("0.01"))
    return {"cost": cost, "method": "备注比例×实付",
            "detail": f"实付 {paid} × {pct}%", "source": "percent"}


# 金额: 带成本类关键词的数字, 或 ¥/元 标注的数字 (避免把尺寸等普通数字误当成本)
_AMT = re.compile(
    r"(?:成本|材料费|料费|加价|定制费|工厂价)\s*[:：=]?\s*[¥￥]?\s*(\d+(?:\.\d+)?)"
    r"|[¥￥]\s*(\d+(?:\.\d+)?)"
    r"|(\d+(?:\.\d+)?)\s*元"
)


def _r_amount(db, o, txt):
    m = _AMT.search(txt)
    if not m:
        return None
    num = next((g for g in m.groups() if g), None)
    amt = _dec(num)
    if amt is None:
        return None
    return {"cost": amt, "method": "备注指定金额", "detail": f"备注金额 {amt}", "source": "amount"}


def _r_external(db, o, txt):
    """预留外部 API: 复杂备注交可配置服务 (未来接 v1.1/LLM)。未配置/失败则放行。"""
    url = settings_service.get(db, API_URL_KEY, env_fallback=False)
    if not url or not txt:
        return None
    try:
        r = requests.post(url, json={
            "order_no": o.order_no, "paid_amount": float(o.paid_amount or 0),
            "remark": txt, "product_name": o.product_name,
            "product_code": o.product_code, "sku": o.sku_code,
        }, timeout=8)
        r.raise_for_status()
        d = r.json() or {}
        if d.get("cost") is None:
            return None
        return {"cost": _dec(d["cost"]), "method": d.get("method") or "外部API推演",
                "detail": d.get("detail") or "外部服务返回", "source": "external"}
    except Exception:  # noqa: BLE001 - 外部服务挂了不能拖垮核对页
        return None


_RESOLVERS = [_r_actual, _r_surcharge, _r_socket, _r_percent, _r_amount, _r_external]


def resolve_cost(db: Session, o: Order, txt: Optional[str] = None) -> dict:
    """跑 resolver 链, 返回推演结果 dict。"""
    if txt is None:
        txt = remark_text(o)
    for fn in _RESOLVERS:
        res = fn(db, o, txt)
        if res is not None:
            return res
    return {"cost": None, "method": "需系统运算(1.1)",
            "detail": "备注复杂/无法识别, 待定制报价系统或人工核算", "source": "manual"}


def _row(db: Session, o: Order) -> dict:
    txt = remark_text(o)
    r = resolve_cost(db, o, txt)
    cost = r.get("cost")
    paid = _dec(o.paid_amount) or Decimal("0")
    return {
        "order_id": o.id,
        "order_no": o.order_no,
        "product_name": o.product_name,
        "product_code": o.product_code,
        "sku": o.sku_code,
        "qty": o.qty,
        "status": o.status,
        "paid_amount": float(paid),
        "remark": txt,
        "actual_cost": float(o.actual_cost) if o.actual_cost is not None else None,
        "projected_cost": float(cost) if cost is not None else None,
        "method": r["method"],
        "detail": r["detail"],
        "source": r["source"],
        "is_final": bool(r.get("final")),     # 工厂成本已覆盖
        "projected_margin": float(paid - cost) if cost is not None else None,
        "projected_margin_rate": (float((paid - cost) / paid) if (cost is not None and paid > 0) else None),
        "needs_compute": r["source"] == "manual",
    }


def list_custom_reconcile(db: Session, *, only_missing: bool = True) -> dict:
    """定制单核对清单。only_missing=True 只看缺工厂成本的(=异常那批); False 看全部定制单。"""
    rows: list[dict] = []
    for o in db.query(Order).filter(
        Order.is_refill == False,                  # noqa: E712
        Order.status.notin_(["cancelled"]),
    ).all():
        if not is_custom_order(o):
            continue
        if only_missing and o.actual_cost is not None:
            continue
        rows.append(_row(db, o))
    rows.sort(key=lambda r: (not r["needs_compute"], -(r["paid_amount"] or 0)))  # 待运算的置顶
    api_url = settings_service.get(db, API_URL_KEY, env_fallback=False)
    return {
        "rows": rows,
        "count": len(rows),
        "needs_compute_count": sum(1 for r in rows if r["needs_compute"]),
        "external_api_configured": bool(api_url),
        "socket_material_code": SOCKET_MATERIAL_CODE,
    }


def apply_projected_cost(db: Session, order_id: int) -> dict:
    """把推演成本写回订单 theoretical_cost (用户逐单确认; 有工厂实际成本则拒绝, 它优先)。"""
    o = db.get(Order, order_id)
    if o is None:
        return {"ok": False, "error": "订单不存在"}
    if o.actual_cost is not None:
        return {"ok": False, "error": "该单已有工厂实际成本, 无需写推演 (工厂成本优先)"}
    r = resolve_cost(db, o)
    cost = r.get("cost")
    if cost is None:
        return {"ok": False, "error": f"无法推演成本: {r['method']} — {r['detail']}"}
    o.theoretical_cost = cost
    db.commit()
    return {"ok": True, "order_no": o.order_no, "written_theoretical_cost": float(cost),
            "method": r["method"]}
