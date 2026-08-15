"""淘宝子订单级工厂制单、退款作废与送达数量安全门。"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail


_NOT_REFUND = ("没有申请退款", "未申请退款", "无退款", "退款关闭", "退款失败", "撤销退款", "买家撤销")


def line_is_refunded(line: OrderDetail) -> bool:
    status = str(line.line_status or "").lower()
    refund_status = str(line.refund_status or "")
    keyword = (
        any(key in refund_status for key in ("退款", "退货", "关闭"))
        and not any(key in refund_status for key in _NOT_REFUND)
    )
    return status in {"cancelled", "aftersales"} or keyword or Decimal(str(line.refund_amount or 0)) > 0


def physical_lines(
    db: Session,
    order_no: str | None = None,
    *,
    required_only: bool = True,
) -> list[OrderDetail]:
    query = select(OrderDetail).where(
        OrderDetail.source == "import",
        OrderDetail.sub_order_no.isnot(None),
    )
    if required_only:
        query = query.where(OrderDetail.factory_delivery_required.is_(True))
    if order_no:
        query = query.where(OrderDetail.order_no == order_no)
    return db.execute(query.order_by(OrderDetail.id.asc())).scalars().all()


def line_is_factory_eligible(db: Session, line: OrderDetail, order: Order | None) -> bool:
    if order is None or order.is_refill or (order.status or "") not in {"paid", "production"}:
        return False
    if line_is_refunded(line):
        return False
    text = f"{line.product_name or ''} {line.sku_name or ''}"
    if any(key in text for key in ("样块", "样品", "小样", "样木")):
        return False
    from app.services import order_flags, order_sheet_archive_service
    if order_flags.is_remote(order):
        return False
    topup, _reason = order_sheet_archive_service._is_parts_topup(db, order)
    return not topup


def active_lines(db: Session, order_no: str | None = None) -> list[OrderDetail]:
    """当前仍需工厂处理的有效商品行（已付/生产中，非退款）。"""
    lines = physical_lines(db, order_no)
    order_nos = {line.order_no for line in lines if line.order_no}
    orders = {
        row.order_no: row
        for row in db.execute(select(Order).where(Order.order_no.in_(order_nos))).scalars().all()
    } if order_nos else {}
    return [
        line for line in lines
        if line_is_factory_eligible(db, line, orders.get(line.order_no))
    ]


def expected_active_lines(db: Session) -> list[OrderDetail]:
    """所有已解析子订单中的有效实体商品，不受迁移开关影响。

    这是全局数量安全门的分母：历史漏绑定也必须被看见并报警。
    """
    lines = physical_lines(db, required_only=False)
    order_nos = {line.order_no for line in lines if line.order_no}
    orders = {
        row.order_no: row
        for row in db.execute(select(Order).where(Order.order_no.in_(order_nos))).scalars().all()
    } if order_nos else {}
    return [
        line for line in lines
        if line_is_factory_eligible(db, line, orders.get(line.order_no))
    ]


def next_factory_no(db: Session) -> int:
    order_max = db.execute(select(func.max(Order.factory_no))).scalar() or 241
    line_max = db.execute(select(func.max(OrderDetail.factory_no))).scalar() or 241
    return max(int(order_max), int(line_max)) + 1


def sent_line_evidence(db: Session) -> dict[str, ImportedFile]:
    """可信实际发送图，按淘宝子订单号唯一归档。"""
    rows = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet_sent")
        .order_by(ImportedFile.id.asc())
    ).scalars().all()
    out: dict[str, ImportedFile] = {}
    for row in rows:
        summary = row.row_summary or {}
        if summary.get("delivery_superseded") is True:
            continue
        sub_order_no = str(summary.get("sub_order_no") or "").strip()
        if sub_order_no and summary.get("pushed") is True:
            out[sub_order_no] = row
    return out


def void_line_evidence(db: Session) -> dict[str, ImportedFile]:
    rows = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet_void")
        .order_by(ImportedFile.id.asc())
    ).scalars().all()
    out: dict[str, ImportedFile] = {}
    for row in rows:
        sub_order_no = str((row.row_summary or {}).get("sub_order_no") or "").strip()
        if sub_order_no:
            out[sub_order_no] = row
    return out


def delivery_count_gate(db: Session) -> dict:
    """有效实体商品数必须等于已发送且仍有效的子订单工厂图数。"""
    # 只核对已进入子订单工厂链的商品。历史主订单证据先由
    # bind_unambiguous_legacy_evidence 安全迁移；没有可信对应关系的旧记录不盲推。
    active = active_lines(db)
    sent = sent_line_evidence(db)
    voided = void_line_evidence(db)
    active_ids = {str(line.sub_order_no) for line in active if line.sub_order_no}
    sent_ids = active_ids & set(sent)
    missing = sorted(active_ids - sent_ids)
    refunded_sent = {
        str(line.sub_order_no)
        for line in physical_lines(db, required_only=False)
        if line.sub_order_no and line_is_refunded(line) and str(line.sub_order_no) in sent
    }
    unvoided_refunds = sorted(refunded_sent - set(voided))
    extra = sorted(set(sent) - active_ids - refunded_sent)
    ok = len(active_ids) == len(sent_ids) and not missing and not unvoided_refunds
    return {
        "ok": ok,
        "active_product_count": len(active_ids),
        "sent_factory_sheet_count": len(sent_ids),
        "missing_sub_order_nos": missing,
        "extra_sent_sub_order_nos": extra,
        "unvoided_refunded_sub_order_nos": unvoided_refunds,
    }


def bind_unambiguous_legacy_evidence(db: Session) -> dict:
    """一张旧主单图仅在恰好对应一个未退款实体行时自动绑定。

    多个有效商品时绝不猜测、绝不重发，由安全门报警。
    """
    rows = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet_sent")
        .order_by(ImportedFile.id.asc())
    ).scalars().all()
    bound: list[str] = []
    ambiguous: list[str] = []
    factory_no_conflicts: list[dict] = []
    for evidence in rows:
        summary = evidence.row_summary or {}
        if (
            summary.get("pushed") is not True
            or summary.get("delivery_superseded") is True
            or summary.get("sub_order_no")
        ):
            continue
        order_no = str(summary.get("order_no") or "").strip()
        if not order_no:
            continue
        parsed_lines = db.execute(
            select(OrderDetail).where(
                OrderDetail.order_no == order_no,
                OrderDetail.source == "import",
                OrderDetail.sub_order_no.isnot(None),
                OrderDetail.sku_code.isnot(None),
            ).order_by(OrderDetail.id.asc())
        ).scalars().all()
        all_candidates = [line for line in parsed_lines if not line_is_refunded(line)]
        order = db.execute(select(Order).where(Order.order_no == order_no)).scalar_one_or_none()
        candidates = all_candidates
        # 多商品或含退款兄弟行时，不能仅因“只剩一个有效商品”就把旧图绑定
        # 给它。旧图实际渲染的是 Order 代表商品，必须与有效行 SKU/产品精确匹配。
        requires_exact = len(parsed_lines) > 1 or any(line_is_refunded(x) for x in parsed_lines)
        if requires_exact and order is not None:
            # 旧主单图实际使用的是 Order 上的代表商品。只有产品/SKU精确唯一
            # 命中时才绑定；否则保持歧义并报警，绝不猜测。
            exact = [
                line for line in candidates
                if (
                    order.sku_code and line.sku_code == order.sku_code
                ) or (
                    not order.sku_code
                    and order.product_code
                    and line.product_code == order.product_code
                )
            ]
            candidates = exact
        if len(candidates) != 1:
            if parsed_lines:
                ambiguous.append(order_no)
            continue
        line = candidates[0]
        factory_no = summary.get("factory_no_at_render")
        if factory_no is not None:
            occupied = db.execute(
                select(OrderDetail).where(
                    OrderDetail.factory_no == int(factory_no),
                    OrderDetail.id != line.id,
                ).limit(1)
            ).scalar_one_or_none()
            if occupied is not None:
                factory_no_conflicts.append({
                    "order_no": order_no,
                    "factory_no": int(factory_no),
                    "occupied_sub_order_no": occupied.sub_order_no,
                })
                ambiguous.append(order_no)
                continue
        evidence.row_summary = {
            **summary,
            "sub_order_no": line.sub_order_no,
            "line_id": line.id,
            "legacy_line_binding": True,
        }
        line.factory_delivery_required = True
        if line.factory_no is None and factory_no is not None:
            line.factory_no = int(factory_no)
        line.factory_delivery_state = "sent"
        line.factory_delivery_sent_at = evidence.created_at
        bound.append(str(line.sub_order_no))
    if bound:
        db.commit()
    return {
        "bound": bound,
        "ambiguous_order_nos": sorted(set(ambiguous)),
        "factory_no_conflicts": factory_no_conflicts,
    }


def migrate_legacy_sent_binding(
    db: Session,
    *,
    order_no: str,
    sub_order_no: str,
) -> dict:
    """把一张已确认的历史主订单发送图绑定到它实际代表的子订单。

    只补证据元数据，不重发、不删图、不改变工厂编号。
    """
    line = db.execute(
        select(OrderDetail).where(OrderDetail.sub_order_no == sub_order_no)
    ).scalar_one_or_none()
    order = db.execute(select(Order).where(Order.order_no == order_no)).scalar_one_or_none()
    if line is None or order is None or line.order_no != order_no:
        return {"ok": False, "reason": "order_or_line_not_found"}
    candidates = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet_sent")
        .order_by(ImportedFile.id.asc())
    ).scalars().all()
    bound = 0
    for row in candidates:
        summary = row.row_summary or {}
        if str(summary.get("order_no") or "") != order_no:
            continue
        existing = str(summary.get("sub_order_no") or "").strip()
        if existing and existing != sub_order_no:
            return {"ok": False, "reason": "legacy_evidence_already_bound_elsewhere"}
        factory_no = summary.get("factory_no_at_render") or order.factory_no
        row.row_summary = {
            **summary,
            "sub_order_no": sub_order_no,
            "line_id": line.id,
            "legacy_line_binding": True,
        }
        if line.factory_no is None and factory_no is not None:
            line.factory_no = int(factory_no)
        line.factory_delivery_required = True
        line.factory_delivery_state = "sent"
        line.factory_delivery_sent_at = row.created_at
        bound += 1
    if bound:
        db.commit()
    return {"ok": bool(bound), "bound": bound, "sub_order_no": sub_order_no}
