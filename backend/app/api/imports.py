"""导入档案: 列出 / 下载 每次导入归档的原始文件 (表格/图片), 可回溯对账。"""
from __future__ import annotations

import io
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.import_file import ImportedFile
from app.services import import_storage

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _out(r: ImportedFile) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "original_filename": r.original_filename,
        "size_bytes": r.size_bytes,
        "source": r.source,
        "row_summary": r.row_summary,
        "uploaded_by": r.uploaded_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/files")
def list_files(
    kind: str | None = Query(None, description="按导入类型筛 orders/alipay/settlement/..."),
    month: str | None = Query(None, description="YYYY-MM 按归档月筛"),
    limit: int = Query(200, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    rows = db.execute(
        select(ImportedFile).order_by(ImportedFile.id.desc())
    ).scalars().all()
    if kind:
        rows = [r for r in rows if r.kind == kind]
    if month:
        rows = [r for r in rows if r.created_at and r.created_at.strftime("%Y-%m") == month]
    total = len(rows)
    page = rows[offset:offset + limit]
    return {"total": total, "files": [_out(r) for r in page]}


@router.get("/files/summary")
def files_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    by_kind = db.execute(
        select(ImportedFile.kind, func.count(ImportedFile.id)).group_by(ImportedFile.kind)
    ).all()
    total = db.execute(select(func.count(ImportedFile.id))).scalar_one()
    return {"total": int(total or 0), "by_kind": {k: int(n) for k, n in by_kind}}


@router.get("/files/{file_id}/download")
def download_file(
    file_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    rec = db.get(ImportedFile, file_id)
    if rec is None:
        raise HTTPException(404, "归档文件不存在")
    try:
        data = import_storage.read(rec.stored_path)
    except (FileNotFoundError, PermissionError) as e:
        raise HTTPException(404, f"原文件已不可读: {e}") from e
    fn = rec.original_filename or f"import-{rec.id}{Path(rec.stored_path).suffix}"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fn)}"},
    )
