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


def match_material_code(db: Session, *, apply: bool = False) -> dict:
    """给没料号的配件采购匹配 material_code(→分类), 让它进大宗对账"零星实际"按分类汇总。

    ① 物料名子串命中某 Material 名 → 用该料号;
    ② 否则按类别关键词(复用配件库归类规则)推出分类 → 取该分类一个代表料号。
    只填空、幂等。
    """
    from app.models.material import Material
    from app.services import material_category_service as mcs
    mats = db.execute(select(Material)).scalars().all()
    by_name = [((m.name or "").strip(), m.code) for m in mats if m.name and m.code]
    by_name.sort(key=lambda x: -len(x[0]))   # 长名优先, 避免短词误命中
    cat_rep: dict[str, str] = {}
    for m in mats:
        if m.category and (m.code or "").upper().startswith("AC") and m.category not in cat_rep:
            cat_rep[m.category] = m.code
    rows = db.execute(
        select(PartPurchase).where(
            or_(PartPurchase.material_code.is_(None), PartPurchase.material_code == ""),
            PartPurchase.material_name.isnot(None),
        )
    ).scalars().all()
    matched: list[dict] = []
    for p in rows:
        name = (p.material_name or "").strip()
        if not name:
            continue
        code = how = None
        for mname, mcode in by_name:
            if len(mname) >= 2 and (mname in name or (len(name) >= 3 and name in mname)):
                code, how = mcode, "名称"
                break
        if not code:
            cat = mcs._ac_category(name)   # 类别关键词 → 分类
            if cat and cat in cat_rep:
                code, how = cat_rep[cat], f"类别({cat})"
        if code:
            if apply:
                p.material_code = code
            matched.append({"purchase_no": p.purchase_no, "material_name": name,
                            "material_code": code, "by": how})
    if apply and matched:
        db.commit()
    return {"applied": apply, "matched": len(matched), "items": matched[:50]}


def run_capture(db: Session, *, apply: bool = False) -> dict:
    """一键: 料号/分类匹配 + 解析订单号 + 改挂匿名供应商 + 把关联订单的采购汇总进 actual_parts。"""
    mats = match_material_code(db, apply=apply)
    orders = link_orders_from_remark(db, apply=apply)
    suppliers = relabel_supplier_from_remark(db, apply=apply)
    parts = {}
    if apply:
        from app.services import parts_recon_service
        parts = parts_recon_service.aggregate_related_purchases(db, apply=True)
    return {"applied": apply, "material_match": mats, "order_link": orders, "supplier_relabel": suppliers,
            "actual_parts": ({"matched_orders": parts.get("matched_orders"),
                              "total": parts.get("total_parts_amount")} if apply else None)}
