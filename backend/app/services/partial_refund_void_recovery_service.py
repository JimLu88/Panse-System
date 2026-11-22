"""Explicit, audited correction of partial-refund void notices; never resends orders."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.order import OrderDetail
from app.services import feishu_client, settings_service
from app.services.order_line_delivery_service import line_is_refunded, line_is_fully_refunded

RETRACTED_KIND = "order_sheet_void_retracted"
KEY = "partial_refund_correction_v1"


def _message(db, message_id):
    items = feishu_client._req(db, "GET", f"{feishu_client._BASE}/im/v1/messages/{message_id}").get("items", [])
    if len(items) != 1 or items[0].get("deleted"):
        raise RuntimeError("Message readback missing or deleted")
    return items[0]


def _save(db, record, correction):
    record.row_summary = dict(record.row_summary or {}, **{KEY: dict(correction)})
    db.commit()


def correct_partial_refund_void(db: Session, archive_id: int, *, apply: bool = False, historical_only: bool = False) -> dict:
    """Run only for an explicitly reviewed archive. Unknown sends cannot replay."""
    record = db.scalar(select(ImportedFile).where(ImportedFile.id == archive_id).with_for_update())
    if not record or record.kind not in {"order_sheet_void", RETRACTED_KIND}:
        raise ValueError("Not a void archive")
    summary = record.row_summary or {}
    correction = dict(summary.get(KEY) or {})
    line = db.get(OrderDetail, summary.get("line_id"))
    if (not line or line.sub_order_no != summary.get("sub_order_no")
            or line.order_no != summary.get("order_no")
            or not summary.get("pushed")
            or line_is_refunded(line)
            or not (0 < Decimal(str(line.refund_amount or 0)) < Decimal(str(line.amount or 0)))):
        raise ValueError("Not a verified active partial-refund child with a sent void notice")
    later_full = line_is_fully_refunded(line, db=db)
    if later_full != historical_only:
        raise ValueError("Later full refund must remain void; historical correction requires explicit scope")
    if historical_only:
        later_voids = db.scalars(select(ImportedFile).where(
            ImportedFile.kind == "order_sheet_void", ImportedFile.id != record.id,
            ImportedFile.created_at > record.created_at)).all()
        if not any((r.row_summary or {}).get("order_no") == line.order_no for r in later_voids):
            raise ValueError("Missing separate later full-refund void evidence")
    label = f"畔色{line.factory_no}单"
    historical = line.line_status == "signed"
    text = (f"【作废通知更正】{label}\n订单：{line.order_no}\n"
            f"该单仅部分退款 ¥{line.refund_amount:.2f}，并非全额退款。此前作废通知是系统误判，现予以更正，原订单仍有效。\n"
            + ("该单已签收，本次仅更正历史记录，无需重新制作或发货。" if historical else
               "此前通知中的“停止制作、不要发货”不适用于本单，请按原订单及既有生产、交运安排执行。")
            + "\n本消息不是新增下单，请勿重复制作、重复发货。")
    if historical_only:
        text = (f"【历史作废通知更正】{label}\n订单：{line.order_no}\n"
                f"早期只退差价 ¥{line.refund_amount:.2f} 时发送作废通知，属于系统误判。"
                "但此后该单又退回了剩余实付货款，财务流水已确认全额退回。\n"
                "本次仅说明早期通知错误，后续全额退款撤单继续保留，不能恢复生产或发货。"
                "\n本消息不是新增下单，请勿重复制作、重复发货。")
    card = {"header": {"template": "green", "title": {"tag": "plain_text", "content": f"作废已更正｜{label}"}},
            "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": text}}]}
    if not apply:
        db.rollback()
        return {"archive_id": archive_id, "factory_no": line.factory_no, "text": text, "correction": correction}
    if os.environ.get("PANSE_DISABLE_NOTIFY"):
        raise RuntimeError("Notifications disabled")
    if correction.get("state") == "verified":
        db.rollback()
        return correction
    if correction.get("state") in {"prepared", "unknown"}:
        db.rollback()
        raise RuntimeError("Prior correction result unknown; reconcile original intent, never resend")
    original_id = summary.get("delivery_message_id")
    if not correction:
        chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
        if original_id:
            original = _message(db, original_id)
            chat_id = original.get("chat_id")
        if not chat_id:
            raise ValueError("No verified destination")
        correction = {"state": "prepared", "at": datetime.now(timezone.utc).isoformat(),
                      "original_kind": record.kind, "original_message_id": original_id,
                      "chat_id": chat_id, "text": text, "factory_no": line.factory_no,
                      "refund_amount": str(line.refund_amount), "amount": str(line.amount), "historical_only": historical_only,
                      "uuid": str(uuid5(NAMESPACE_URL, f"panse:partial-refund-void:{archive_id}"))}
        record.kind = record.kind if historical_only else RETRACTED_KIND
        _save(db, record, correction)  # Persist intent and retain original artifact/history.
        try:
            if original_id:
                try:
                    feishu_client.patch_card(db, original_id, card)
                    original = _message(db, original_id)
                    content = (original.get("body") or {}).get("content", "")
                    correction["original_card_updated"] = "作废已更正" in content and line.order_no in content
                except feishu_client.FeishuError as exc:
                    # A new correction is still required if the old card is no longer editable.
                    correction["original_card_updated"] = False
                    correction["original_card_error_code"] = exc.code
                _save(db, record, correction)
            receipt = feishu_client._req(db, "POST", f"{feishu_client._BASE}/im/v1/messages?receive_id_type=chat_id",
                json={"receive_id": chat_id, "msg_type": "interactive", "content": json.dumps(card, ensure_ascii=False),
                      "uuid": correction["uuid"]})
            if not receipt.get("message_id"):
                raise RuntimeError("Correction has no message receipt")
            correction.update(state="sent", message_id=receipt["message_id"])
            _save(db, record, correction)
        except Exception:
            correction["state"] = "unknown"
            _save(db, record, correction)
            raise
    # A readback failure after a known send is resumable without another POST.
    message = _message(db, correction["message_id"])
    content = (message.get("body") or {}).get("content", "")
    if (message.get("chat_id") != correction["chat_id"] or line.order_no not in content
            or "请勿重复制作" not in content or "作废已更正" not in content):
        raise RuntimeError("Correction content/destination readback mismatch")
    correction.update(state="verified", verified_at=datetime.now(timezone.utc).isoformat())
    _save(db, record, correction)
    return correction
