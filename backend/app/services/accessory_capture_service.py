# -*- coding: utf-8 -*-
"""配件采购"备注识别" — 零星采购的核心 (用户 2026-06-27)。

用户的结算模型: 零星采购(用了才买、跨平台)→ 支付宝付款时备注写「订单号 or 人名」→ 系统从备注解析:
  - 订单号(15-19位数字, 命中真实订单) → 写 PartPurchase.related_order_no → 经 aggregate_related_purchases
    汇总进 Order.actual_parts(该单转逐项真实计价);
  - 人名/供应商关键字(命中 Supplier.alipay_counterparty_keywords) → 把对手方是"收钱码/付款码"这类
    匿名采购改挂到真实供应商, 让评分/对账归对家。
认不出订单号也认不出供应商的, 由现有 data_quality.scan_unclassified_purchase 捞成"待归类配件采购"异常
(不静默)。本服务只填空/只改匿名行, 幂等。
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.order import Order, PartPurchase
from app.models.supplier import Supplier

_logger = logging.getLogger("panse.accessory_capture")

# 淘宝订单号一般 15-19 位纯数字; 从备注里抠出来再核对是不是真实订单
_ORDER_NO_RE = re.compile(r"(?<!\d)(\d{15,19})(?!\d)")

# 对手方"匿名/付款码"特征 → 这类才允许按备注里的人名改挂供应商(避免误改正常对手方)
_ANON_COUNTERPARTY_KW = ("收钱码", "付款码", "二维码", "个人", "商户", "扫码", "小商户")


def _purchase_text(p: PartPurchase) -> str:
    return f"{p.material_name or ''} {p.remark or ''} {p.spec or ''}"


def link_orders_from_remark(db: Session, *, apply: bool = False) -> dict:
    """从配件采购备注解析订单号 → 填 related_order_no(只填空, 幂等)。返回匹配明细。"""
    valid = {o for (o,) in db.execute(select(Order.order_no)).all() if o}
    rows = db.execute(
        select(PartPurchase).where(
            or_(PartPurchase.related_order_no.is_(None), PartPurchase.related_order_no == ""),
        )
    ).scalars().all()
    linked: list[dict] = []
    for p in rows:
        for cand in _ORDER_NO_RE.findall(_purchase_text(p)):
            if cand in valid:
                if apply:
                    p.related_order_no = cand
                linked.append({"purchase_no": p.purchase_no, "order_no": cand,
                               "supplier": p.supplier, "amount": float(p.amount or 0)})
                break
    if apply and linked:
        db.commit()
    return {"applied": apply, "linked": len(linked), "items": linked[:50]}


def relabel_supplier_from_remark(db: Session, *, apply: bool = False) -> dict:
    """对手方是"收钱码/付款码"等匿名付款的采购, 若备注命中某供应商关键字 → 改挂到该供应商(幂等)。"""
    kw_to_name: list[tuple[str, str]] = []
    for s in db.execute(select(Supplier)).scalars().all():
        for kw in list(s.alipay_counterparty_keywords or []) + [s.name]:
            if kw and len(kw) >= 2:
                kw_to_name.append((kw, s.name))
    # 长关键字优先, 避免短词误命中
    kw_to_name.sort(key=lambda x: -len(x[0]))
    rows = db.execute(select(PartPurchase)).scalars().all()
    relabeled: list[dict] = []
    for p in rows:
        sup = p.supplier or ""
        if not any(a in sup for a in _ANON_COUNTERPARTY_KW):
            continue   # 只处理匿名付款码类; 正常对手方不动
        text = _purchase_text(p)
        for kw, name in kw_to_name:
            if kw in text and p.supplier != name:
                relabeled.append({"purchase_no": p.purchase_no, "from": p.supplier, "to": name})
                if apply:
                    p.supplier = name
                break
    if apply and relabeled:
        db.commit()
    return {"applied": apply, "relabeled": len(relabeled), "items": relabeled[:50]}


def run_capture(db: Session, *, apply: bool = False) -> dict:
    """一键: 解析订单号 + 改挂匿名供应商 + 把关联订单的采购汇总进 actual_parts。"""
    orders = link_orders_from_remark(db, apply=apply)
    suppliers = relabel_supplier_from_remark(db, apply=apply)
    parts = {}
    if apply:
        from app.services import parts_recon_service
        parts = parts_recon_service.aggregate_related_purchases(db, apply=True)
    return {"applied": apply, "order_link": orders, "supplier_relabel": suppliers,
            "actual_parts": ({"matched_orders": parts.get("matched_orders"),
                              "total": parts.get("total_parts_amount")} if apply else None)}
