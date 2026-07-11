"""导入档案: 列出 / 下载 每次导入归档的原始文件 (表格/图片), 可回溯对账。"""
from __future__ import annotations

import io
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.import_file import ImportedFile
from app.services import import_storage, order_sheet_archive_service, settings_service
from app.services.delivery_storage import get_root

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _host_root() -> str:
    """归档根目录在主机(PC)上的路径。后端跑在 Docker(Linux) 里, 容器内是 /app/storage,
    主机上是仓库下的 ./storage。用 HOST_STORAGE_ROOT 覆盖, 默认按本机仓库位置。"""
    return os.environ.get("HOST_STORAGE_ROOT") or r"D:\Panse-System\storage"


def _host_path(container_path: str | None) -> str | None:
    """把容器内路径翻译成主机(PC)上的 Windows 路径, 供前端「打开文件夹」展示/复制。"""
    if not container_path:
        return None
    p = Path(container_path)
    try:
        rel = p.resolve().relative_to(get_root().resolve())
        return str(Path(_host_root()).joinpath(*rel.parts)).replace("/", "\\")
    except Exception:
        return str(p).replace("/", "\\")


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
        "folder": _host_path(str(Path(r.stored_path).parent)) if r.stored_path else None,
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
    return {
        "total": int(total or 0),
        "by_kind": {k: int(n) for k, n in by_kind},
        "imports_root": _host_path(str(get_root() / "imports")),
    }


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


# ── 工厂下单图 → 飞书 手动补推 (修复: 旧逻辑只推"本次新生成"被每小时补生成抢空) ──
@router.get("/order-sheets/push-status")
def order_sheet_push_status(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """资料存档库「推送下单图到飞书」按钮的状态: 飞书是否配好 + 待推张数。

    pending_total: 含历史基线的全部未推 (手动按钮可推的总量);
    pending_new:   不含历史基线 (18:00 自动会推的量, 平时应接近 0)。
    """
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    return {
        "configured": bool(chat_id),
        "pending_total": order_sheet_archive_service.count_pending_push(db, include_baseline=True),
        "pending_new": order_sheet_archive_service.count_pending_push(db, include_baseline=False),
    }


class OrderSheetPushIn(BaseModel):
    limit: int = 20   # 每次最多推多少张 (防一次性刷屏工厂群)


@router.post("/order-sheets/push")
def order_sheet_push(
    payload: OrderSheetPushIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """手动把"还没推过图"的工厂下单图渲染成图片推飞书工厂群 (含历史基线, 分批每次≤上限)。

    用于: 历史堆积补推、或想立刻验证。推成功的会标记 pushed, 不会重复推。
    """
    limit = max(1, min(int(payload.limit or 20), 50))
    res = order_sheet_archive_service.push_pending_images(db, limit=limit, include_baseline=True)
    if res.get("reason") == "no_chat_id":
        raise HTTPException(400, "飞书推送群未配置: 请到 管理 → 飞书 设置 feishu_push_chat_id (推送群会话ID)")
    return res


class PushConfigIn(BaseModel):
    min_amount: float = 400.0   # 实付低于此的单判为补差/加价, 不推工厂 (0=关闭金额规则)


@router.get("/order-sheets/push-config")
def get_order_sheet_push_config(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """工厂下单图推送设置: 补差/加价单不推的金额门槛 + 补差关键词(只读展示)。用户 2026-07-12。"""
    return {
        "min_amount": order_sheet_archive_service._push_min_amount(db),
        "topup_keywords": list(order_sheet_archive_service._PARTS_TOPUP_KEYWORDS),
    }


@router.post("/order-sheets/push-config")
def set_order_sheet_push_config(
    payload: PushConfigIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """设置补差/加价单不推的实付金额门槛 (工厂制作单页面配置, 0=关闭金额规则)。"""
    amt = max(0.0, float(payload.min_amount or 0))
    settings_service.set_value(db, "factory_push_min_amount", str(amt),
                               description="工厂下单图: 实付低于此值判为补差/加价单不推(0=关闭)")
    return {"ok": True, "min_amount": amt}
