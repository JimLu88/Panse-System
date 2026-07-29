"""工厂出厂复检图库：按订单、产品和日期归档原图。"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.order import Order
from app.services import import_storage, order_flags

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def find_order(
    db: Session,
    *,
    order_no: Optional[str] = None,
    factory_no: Optional[int] = None,
) -> Optional[Order]:
    if order_no:
        return db.execute(
            select(Order).where(Order.order_no == str(order_no).strip())
        ).scalar_one_or_none()
    if factory_no is not None:
        return db.execute(
            select(Order).where(Order.factory_no == int(factory_no))
        ).scalar_one_or_none()
    return None


def archive_image(
    db: Session,
    *,
    order: Order,
    content: bytes,
    original_name: str,
    source: str,
    uploaded_by: Optional[str],
    captured_on: Optional[date] = None,
) -> ImportedFile:
    suffix = Path(original_name or "").suffix.lower()
    if suffix not in _IMAGE_EXT:
        raise ValueError("只支持 JPG、PNG、WEBP、GIF、BMP 图片")
    if not content:
        raise ValueError("图片为空")
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("单张图片不能超过 20MB")
    on_date = captured_on or date.today()
    summary = {
        "order_id": order.id,
        "order_no": order.order_no,
        "factory_no": order.factory_no,
        "factory_label": order_flags.factory_label(order),
        "product_code": order.product_code,
        "product_name": order.product_name,
        "sku_code": order.sku_code,
        "sku": order.sku,
        "captured_on": on_date.isoformat(),
        "source": source,
    }
    archived = import_storage.archive(
        db,
        content=content,
        original_name=original_name,
        kind="factory_inspection",
        source=source,
        uploaded_by=uploaded_by,
        row_summary=summary,
        on_date=on_date,
    )
    return archived.file


def list_images(
    db: Session,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    product: Optional[str] = None,
    order_no: Optional[str] = None,
    factory_no: Optional[int] = None,
    limit: int = 500,
) -> list[dict]:
    rows = db.execute(
        select(ImportedFile)
        .where(ImportedFile.kind == "factory_inspection")
        .order_by(ImportedFile.created_at.desc(), ImportedFile.id.desc())
        .limit(min(max(limit, 1), 2000))
    ).scalars().all()
    needle = (product or "").strip().lower()
    out: list[dict] = []
    for row in rows:
        meta = row.row_summary or {}
        try:
            captured = date.fromisoformat(str(meta.get("captured_on") or "")[:10])
        except ValueError:
            captured = row.created_at.date() if row.created_at else None
        if date_from and (captured is None or captured < date_from):
            continue
        if date_to and (captured is None or captured > date_to):
            continue
        if order_no and str(meta.get("order_no") or "") != str(order_no):
            continue
        if factory_no is not None and int(meta.get("factory_no") or 0) != int(factory_no):
            continue
        if needle:
            hay = " ".join(str(meta.get(k) or "") for k in (
                "product_code", "product_name", "sku_code", "sku",
            )).lower()
            if needle not in hay:
                continue
        out.append({
            "id": row.id,
            "order_id": meta.get("order_id"),
            "order_no": meta.get("order_no"),
            "factory_no": meta.get("factory_no"),
            "factory_label": meta.get("factory_label"),
            "product_code": meta.get("product_code"),
            "product_name": meta.get("product_name"),
            "sku_code": meta.get("sku_code"),
            "sku": meta.get("sku"),
            "captured_on": captured.isoformat() if captured else None,
            "uploaded_by": row.uploaded_by,
            "source": row.source,
            "original_filename": row.original_filename,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        })
    return out
