"""导入原始文件归档 — 把每次导入的 表格/图片 原文件按 类型/年/月 落盘 + 登记 ImportedFile。

布局: {STORAGE_ROOT}/imports/{kind}/{year}/{month:02d}/{uuid}.{ext}
复用 delivery_storage.get_root() (环境变量 DELIVERY_STORAGE_ROOT, 默认 ./storage)。

archive(): 落盘 + 登记一条 ImportedFile (不 commit, 交调用方)。同 hash 文件不重复占盘,
但仍登记一行 (每次上传都留痕), 并返回 is_duplicate 供调用方提示。
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.services.delivery_storage import get_root

KINDS = (
    "orders", "taobao", "alipay", "settlement", "wanshifu", "logistics",
    "promotion", "aftersales", "refill", "account_balance",
    "factory_recon", "purchase", "screenshot", "generic",
)


def folder_for(kind: str, year: int, month: int) -> Path:
    safe_kind = kind if kind in KINDS else "generic"
    return get_root() / "imports" / safe_kind / str(year) / f"{month:02d}"


@dataclass
class ArchiveResult:
    file: ImportedFile
    is_duplicate: bool


def archive(
    db: Session,
    *,
    content: bytes,
    original_name: str,
    kind: str,
    source: str = "web",
    uploaded_by: Optional[str] = None,
    row_summary: Optional[dict] = None,
    import_job_id: Optional[int] = None,
    on_date: Optional[date] = None,
) -> ArchiveResult:
    """归档一份导入原文件。返回 ArchiveResult(file, is_duplicate)。不 commit。"""
    file_hash = hashlib.sha256(content).hexdigest()
    prior = db.execute(
        select(ImportedFile).where(ImportedFile.file_hash == file_hash).order_by(ImportedFile.id.desc())
    ).scalars().first()

    if prior is not None:
        stored_path = prior.stored_path  # 同文件复用磁盘, 不重复占盘
        is_dup = True
    else:
        today = on_date or date.today()
        folder = folder_for(kind, today.year, today.month)
        folder.mkdir(parents=True, exist_ok=True)
        ext = Path(original_name or "").suffix.lower() or ".bin"
        full = folder / f"{uuid.uuid4().hex}{ext}"
        full.write_bytes(content)
        stored_path = str(full)
        is_dup = False

    rec = ImportedFile(
        kind=kind if kind in KINDS else "generic",
        original_filename=original_name,
        stored_path=stored_path,
        file_hash=file_hash,
        size_bytes=len(content),
        source=source,
        uploaded_by=uploaded_by,
        row_summary=row_summary,
        import_job_id=import_job_id,
    )
    db.add(rec)
    db.flush()
    return ArchiveResult(file=rec, is_duplicate=is_dup)


def update_summary(db: Session, file_id: int, row_summary: dict) -> None:
    """导入解析完成后回填结果摘要 (inserted/updated/...)。不 commit。"""
    rec = db.get(ImportedFile, file_id)
    if rec is not None:
        rec.row_summary = row_summary
        db.flush()


def read(stored_path: str) -> bytes:
    p = Path(stored_path)
    if not p.is_file():
        raise FileNotFoundError(stored_path)
    try:
        p.resolve().relative_to(get_root())
    except ValueError as e:
        raise PermissionError(f"路径越权: {stored_path}") from e
    return p.read_bytes()
