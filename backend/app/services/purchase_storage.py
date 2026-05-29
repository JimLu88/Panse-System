"""配件采购发票原图文件存储 (业务需求: 历史发票留存, 可点击查看).

布局: {STORAGE_ROOT}/part_purchases/{year}/{month:02d}/{uuid}.{ext}

STORAGE_ROOT 取自环境变量 DELIVERY_STORAGE_ROOT (与送货单共用根), 默认 ./storage。
"""
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from app.services.delivery_storage import get_root

_ALLOWED_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf", ".bmp")
_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
    ".heic": "image/heic", ".pdf": "application/pdf",
    ".bmp": "image/bmp",
}


def folder_for(year: int, month: int) -> Path:
    return get_root() / "part_purchases" / str(year) / f"{month:02d}"


def save_upload(
    *,
    content: bytes,
    original_name: str,
    on_date: Optional[date] = None,
) -> dict:
    """落地一个上传图片, 返回 {file_path, year, month, original_name, size_bytes, mime_type}."""
    today = on_date or date.today()
    folder = folder_for(today.year, today.month)
    folder.mkdir(parents=True, exist_ok=True)
    ext = Path(original_name).suffix.lower()
    if ext not in _ALLOWED_EXT:
        ext = ".bin"
    full = folder / f"{uuid.uuid4().hex}{ext}"
    full.write_bytes(content)
    return {
        "file_path": str(full),
        "year": today.year,
        "month": today.month,
        "original_name": original_name,
        "size_bytes": len(content),
        "mime_type": _MIME.get(ext, "application/octet-stream"),
    }


def read(file_path: str) -> bytes:
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(file_path)
    root = get_root()
    try:
        p.resolve().relative_to(root)
    except ValueError as e:
        raise PermissionError(f"路径越权: {file_path}") from e
    return p.read_bytes()


def remove(file_path: str) -> None:
    p = Path(file_path)
    try:
        p.resolve().relative_to(get_root())
    except ValueError:
        return
    if p.is_file():
        p.unlink(missing_ok=True)
