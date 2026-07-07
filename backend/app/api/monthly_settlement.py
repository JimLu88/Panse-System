"""月度对账中心 API (用户 2026-06-27, 方向三)。

统一所有月结(配件/打包/运费)对账总览 + 一键导出全部月结账单。
只读纯计算, 不写任何表。路由前缀 /api/monthly-settlement。
(与 settlements.py = 消费券/支付宝分账结算 是两码事, 勿混。)
"""
from __future__ import annotations

import io
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import monthly_settlement_service as mss

router = APIRouter(prefix="/api/monthly-settlement", tags=["monthly-settlement"])


@router.get("/center")
def center(db: Session = Depends(get_db)) -> dict:
    """月度对账中心: 三域(配件/打包/运费)× 每月 预估|实际|差异|差异%。"""
    return mss.build_center(db)


@router.get("/export")
def export(
    date_from: Optional[str] = Query(None, description="发货日起 'YYYY-MM-DD'(与 year_month 二选一)"),
    date_to: Optional[str] = Query(None, description="发货日止 'YYYY-MM-DD'"),
    year_month: Optional[str] = Query(None, description="单月 'YYYY-MM'"),
    db: Session = Depends(get_db),
):
    """一键导出全部月结账单 xlsx(月结汇总 + 配件四账户逐单BOM明细 + 打包/运费逐单明细, 内存生成不落盘)。
    不传日期=全部账期; 传 date_from/date_to 或 year_month = 按发货日圈定明细页。"""
    wb = mss.build_export_workbook(db, date_from=date_from, date_to=date_to, year_month=year_month)
    buf = io.BytesIO()
    wb.save(buf)
    if year_month:
        tag = year_month
    elif date_from or date_to:
        tag = f"{date_from or ''}~{date_to or ''}"
    else:
        tag = "全部账期"
    fname = f"月结账单_{tag}.xlsx"
    return StreamingResponse(
        io.BytesIO(buf.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )


@router.get("/packing-checklist")
def packing_checklist(year_month: str = Query(..., description="发货月 'YYYY-MM'"),
                      db: Session = Depends(get_db)) -> dict:
    """打包导清单(JSON, 供前端打印): 当月发货单 + 每单预估/实际打包费, 给打包供应商核对。"""
    return mss.packing_checklist(db, year_month=year_month)


@router.get("/packing-checklist.xlsx")
def packing_checklist_xlsx(year_month: str = Query(..., description="发货月 'YYYY-MM'"),
                           db: Session = Depends(get_db)):
    """打包导清单 xlsx 下载(订单号文本格式, 防 Excel 转科学计数法)。"""
    wb, _ = mss.build_packing_checklist_xlsx(db, year_month=year_month)
    buf = io.BytesIO()
    wb.save(buf)
    fname = f"打包对账清单_{year_month}.xlsx"
    return StreamingResponse(
        io.BytesIO(buf.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )
