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
    "orders", "taobao", "alipay", "settlement", "wanshifu", "wanshifu_orders",
    "logistics", "promotion", "aftersales", "refill", "account_balance",
    "factory_recon", "purchase", "screenshot",
    # 系统生成档案 (2026-06-11 用户需求: 下单图/作废图/页面导出 单独分类入口)
    "order_sheet", "order_sheet_void", "page_export",
    # 全类目 Excel 导出 (2026-06-12 用户需求: 资料存档库留存, 超30份轮转)
    "full_export",
    "generic",
)


# 物理文件夹中文名 (用户拍板 2026-06-19: 直接浏览文件时能看懂)。
# 注意: DB 里的 ImportedFile.kind 仍是英文 key (所有 kind== 查询不受影响), 只有落盘文件夹改中文。
_KIND_FOLDER = {
    "orders": "订单", "taobao": "淘宝", "alipay": "支付宝", "settlement": "淘宝结算",
    "wanshifu": "万师傅", "wanshifu_orders": "万师傅订单", "logistics": "物流",
    "promotion": "大促", "aftersales": "售后", "refill": "补单",
    "account_balance": "账户余额", "factory_recon": "工厂对账", "purchase": "采购",
    "screenshot": "截图", "order_sheet": "工厂下单图", "order_sheet_void": "作废下单图",
    "page_export": "页面导出", "full_export": "全量导出", "generic": "其他",
}


def folder_name_for(kind: str) -> str:
    """kind → 物理文件夹名 (中文); 未知 kind 归「其他」。"""
    safe_kind = kind if kind in KINDS else "generic"
    return _KIND_FOLDER.get(safe_kind, safe_kind)


def folder_for(kind: str, year: int, month: int) -> Path:
    return get_root() / "imports" / folder_name_for(kind) / str(year) / f"{month:02d}"


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


def delete_record(db: Session, file_id: int) -> bool:
    """删除一条归档记录; 物理文件仅在没有其他记录共享同一路径时才删 (hash 去重共盘)。

    用途: 退款作废 — 作废图生成后删掉原下单图 (用户拍板 2026-06-11)。不 commit。
    """
    rec = db.get(ImportedFile, file_id)
    if rec is None:
        return False
    path = rec.stored_path
    db.delete(rec)
    db.flush()
    still_used = db.execute(
        select(ImportedFile).where(ImportedFile.stored_path == path)
    ).scalars().first()
    if still_used is None:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 文件已不在等
            pass
    return True


def read(stored_path: str) -> bytes:
    p = Path(stored_path)
    if not p.is_file():
        raise FileNotFoundError(stored_path)
    try:
        p.resolve().relative_to(get_root())
    except ValueError as e:
        raise PermissionError(f"路径越权: {stored_path}") from e
    return p.read_bytes()
