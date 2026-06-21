# -*- coding: utf-8 -*-
"""物流费账单逐单行 → 淘宝订单 自动配对 (用户 2026-06-21)。

德邦逐单行有 收货人(recipient_name) + 目的地(destination) + 运单号(tracking_no)。
按可靠度配对淘宝订单 (只配已发货成交单, 关闭/未付款单按铁律排除):
    track       运单号全等 (最可靠)
    name_prov   收货姓名全等 + 订单地址含目的地省/市
    name_unique 收货姓名在订单库唯一
命中唯一 → order_no + match_method; 多候选 → 'multi' + 候选写 match_note;
全不中 → 'none' + 原因 (前端在订单号位显示「未能自动匹配」)。
人工指定过的 (match_method='manual') 不重算。summary(月结汇总)行不参与配对。
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import LogisticsBill
from app.models.order import Order
from app.services.sales_analytics import SETTLED_SALE_STATUSES

METHOD_CN = {
    "track": "运单号全等", "name_prov": "姓名+省市", "name_unique": "姓名唯一",
    "name_addr": "姓名在地址(宽松)", "multi": "多候选待人工",
    "none": "未能自动匹配", "manual": "人工指定",
}

# 目的地里的省/市/区 词 (用来和订单地址交叉验证同名是否同地)
_PLACE_RE = re.compile(
    r"(北京|天津|上海|重庆|香港|澳门|"
    r"[一-龥]{2,4}?(?:省|自治区|特别行政区)|"
    r"[一-龥]{2,6}?(?:市|自治州|地区|盟)|"
    r"[一-龥]{2,4}?(?:区|县))"
)


def _place_tokens(text: Optional[str]) -> list[str]:
    """从目的地/地址里抽省/市/区词, 去掉省市区后缀做包含比对 (订单地址写法不统一)。"""
    if not text:
        return []
    out: list[str] = []
    for m in _PLACE_RE.findall(text):
        core = re.sub(r"(省|市|区|县|自治区|自治州|特别行政区|地区|盟)$", "", m)
        if len(core) >= 2:
            out.append(core)
    return out


def match_logistics_bills(db: Session, *, only_unmatched: bool = True, loose: bool = False) -> dict:
    """给逐单物流账单配淘宝订单。返回 {matched, multi, none, skipped} 计数。

    loose=True: 多加一档「宽松」匹配 — 收货人姓名出现在订单收货地址里 + 目的地省市也对上 →
    命中唯一则配(应对 订单存买家昵称、物流写收货人真名 的错位)。仍要求唯一, 不乱配。
    """
    orders = db.execute(
        select(Order.order_no, Order.customer_name, Order.customer_address, Order.tracking_no)
        .where(Order.status.in_(SETTLED_SALE_STATUSES))  # 铁律: 只配已发货成交单, 排除关闭/未付款
    ).all()
    by_track: dict[str, set] = {}
    by_name: dict[str, list] = {}
    for o in orders:
        if o.tracking_no:
            by_track.setdefault(o.tracking_no.strip(), set()).add(o.order_no)
        if o.customer_name:
            by_name.setdefault(o.customer_name.strip(), []).append(o)

    stmt = select(LogisticsBill).where(LogisticsBill.row_type == "line")
    if only_unmatched:
        stmt = stmt.where(
            LogisticsBill.order_no.is_(None),
            (LogisticsBill.match_method.is_(None)) | (LogisticsBill.match_method != "manual"),
        )
    counts = {"matched": 0, "multi": 0, "none": 0, "skipped": 0}
    for b in db.execute(stmt).scalars().all():
        method, nos = None, set()
        if b.tracking_no and b.tracking_no.strip() in by_track:
            method, nos = "track", by_track[b.tracking_no.strip()]
        if not method and b.recipient_name:
            nc = by_name.get(b.recipient_name.strip(), [])
            tokens = _place_tokens(b.destination)
            ac = {o.order_no for o in nc if o.customer_address
                  and any(t in o.customer_address for t in tokens)}
            if ac:
                method, nos = "name_prov", ac
            elif len(nc) == 1:
                method, nos = "name_unique", {nc[0].order_no}
            elif len(nc) > 1:
                method, nos = "multi", {o.order_no for o in nc}
        # 宽松档: 收货人姓名(≥2字)出现在订单收货地址里 + 目的地省市也在 → 唯一才配
        if not method and loose and b.recipient_name and len(b.recipient_name.strip()) >= 2:
            nm = b.recipient_name.strip()
            tokens = _place_tokens(b.destination)
            ac = {o.order_no for o in orders if o.customer_address and nm in o.customer_address
                  and (not tokens or any(t in o.customer_address for t in tokens))}
            if len(ac) == 1:
                method, nos = "name_addr", ac
            elif len(ac) > 1:
                method, nos = "multi", ac
        if method and method != "multi" and len(nos) == 1:
            b.order_no = next(iter(nos))
            b.match_method = method
            b.match_note = None
            counts["matched"] += 1
        elif method == "multi" or (method and len(nos) > 1):
            b.match_method = "multi"
            cand = "/".join(sorted(nos)[:5])
            b.match_note = f"收货人「{b.recipient_name}」命中多个候选: {cand}"
            counts["multi"] += 1
        else:
            b.match_method = "none"
            if not b.recipient_name:
                b.match_note = "账单无收货人, 无法按人名匹配 (需运单号或人名)"
            elif b.recipient_name.strip() not in by_name:
                b.match_note = f"订单库无收货人「{b.recipient_name}」(主订单未导入 / 姓名对不上)"
            else:
                b.match_note = f"有同名「{b.recipient_name}」但目的地省市对不上"
            counts["none"] += 1
    db.flush()
    # 配单后回填订单的 实际物流费分量, 供 physical_cost 用实际替预估 (用户 2026-06-21)
    try:
        from app.services import order_fee_actual_service
        order_fee_actual_service.sync_fee_components(db)
    except Exception:  # noqa: BLE001 — 回填失败不阻断配单
        pass
    return counts
