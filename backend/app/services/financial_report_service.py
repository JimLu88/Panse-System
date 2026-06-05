"""月度财务报表 (优化 #10).

复用 sales_analytics.summary (口径与日报一致) 算某月 营收/成本/毛利/净利/订单数,
产出 dict / 文本 / Excel。供"每月 1 号自动推送上月报表"的定时任务 + 后台手动下载。
"""
from __future__ import annotations

import calendar
import io
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.services import sales_analytics


def monthly_summary(db: Session, year: int, month: int) -> dict[str, Any]:
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    s = sales_analytics.summary(db, start=start, end=end)

    def f(v: Any) -> float:
        return float(v or 0)

    return {
        "period": f"{year}-{month:02d}",
        "year": year,
        "month": month,
        "order_count": s.order_count,
        "revenue": f(s.revenue),
        "cost": f(s.cost),
        "gross_profit": f(s.gross_profit),
        "net_profit": f(s.net_profit),
    }


def summary_text(summary: dict) -> str:
    return (
        f"📊 {summary['period']} 月度财务报表\n"
        f"订单数: {summary['order_count']}\n"
        f"营收(店铺实收): ¥{summary['revenue']:.2f}\n"
        f"成本: ¥{summary['cost']:.2f}\n"
        f"毛利: ¥{summary['gross_profit']:.2f}\n"
        f"净利: ¥{summary['net_profit']:.2f}"
    )


def build_excel(summary: dict) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "月度财务"
    rows = [
        ("畔色孚格 ERP 月度财务报表", ""),
        ("会计期间", summary["period"]),
        ("订单数", summary["order_count"]),
        ("营收(店铺实收)", summary["revenue"]),
        ("成本", summary["cost"]),
        ("毛利", summary["gross_profit"]),
        ("净利", summary["net_profit"]),
    ]
    for r in rows:
        ws.append(list(r))
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
