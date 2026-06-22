"""工厂对账单生成 API (用户需求 2026-06-16)。

按月生成给工厂的对账单: 每单 预测工厂价 + 盈亏平衡红线(净不亏) + 安全垫, JSON + xlsx 导出。
只读纯计算, 不写任何表。路由前缀 /api/factory-statement。
"""
from __future__ import annotations

import io
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import factory_statement_service as fss

router = APIRouter(prefix="/api/factory-statement", tags=["factory-statement"])


@router.get("/periods")
def periods(db: Session = Depends(get_db)) -> list[str]:
    """有订单的月份(YYYY-MM)倒序, 给前端月份选择器。"""
    return fss.available_periods(db)


@router.get("/data")
def statement(period: Optional[str] = None, db: Session = Depends(get_db)) -> dict:
    """生成对账单 JSON。period='YYYY-MM' 按下单月筛; 不传=全部(限 5000)。"""
    return fss.generate(db, period=period)


@router.get("/export")
def export(period: Optional[str] = None, db: Session = Depends(get_db)):
    """导出对账单 xlsx (在内存生成, 不落盘)。"""
    import openpyxl

    data = fss.generate(db, period=period)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "工厂对账单"
    ws.append([
        "订单号", "下单日期", "产品", "SKU", "数量", "定制",
        "售价(实收)", "预测工厂价", "盈亏平衡价(红线)", "安全垫(工厂可再高)",
        "预测木作", "实收木作", "预估配件", "物流", "安装", "备注",
    ])
    for r in data["rows"]:
        ws.append([
            r["order_no"], r["order_date"], r["product_name"], r["sku"], r["qty"],
            "是" if r["is_custom"] else "",
            r["revenue"], r["factory_predicted"], r["break_even_factory"],
            r["break_even_buffer"],
            r["predicted_wood"],
            "缺实际工厂价格" if r["actual_wood"] is None else r["actual_wood"],
            r["predicted_parts"], r["logistics"], r["install"], r["note"],
        ])
    t = data["totals"]
    ws.append([])
    ws.append([
        "合计", "", "", "", data["count"], "", t["revenue"],
        t["factory_predicted"], t["break_even_factory"], t["break_even_buffer"],
        t["predicted_wood"], t["actual_wood"], t["predicted_parts"],
        t["logistics"], t["install"],
        f"缺数据 {data['missing']} 单",
    ])
    buf = io.BytesIO()
    wb.save(buf)
    fname = f"factory_statement_{period or 'all'}.xlsx"
    return StreamingResponse(
        io.BytesIO(buf.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.get("/missing-bill-export")
def missing_bill_export(period: Optional[str] = None, db: Session = Depends(get_db)):
    """导出"未录工厂账单"的订单 xlsx (用户需求 2026-06-22)。

    口径: 真实成交(settled)、非补单、未对账(actual_cost 为空)、有商品成本(theoretical_cost>0)的单
    —— 即工厂账单列没覆盖、商品成本只能用估算的那些。供把工厂对账单补录进系统。period='YYYY-MM' 按下单月筛。
    """
    import openpyxl
    from calendar import monthrange
    from datetime import date as _date
    from sqlalchemy import select
    from app.models.order import Order
    from app.services.sales_analytics import settled_sale_clause

    stmt = select(Order).where(
        settled_sale_clause(), Order.is_refill == False,  # noqa: E712
        Order.actual_cost.is_(None),
        Order.theoretical_cost.isnot(None), Order.theoretical_cost > 0,
    )
    if period:
        try:
            y, m = (int(x) for x in str(period).split("-")[:2])
            stmt = stmt.where(
                Order.order_date >= _date(y, m, 1),
                Order.order_date <= _date(y, m, monthrange(y, m)[1]),
            )
        except (ValueError, TypeError):
            pass
    rows = db.execute(stmt.order_by(Order.order_date, Order.order_no)).scalars().all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "未录工厂账单订单"
    ws.append(["订单号", "下单日期", "工厂编号", "产品", "SKU", "数量",
               "实付", "估算商品成本", "实际工厂账单(待填)", "状态"])
    total_est = 0.0
    for o in rows:
        est = float(o.theoretical_cost or 0)
        total_est += est
        ws.append([
            o.order_no,
            o.order_date.isoformat() if o.order_date else "",
            getattr(o, "factory_no", "") or "",
            o.product_name or "", o.sku or "", int(o.qty or 1),
            float(o.paid_amount or 0), round(est, 2), "", o.status or "",
        ])
    ws.append([])
    ws.append(["合计", "", "", "", "", len(rows), "", round(total_est, 2), "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    fname = f"missing_factory_bill_{period or 'all'}.xlsx"
    return StreamingResponse(
        io.BytesIO(buf.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )
