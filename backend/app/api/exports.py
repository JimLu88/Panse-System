# -*- coding: utf-8 -*-
"""页面导出 (用户需求 2026-06-11): 每个列表页「导出当页为 Excel」。

前端 (PresetTable 统一按钮) 把当页 标题/列定义/行数据 POST 过来,
这里生成 xlsx 返回, 同时归档进 导入档案 (kind=page_export, 按日期分文件夹),
导出记录在 工具→导入档案→页面导出 分类可查可重新下载。
"""
from __future__ import annotations

import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/api/exports", tags=["exports"])

_MAX_ROWS = 20000


def _cell(v):
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


@router.post("/page")
def export_page(payload: dict, db: Session = Depends(get_db)):
    """body: {title, columns: [{key, title}], rows: [{...}]} → xlsx + 归档留底。"""
    import openpyxl

    from app.services import import_storage

    title = str(payload.get("title") or "页面导出").strip()[:60]
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    if not isinstance(columns, list) or not columns:
        raise HTTPException(400, "columns 不能为空")
    if not isinstance(rows, list):
        raise HTTPException(400, "rows 必须是数组")
    if len(rows) > _MAX_ROWS:
        raise HTTPException(400, f"单次导出最多 {_MAX_ROWS} 行")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title[:28] or "导出"
    keys = [str(c.get("key") or "") for c in columns]
    ws.append([str(c.get("title") or c.get("key") or "") for c in columns])
    for r in rows:
        if isinstance(r, dict):
            ws.append([_cell(r.get(k)) for k in keys])

    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()

    fname = f"{title}_{date.today().isoformat()}.xlsx"
    import_storage.archive(
        db, content=content, original_name=fname, kind="page_export",
        source="web", row_summary={"note": f"{len(rows)} 行 × {len(columns)} 列"},
    )
    db.commit()

    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=page_export.xlsx"},
    )


@router.post("/full")
def export_full(db: Session = Depends(get_db)):
    """全类目 Excel 导出 (工具→Excel 导出): 每个数据表一个 Sheet, 全行 + 末列异常批注。
    导出后存「资料存档库」(kind=full_export), 超 30 份自动删最早。返回 xlsx 下载。"""
    from app.services import data_export_service

    res = data_export_service.run_full_export(db)
    return StreamingResponse(
        io.BytesIO(res["content"]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": "attachment; filename=full_export.xlsx",
            "X-Export-Sheets": str(res["sheets"]),
            "X-Export-Rotated": str(res["rotated_removed"]),
        },
    )
