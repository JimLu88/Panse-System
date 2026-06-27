"""月度对账中心 API (用户 2026-06-27, 方向三)。

统一所有月结(配件/打包/运费)对账总览 + 一键导出全部月结账单。
只读纯计算, 不写任何表。路由前缀 /api/monthly-settlement。
(与 settlements.py = 消费券/支付宝分账结算 是两码事, 勿混。)
"""
from __future__ import annotations

import io
from urllib.parse import quote

from fastapi import APIRouter, Depends
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
def export(db: Session = Depends(get_db)):
    """一键导出全部月结账单 xlsx(汇总 sheet + 每域 sheet, 内存生成不落盘)。"""
    wb = mss.build_export_workbook(db)
    buf = io.BytesIO()
    wb.save(buf)
    fname = "月度对账_全部月结.xlsx"
    return StreamingResponse(
        io.BytesIO(buf.getvalue()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"},
    )
