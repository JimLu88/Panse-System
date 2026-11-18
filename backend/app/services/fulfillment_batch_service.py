"""Prepare the user's official three-column template. Never contacts Taobao.

Workbook output is a surgical OOXML template fill in the production Python
service. All other ZIP members, styles, metadata and header bytes are retained.
Prepared/downloaded files are not platform success. Claims prevent another
batch from re-exporting the same order or parcel with an uncertain outcome.
"""
import hashlib
import io
import json
import re
from pathlib import Path
from datetime import date
from xml.sax.saxutils import escape
from zipfile import ZipFile

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.fulfillment_intake import FulfillmentBatch, FulfillmentIntake
from app.models.order import Order, OrderDetail
from app.services import order_service, order_flags
from app.services.order_line_delivery_service import line_is_refunded
from app.services.taobao_shipping import ShippingGate

TEMPLATE = Path(__file__).resolve().parents[1] / "assets/taobao-bulk-self-shipping.xlsx"
TEMPLATE_SHA256 = "fb86b967f57f4033beedfc6092cc26b829f03d1ecf8bc7cb688f694b6dca2faa"
MAX_ROWS = 200  # Application batch size, not a claimed Taobao limit.


def clean_fields(order_no, carrier, tracking_no):
    if not all(isinstance(v, str) for v in (order_no, carrier, tracking_no)):
        raise ShippingGate("订单号和运单号必须按文本提供，不能使用被Excel舍入的数字")
    values = tuple(v.strip() for v in (order_no, carrier, tracking_no))
    tid, company, tracking = values
    if not re.fullmatch(r"[0-9]{10,24}", tid):
        raise ShippingGate("请填写完整淘宝主交易单号")
    if not company or len(company) > 64 or any(ord(c) < 32 for c in company) or company[0] in "=+-@":
        raise ShippingGate("请填写淘宝显示的完整物流公司名称或编码")
    if company in {"中通", "德邦", "其他", "其它", "商家配送", "无需物流"}:
        raise ShippingGate("请明确选择快递或快运的完整名称；其他发货方式不能使用本模板")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{5,63}", tracking):
        raise ShippingGate("运单号格式不完整，请核对原始单号")
    return values


def workbook(rows):
    if not rows or len(rows) > MAX_ROWS:
        raise ShippingGate(f"每批需有1至{MAX_ROWS}个主订单")
    body = TEMPLATE.read_bytes()
    if hashlib.sha256(body).hexdigest() != TEMPLATE_SHA256:
        raise ShippingGate("官方发货模板发生变化，请先核验模板")
    data, orders, parcels = [], set(), set()
    for row in rows:
        tid, company, tracking = clean_fields(row["order_no"], row["carrier"], row["tracking_no"])
        parcel = (company, tracking.upper())
        if tid in orders or parcel in parcels:
            raise ShippingGate("本批含重复主订单或重复运单，请先核对")
        orders.add(tid)
        parcels.add(parcel)
        data.append((tid, company, tracking))
    output = io.BytesIO()
    with ZipFile(io.BytesIO(body)) as source, ZipFile(output, "w") as target:
        for member in source.infolist():
            content = source.read(member.filename)
            if member.filename == "xl/worksheets/sheet1.xml":
                sheet = content.decode("utf-8")
                generated = []
                for number, values in enumerate(data, 2):
                    cells = "".join(f'<c r="{col}{number}" s="{style}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
                                    for col, style, value in zip("ABC", (3, 4, 5), values))
                    generated.append(f'<row r="{number}">{cells}</row>')
                sheet = sheet.replace('</sheetData>', ''.join(generated) + '</sheetData>')
                sheet = sheet.replace('<dimension ref="A1"/>', f'<dimension ref="A1:C{len(data)+1}"/>')
                content = sheet.encode("utf-8")
            target.writestr(member, content)
    return output.getvalue()


def preview(db, order_no, carrier, tracking_no):
    order_no, carrier, tracking_no = clean_fields(order_no, carrier, tracking_no)
    order = db.scalar(select(Order).where(Order.order_no == order_no).with_for_update())
    if not order or order.platform not in ("淘宝", "taobao"):
        raise ShippingGate("未找到对应淘宝主订单，请先同步订单")
    if not order.shop:
        raise ShippingGate("订单缺少店铺身份，请先核实店铺")
    if (not order_service.is_in_factory_production(order) or order.is_refill
            or order.ship_date or order.tracking_no
            or (order.refund_status or "") not in ("", "无退款", "没有申请退款", "未申请退款")):
        raise ShippingGate("订单未付款、已发货或有退款，不能登记整单发货")
    if order_flags.has_shipping_delay(order) or order_flags.is_remote(order) or order_flags.waits_for_shipping_notice(order):
        raise ShippingGate("订单仍有延期或等通知安排，请先在订单中处理")
    children = list(db.scalars(select(OrderDetail).where(OrderDetail.order_no == order_no,
                OrderDetail.source == "import").order_by(OrderDetail.id)))
    if not children or any(not c.sub_order_no or line_is_refunded(c)
            or order_service.normalize_status(c.line_status) not in ("paid", "production")
            or not c.qty or c.qty <= 0 for c in children):
        raise ShippingGate("子单资料、数量、退款或发货状态不完整，请先核对")
    for item in [order, *children]:
        resume = order_flags.parse_resume_date(order_flags.order_text(item), order.order_date, date.today())
        if (order_flags.has_shipping_delay(item) or order_flags.is_remote(item)
                or order_flags.waits_for_shipping_notice(item) or (resume and resume > date.today())):
            raise ShippingGate("主单或子单仍有延期或等通知安排，请先在订单中处理")
    snapshot = {"order_no": order_no, "carrier": carrier, "tracking_no": tracking_no,
        "shop": order.shop, "status": order.status, "notes": order_flags.order_text(order).strip(),
        "lines": [{"id": c.id, "sub_order_no": c.sub_order_no, "factory_no": c.factory_no,
                   "product_code": c.product_code, "product_name": c.product_name,
                   "sku": c.sku_name, "sku_code": c.sku_code,
                   "qty": str(c.qty), "remark": c.remark or "", "status": c.line_status}
                  for c in children]}
    digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {**snapshot, "snapshot_sha256": digest,
            "has_notes": bool(snapshot["notes"] or any(c.remark for c in children))}


def register_manual(db, *, request_id, actor, order_no, carrier, tracking_no,
                    snapshot_sha256, all_goods_handed_over, notes_fulfilled):
    key = "erp:" + request_id
    old = db.get(FulfillmentIntake, key)
    if old:
        if (old.actor, old.order_no, old.carrier, old.tracking_no) != (actor, order_no.strip(), carrier.strip(), tracking_no.strip()):
            raise ShippingGate("登记请求编号已用于另一条记录")
        return old
    current = preview(db, order_no, carrier, tracking_no)
    if current["snapshot_sha256"] != snapshot_sha256:
        raise ShippingGate("订单资料已更新，请重新核对预览")
    if not all_goods_handed_over or (current["has_notes"] and not notes_fulfilled):
        raise ShippingGate("请确认全部商品已交运，且备注要求已落实")
    row = FulfillmentIntake(message_id=key, actor=actor, order_no=current["order_no"],
        sub_order_no=current["lines"][0]["sub_order_no"] if len(current["lines"]) == 1 else None,
        carrier=current["carrier"], tracking_no=current["tracking_no"], state="ready",
        detail="整单交运已确认，待生成官方批量表", review_evidence={"kind": "erp_full_order",
        "snapshot_sha256": snapshot_sha256, "all_goods_handed_over": True,
        "notes_fulfilled": notes_fulfilled, "line_ids": [c["id"] for c in current["lines"]]})
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        raise ShippingGate("该登记已经处理，请刷新列表")
    db.commit()
    return row


def prepare(db, *, request_id, actor, message_ids):
    if not message_ids or len(message_ids) > MAX_ROWS or len(set(message_ids)) != len(message_ids):
        raise ShippingGate(f"请选择1至{MAX_ROWS}条不同的待发货登记")
    existing = db.get(FulfillmentBatch, request_id)
    if existing:
        if existing.actor != actor or sorted(r["message_id"] for r in existing.rows) != sorted(message_ids):
            raise ShippingGate("该批次请求编号已被使用，请核对原批次")
        return existing
    records = list(db.scalars(select(FulfillmentIntake).where(
        FulfillmentIntake.message_id.in_(message_ids)).order_by(FulfillmentIntake.message_id).with_for_update()))
    if len(records) != len(message_ids):
        raise ShippingGate("部分登记已不存在，请刷新列表")
    rows = []
    for row in records:
        if row.state != "ready" or row.batch_id or row.order_claim or row.parcel_claim:
            raise ShippingGate("包含待核对、已出表或结果待核的记录，请取消这些选择")
        evidence = row.review_evidence or {}
        current = preview(db, row.order_no, row.carrier, row.tracking_no)
        if evidence.get("kind") == "original_image":
            from app.services.fulfillment_intake import _match
            _match(db, row)
        elif evidence.get("kind") != "erp_full_order":
            raise ShippingGate("缺少整单交运的核对记录")
        if current["snapshot_sha256"] != evidence.get("snapshot_sha256"):
            raise ShippingGate("订单资料在登记后发生变化，请重新核对，未生成新表")
        rows.append({"message_id": row.message_id, "order_no": row.order_no,
                     "carrier": row.carrier, "tracking_no": row.tracking_no,
                     "shop": current["shop"],
                     "snapshot_sha256": current["snapshot_sha256"]})
    if len({row["shop"] for row in rows}) != 1:
        raise ShippingGate("不同店铺的订单不能放入同一发货表")
    content = workbook(rows)
    batch = FulfillmentBatch(id=request_id, actor=actor, state="prepared", rows=rows,
                             file_bytes=content, file_sha256=hashlib.sha256(content).hexdigest())
    try:
        with db.begin_nested():
            db.add(batch)
            for row in records:
                row.order_claim = row.order_no
                row.parcel_claim = row.carrier + "/" + row.tracking_no.upper()
                row.batch_id, row.state = request_id, "batch_prepared"
                row.detail = "官方批量表已生成，尚未取得淘宝发货结果"
            db.flush()
    except IntegrityError:
        raise ShippingGate("订单或运单已在其他批次中，请核对原文件，不能重复出表")
    db.commit()
    return batch
