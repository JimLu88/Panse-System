"""送货单原图文件存储 (业务需求: 按 供应商/年月 归档).

布局: {STORAGE_ROOT}/delivery_notes/{supplier_id}/{year}/{month:02d}/{uuid}.{ext}

STORAGE_ROOT 取自环境变量 DELIVERY_STORAGE_ROOT, 默认 ./storage。
"""
from __future__ import annotations

import os
import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import Optional


def get_root() -> Path:
    root = os.environ.get("DELIVERY_STORAGE_ROOT") or "./storage"
    return Path(root).resolve()


def folder_for(supplier_id: int, year: int, month: int) -> Path:
    return get_root() / "delivery_notes" / str(supplier_id) / str(year) / f"{month:02d}"


def save_upload(
    supplier_id: int,
    *,
    content: bytes,
    original_name: str,
    on_date: Optional[date] = None,
) -> dict:
    """落地一个上传图片, 返回 {file_path, year, month, original_name, size_bytes, mime_type}.

    on_date 不传时 = 今天 (上传日期); 用于按月归档。
    """
    today = on_date or date.today()
    folder = folder_for(supplier_id, today.year, today.month)
    folder.mkdir(parents=True, exist_ok=True)
    ext = Path(original_name).suffix.lower() or ".bin"
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf", ".bmp"):
        ext = ".bin"
    fname = f"{uuid.uuid4().hex}{ext}"
    full = folder / fname
    full.write_bytes(content)
    mime = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".webp": "image/webp",
        ".heic": "image/heic", ".pdf": "application/pdf",
        ".bmp": "image/bmp",
    }.get(ext, "application/octet-stream")
    return {
        "file_path": str(full),
        "year": today.year,
        "month": today.month,
        "original_name": original_name,
        "size_bytes": len(content),
        "mime_type": mime,
    }


def read(file_path: str) -> bytes:
    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(file_path)
    # 安全: 只允许读 STORAGE_ROOT 之下的文件
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


def folder_uri(supplier_id: int, year: int, month: int) -> str:
    """供前端展示用 — 仅相对路径, 实际访问走 API。"""
    return f"delivery_notes/{supplier_id}/{year}/{month:02d}"


def reset_root_for_tests(tmp: Path) -> None:
    """conftest 用: 临时 chdir + env 切换。"""
    os.environ["DELIVERY_STORAGE_ROOT"] = str(tmp)
