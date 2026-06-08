"""送货单创建 — 落盘原图 + OCR 解析 + 入主表/明细 + 自动匹配。

从 api/suppliers.py 的上传处理抽出, 供 API 上传 与 飞书机器人(供应商送货单)共用,
避免两处各写一份入库逻辑。返回 DeliveryNote(已 flush, 由调用方决定 commit)。
"""
from __future__ import annotations

import logging
from datetime import date as _date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.supplier import DeliveryFile, DeliveryNote, DeliveryNoteLine, Supplier
from app.services import delivery_matcher, delivery_storage, ocr_service

_log = logging.getLogger("panse.delivery_note")


def create_from_image(
    db: Session, *, supplier: Supplier, content: bytes,
    mime: Optional[str] = None, original_name: str = "upload.jpg",
    uploaded_by: Optional[str] = None, on_date: Optional[_date] = None,
) -> DeliveryNote:
    """落盘 → OCR → DeliveryNote(+lines+match)。OCR 未配置/解析失败时返回 pending_review 空单。"""
    saved = delivery_storage.save_upload(
        supplier.id, content=content, original_name=original_name, on_date=on_date,
    )
    df = DeliveryFile(
        supplier_id=supplier.id,
        year=saved["year"], month=saved["month"],
        file_path=saved["file_path"], original_name=saved["original_name"],
        mime_type=saved["mime_type"], size_bytes=saved["size_bytes"],
        uploaded_by=uploaded_by,
    )
    db.add(df)
    db.flush()

    try:
        parsed = ocr_service.ocr_delivery_note(
            db, image_bytes=content, mime=saved["mime_type"] or mime or "image/jpeg",
            supplier_name=supplier.name, supplier_type=supplier.supplier_type,
        )
    except (ocr_service.OcrUnavailable, ocr_service.OcrParseError) as e:
        note = DeliveryNote(
            supplier_id=supplier.id, source_file_id=df.id,
            status="pending_review", ocr_warnings=[f"OCR 失败: {e}"],
        )
        db.add(note)
        db.flush()
        return note

    note = DeliveryNote(
        supplier_id=supplier.id, source_file_id=df.id,
        note_no=parsed.note_no, delivery_date=parsed.delivery_date,
        total_amount=parsed.total_amount, ocr_model=parsed.model,
        ocr_warnings=parsed.warnings, ocr_confidence=parsed.confidence,
        status="pending_review",
    )
    db.add(note)
    db.flush()

    for pl in parsed.lines:
        line = DeliveryNoteLine(
            delivery_note_id=note.id, line_no=pl.line_no,
            item_name=pl.item_name, spec=pl.spec, unit=pl.unit,
            qty=pl.qty, unit_price=pl.unit_price, amount=pl.amount,
            ocr_raw_text=pl.raw_text, ocr_warnings=pl.warnings,
        )
        try:
            candidates = delivery_matcher.match_line(
                db, item_name=pl.item_name, spec=pl.spec, qty=pl.qty,
                delivery_date=parsed.delivery_date,
            )
            delivery_matcher.apply_candidates_to_line(line, candidates)
        except Exception as e:  # pragma: no cover
            line.match_method = "error"
            line.match_candidates = []
            line.remark = f"匹配失败: {e}"
        db.add(line)
    db.flush()
    return note
