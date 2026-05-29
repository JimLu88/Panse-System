"""淘宝批量导出路由 — 用系统数据填好淘宝后台批量格式, 下载即可上传淘宝后台."""
from __future__ import annotations

from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.services import taobao_export_service

router = APIRouter(prefix="/api/taobao-export", tags=["taobao"])

_EXPORTERS = {
    "price_publish": (taobao_export_service.export_price_publish, "价格批量发布"),
    "single_discount": (taobao_export_service.export_single_discount, "单品立减"),
    "promo_signup": (taobao_export_service.export_promo_signup, "大促活动报名"),
    "product_info": (taobao_export_service.export_product_info, "商品信息批量"),
}

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/types")
def list_export_types(_: User = Depends(get_current_user)):
    return [{"key": k, "label": label} for k, (_fn, label) in _EXPORTERS.items()]


@router.get("/{export_type}/download")
def download_export(
    export_type: str,
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    meta = _EXPORTERS.get(export_type)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"未知导出类型: {export_type}")
    fn, _label = meta
    data = fn(db, category=category)
    # ASCII filename 避免 header 编码问题
    filename = f"taobao_{export_type}.xlsx"
    return StreamingResponse(
        BytesIO(data),
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
