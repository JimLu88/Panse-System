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
        "售价(实收)", "预测工厂价", "盈亏平衡价(红线)", "安全垫(工厂可再高)", "备注",
    ])
    for r in data["rows"]:
        ws.append([
            r["order_no"], r["order_date"], r["product_name"], r["sku"], r["qty"],
            "是" if r["is_custom"] else "",
            r["revenue"], r["factory_predicted"], r["break_even_factory"],
            r["break_even_buffer"], r["note"],
        ])
    t = data["totals"]
    ws.append([])
    ws.append([
        "合计", "", "", "", data["count"], "", t["revenue"],
        t["factory_predicted"], t["break_even_factory"], t["break_even_buffer"],
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
