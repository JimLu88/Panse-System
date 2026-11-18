"""Bind one Feishu message and one archived factory sheet to a shipping registration."""
import hashlib
import os
import re

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.models.fulfillment_intake import FulfillmentIntake
from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail
from app.services import feishu_client, order_flags, order_service, settings_service
from app.services.order_line_delivery_service import audit_sent_line_content, line_is_refunded
from app.services.taobao_shipping import ShippingGate, TaobaoShipping


FORMAT = "请将工厂下单原图与「发货登记 整单单包已发 物流公司 运单号」放在同一条图文。"
COMMAND = re.compile(r"发货登记\s+整单单包已发\s+([^\s,，:：]{2,32})\s+([A-Za-z0-9][A-Za-z0-9-]{5,63})")
PROTECTED = ("submitting", "unknown", "confirmed")


def _match(db, row):
    candidates = db.scalars(select(ImportedFile).where(
        ImportedFile.kind == "order_sheet_sent", ImportedFile.file_hash == row.image_hash)).all()
    valid = [item for item in candidates if (item.row_summary or {}).get("pushed") is True
             and not item.row_summary.get("delivery_superseded")]
    identities = {(item.row_summary.get("order_no"), item.row_summary.get("sub_order_no"),
                   item.row_summary.get("line_id")) for item in valid}
    if len(identities) != 1:
        raise ShippingGate("图片未唯一匹配工厂已发原图，请核对是否为转发原图")
    order_no, sub_no, line_id = identities.pop()
    if not order_no or not sub_no or not line_id:
        raise ShippingGate("下单图缺少子单记录")
    row.order_no, row.sub_order_no = order_no, sub_no
    order = db.scalar(select(Order).where(Order.order_no == order_no).with_for_update())
    line = db.get(OrderDetail, line_id)
    if (not order or not line or line.order_no != order_no or line.sub_order_no != sub_no
            or order.platform not in ("淘宝", "taobao") or not re.fullmatch(r"\d{10,24}", order_no)
            or not order_service.is_in_factory_production(order) or order.is_refill
            or order.ship_date or order.tracking_no or (order.platform_field_state or {}).get("shipping_only")
            or (order.refund_status or "") not in ("", "无退款", "没有申请退款", "未申请退款")
            or line_is_refunded(line) or order_service.normalize_status(line.line_status) not in ("paid", "production")):
        raise ShippingGate("订单、退款或已发货状态需核对")
    children = list(db.scalars(select(OrderDetail.id).where(OrderDetail.order_no == order_no,
                                                              OrderDetail.source == "import")))
    if children != [line.id] or line.qty != 1 or order.qty != 1 or not line.factory_delivery_required:
        raise ShippingGate("涉及多件、拆包或子单范围不清，请按包裹人工核对")
    if (order_flags.has_shipping_delay(order) or order_flags.is_remote(order)
            or order_flags.waits_for_shipping_notice(order)
            or order_flags.order_text(order).strip() or (line.remark or "").strip()):
        raise ShippingGate("订单有备注或延期安排，需核实拍照、通知等发货条件")
    latest = list(db.scalars(select(ImportedFile).where(
        ImportedFile.kind.in_(("order_sheet_sent", "order_sheet_void")),
        ImportedFile.row_summary["order_no"].as_string() == order_no
    ).order_by(ImportedFile.id.desc())))
    if any(item.kind == "order_sheet_void" and (item.row_summary or {}).get("sub_order_no") in (None, "", sub_no)
           for item in latest):
        raise ShippingGate("订单存在工厂作废记录")
    for item in latest:
        summary = item.row_summary or {}
        if summary.get("sub_order_no") == sub_no and summary.get("pushed") is True and not summary.get("delivery_superseded"):
            if item.file_hash != row.image_hash or summary.get("factory_no_at_render") != line.factory_no:
                raise ShippingGate("工厂下单图已有新版本")
            audit = audit_sent_line_content([line], {sub_no: item})
            if audit["unverified"] or audit["mismatches"] or (summary.get("rendered_line") or {}).get("qty") != 1:
                raise ShippingGate("工厂图的规格或实物数量需核实")
            break
    else:
        raise ShippingGate("当前工厂图无法核实")
    return order


def _readback(db, row, client):
    try:
        confirmed = client.confirmed(row)
    except Exception:
        confirmed = False
    result = "confirmed" if confirmed else "unknown"
    detail = "淘宝已回读相同订单、承运商和运单号" if confirmed else "淘宝结果待核，仅查原单"
    db.execute(update(FulfillmentIntake).where(FulfillmentIntake.message_id == row.message_id,
        FulfillmentIntake.state.in_(("submitting", "unknown"))).values(
            state=result, detail=detail, readbacks=FulfillmentIntake.readbacks + 1))
    db.commit()
    db.refresh(row)


def reconcile_pending(db):
    rows = list(db.scalars(select(FulfillmentIntake).where(
        FulfillmentIntake.state.in_(("submitting", "unknown")), FulfillmentIntake.readbacks < 4).order_by(
            FulfillmentIntake.updated_at, FulfillmentIntake.message_id).limit(20)))
    if rows:
        client = TaobaoShipping()
        for row in rows:
            _readback(db, row, client)
    return {"checked": len(rows)}


def _process(db, row, match, keys):
    if not match or len(keys) != 1:
        raise ShippingGate(FORMAT)
    row.carrier, row.tracking_no = match.groups()
    row.image_key = keys[0]
    image = feishu_client.download_message_resource(db, row.message_id, row.image_key)
    row.image_hash = hashlib.sha256(image).hexdigest()
    if not db.scalar(select(ImportedFile.id).where(ImportedFile.kind == "order_sheet_sent",
                                                   ImportedFile.file_hash == row.image_hash).limit(1)):
        # A re-encoded screenshot can identify a candidate for staff, but cannot release a shipment.
        from app.services.vision_ocr_service import read_factory_shipping_identity
        try:
            identity = read_factory_shipping_identity(db, image)
        except Exception:
            identity = {}
        candidate = db.scalar(select(OrderDetail).where(
            OrderDetail.source == "import", OrderDetail.sub_order_no == identity.get("sub_order_no"),
            OrderDetail.factory_no == identity.get("factory_no"))) if identity else None
        if candidate:
            row.order_no, row.sub_order_no = candidate.order_no, candidate.sub_order_no
            raise ShippingGate(f"图片已识别为畔色{candidate.factory_no}单，请核对原图后提交发货")
        raise ShippingGate("图片未匹配工厂原图，请核对下单图和工厂单号")
    order = _match(db, row)
    if os.getenv("PANSE_TOP_SHIPPING_ENABLED") != "1":
        from app.services.fulfillment_batch_service import preview
        snapshot = preview(db, row.order_no, row.carrier, row.tracking_no)
        row.review_evidence = {"kind": "original_image", "snapshot_sha256": snapshot["snapshot_sha256"]}
        row.state, row.detail = "ready", "已匹配工厂原图与整单运单，待生成官方批量表"
        return
    client = TaobaoShipping()
    if order.shop != client.shop or not client.exact_trade(row, "WAIT_SELLER_SEND_GOODS"):
        raise ShippingGate("淘宝店铺、子单、数量或待发货状态需核实")
    code = client.carrier_code(row.carrier)
    # Persist the irreversible boundary BEFORE the only external write. A worker
    # interrupted anywhere after this commit may only read the official record.
    try:
        with db.begin_nested():
            row.order_claim = row.order_no
            row.parcel_claim = row.carrier + "/" + row.tracking_no.upper()
            row.state, row.detail = "submitting", "等待淘宝回读"
            db.flush()
    except IntegrityError:
        raise ShippingGate("订单或运单已有登记，请核对原记录")
    db.commit()
    try:
        client.send(row, code)
    except Exception:
        pass  # Includes a lost acknowledgement; issue read-only reconciliation.
    _readback(db, row, client)


def register(db, event, text, keys):
    if not re.match(r"^发货登记(?:\s|[：:]|$)", text):
        return None
    msg = event.get("message") or {}
    mid = msg.get("message_id")
    chat = settings_service.get(db, "feishu_shipping_intake_chat_id", env_fallback=False)
    if not chat:
        chat = settings_service.get(db, "feishu_alert_chat_id", env_fallback=False)
    if not (mid and event.get("_factory_forward_trusted") is True and chat and msg.get("chat_id") == chat):
        return {"kind": "shipping_registration", "state": "unbound"}
    sender = event.get("sender") or {}
    actor = (sender.get("sender_id") or {}).get("open_id")
    allowed = (settings_service.get(db, "feishu_shipping_operator_ids", env_fallback=False) or "").split(",")
    enabled = os.getenv("PANSE_TOP_SHIPPING_ENABLED") == "1"
    if (sender.get("sender_type") != "user" or not actor
            or ((enabled or any(allowed)) and actor not in {v.strip() for v in allowed})):
        return {"kind": "shipping_registration", "state": "unauthorized"}
    row = FulfillmentIntake(message_id=mid, actor=actor, state="received", detail="已接收")
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        existing = db.get(FulfillmentIntake, mid)
        if existing is None:
            raise
        return {"kind": "shipping_registration", "state": existing.state, "duplicate": True}
    try:
        _process(db, row, COMMAND.fullmatch(text), keys)
    except ShippingGate as exc:
        row.state, row.detail = "review", str(exc)
    except Exception:
        db.rollback()
        # If the write boundary has already committed, never replace it with review.
        row = db.get(FulfillmentIntake, mid) or row
        if row.state == "confirmed":
            pass
        elif row.state in PROTECTED:
            row.state, row.detail = "unknown", "淘宝结果待核，仅查原单"
        else:
            row.state, row.detail = "review", "读取图片或核对平台失败，请核对登记"
            db.add(row)
    db.commit()
    return {"kind": "shipping_registration", "state": row.state, "detail": row.detail}
