# -*- coding: utf-8 -*-
"""售后条目自动化 (用户拍板 2026-06-11 "售后做1,2,3"):

  ① 万师傅档案 → 售后: 交易成功 + 已配对订单 + 有服务费 → 自动建售后
     (安装费=服务费, 挂淘宝订单号)
  ② 支付宝售后流水 → 售后: 复用 alipay_flow_router.create_aftersales_from_flows
  ③ 退款驱动: 订单退款 (重导刷出来的) → 自动开售后条目 (原因=平台退款)

全部幂等 (remark/reason 标记防重), 每日 09:00 调度统一跑 + 飞书日报
(有新建才推, 不打扰)。人工只需要处理日报里点名的"待补原因"条目。
"""
from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import WanshifuOrder
from app.models.marketing import AfterSales
from app.models.order import Order

_logger = logging.getLogger("panse.aftersales_auto")


def create_from_wanshifu(db: Session) -> int:
    """① 万师傅交易成功单 → 按服务类型分流 (用户拍板 2026-06-12):

    - 安装 (家具|安装) = 固定成本 → 写进订单 Order.install_fee (只填空缺, 留痕),
      方便统计整体成本; 不建售后条。
    - 维修/更换配件/未知 = 变动成本 → 建售后条目 (估算在总金额里, 不按单摊)。
    返回处理条数 (装费回填 + 售后新建)。
    """
    from app.models.order import Order
    from app.services import field_change_service

    existing_marks = {
        r for r in db.execute(
            select(AfterSales.remark).where(AfterSales.remark.like("自动:万师傅单 %"))
        ).scalars().all() if r
    }
    rows = db.execute(
        select(WanshifuOrder).where(
            WanshifuOrder.status == "交易成功",
            WanshifuOrder.matched_order_no.isnot(None),
        )
    ).scalars().all()
    n = 0
    for w in rows:
        fee = Decimal(w.service_fee or 0)
        if fee <= 0:
            continue
        mark = f"自动:万师傅单 {w.wsf_order_no}"
        is_install = "安装" in (w.service_type or "")
        if is_install:
            order = db.execute(
                select(Order).where(Order.order_no == w.matched_order_no)
            ).scalar_one_or_none()
            # 只填空缺: 已有安装费 (导入/人工) 不覆盖
            if order is None or order.install_fee is not None:
                continue
            order.install_fee = fee
            field_change_service.record(
                db, table="orders", pk=order.order_no, field="install_fee",
                old=None, new=str(fee), actor="万师傅自动", source="web",
                row_label=f"订单 {order.order_no}", field_label="安装费",
            )
            n += 1
            continue
        # 维修/更换配件/未知 → 售后条目 (变动成本池)
        if any(mark in m for m in existing_marks):
            continue
        svc = (w.service_type or "").split("|")[-1] or "维修"
        db.add(AfterSales(
            platform_order_no=w.matched_order_no,
            reason=f"万师傅{svc}",
            wanshifu_deduction=fee,
            processed_at=(w.finished_time.date() if w.finished_time
                          else (w.created_time.date() if w.created_time else date.today())),
            status="auto",
            remark=f"{mark} ({w.product_category or ''} {w.city or ''})".strip(),
        ))
        existing_marks.add(mark)
        n += 1
    db.flush()
    return n


def create_from_refunds(db: Session) -> int:
    """③ 退款订单 → 售后条目。标记: 同订单号 + reason=平台退款 只建一条。

    退款金额不进成本字段 (退款是收入回吐, 不是售后成本), 写在备注里供人工补全。
    """
    from app.services.order_sheet_archive_service import _is_paid, _is_refunded
    existing = {
        no for no in db.execute(
            select(AfterSales.platform_order_no).where(AfterSales.reason == "平台退款")
        ).scalars().all() if no
    }
    # 财务起始线起算 (2025 不导入拍板)
    from app.services.reconciliation_service import _finance_start
    start = _finance_start(db)
    orders = db.execute(
        select(Order).where(Order.order_date >= start, Order.is_refill == False)  # noqa: E712
    ).scalars().all()
    n = 0
    for o in orders:
        if o.order_no in existing or not _is_refunded(o) or not _is_paid(o):
            continue
        db.add(AfterSales(
            platform_order_no=o.order_no,
            reason="平台退款",
            processed_at=o.refund_date or date.today(),
            status="auto",
            remark=(f"自动:订单退款 ¥{o.refund_amount or 0}"
                    f" ({o.refund_status or '退款'}) — 请补退款原因/责任方"),
        ))
        existing.add(o.order_no)
        n += 1
    db.flush()
    return n


def run_daily(db: Session) -> dict:
    """每日 09:00: 三路自动建条 + 飞书日报 (有新建才推)。"""
    from app.services import alipay_flow_router_service
    wsf_n = create_from_wanshifu(db)
    flow_n = alipay_flow_router_service.create_aftersales_from_flows(db)
    refund_n = create_from_refunds(db)
    db.commit()
    total = wsf_n + flow_n + refund_n
    result = {"wanshifu": wsf_n, "flows": flow_n, "refunds": refund_n, "total": total}
    if total:
        text = (f"今日自动新建 {total} 条售后记录: 万师傅安装 {wsf_n}、"
                f"支付宝流水 {flow_n}、订单退款 {refund_n}。\n"
                "退款类已标「请补退款原因/责任方」, 到 售后页 筛状态 auto 补全即可。")
        try:
            from app.services import notify_service
            ok, _ = notify_service.notify(db, text, level="info",
                                          title="畔色 ERP [售后自动化日报]")
            result["pushed"] = bool(ok)
        except Exception:  # pragma: no cover
            result["pushed"] = False
    return result
