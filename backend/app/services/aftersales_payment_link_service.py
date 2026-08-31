"""个人支付宝售后打款：识别、候选、确认和全模块联动。

安全边界：
- 只处理主力号的负数流水，正数追偿/收回款不得写成售后成本；
- 只有精确类型 + 唯一未取消订单才可自动确认；
- 送装/直达、退回、混合用途和万师傅可能重复的支出始终需要复核；
- 确认后由一条关联账统一生成/刷新 AfterSales，不在多个模块重复写金额。
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.aftersales_payment import AfterSalesPaymentLink
from app.models.finance import AlipayFlow, WanshifuOrder
from app.models.marketing import AfterSales
from app.models.order import Order


PERSONAL_ACCOUNT = "主力号"
ALLOCATION_FULL = "full"
LINK_STATES = frozenset({"proposed", "confirmed", "rejected", "voided"})

CATEGORY_LABELS = {
    "price_difference": "差价退补",
    "review_refund": "晒图/好评返现",
    "customer_compensation": "客户赔付",
    "repair_service": "售后维修",
    "onsite_service": "上门/送装服务",
    "return_service": "退回/返厂服务",
    "misc_after_sales": "其他售后",
}

# 仅这些明确是客户侧售后支出，且订单唯一时才可无人值守确认。
AUTO_CATEGORIES = frozenset({"price_difference", "review_refund", "customer_compensation"})
_LINK_AMOUNT_FIELDS = (
    "compensation_fee", "good_review_refund", "direct_compensation",
    "second_visit_fee", "return_pack_freight", "refill_freight",
    "wanshifu_deduction", "factory_compensation", "logistics_compensation",
)


@dataclass
class Candidate:
    alipay_flow_id: int
    transaction_no: str
    transaction_time: Optional[str]
    amount: str
    counterparty: Optional[str]
    remark: Optional[str]
    category: str
    category_label: str
    extracted_order_no: Optional[str]
    extracted_customer_name: Optional[str]
    order_id: Optional[int]
    order_no: Optional[str]
    order_customer_name: Optional[str]
    order_product_name: Optional[str]
    wanshifu_order_id: Optional[int]
    wanshifu_order_no: Optional[str]
    match_method: str
    confidence: str
    auto_eligible: bool
    reason: str
    evidence: dict


def _q2(value) -> Decimal:
    return Decimal(str(abs(value or 0))).quantize(Decimal("0.01"))


def _dedupe_repeated_text(value: Optional[str]) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    # 个人支付宝导出常把备注拼两遍；只在两半完全相同时去重。
    parts = text.split(" ")
    if len(parts) % 2 == 0 and parts[: len(parts) // 2] == parts[len(parts) // 2 :]:
        return " ".join(parts[: len(parts) // 2])
    if len(text) % 2 == 0 and text[: len(text) // 2] == text[len(text) // 2 :]:
        return text[: len(text) // 2].strip()
    return text


def classify_remark(value: Optional[str]) -> Optional[str]:
    text = _dedupe_repeated_text(value)
    if not text:
        return None
    if any(k in text for k in ("客户图评返", "客户图返", "图评返", "好评返")):
        return "review_refund"
    if "差价" in text:
        return "price_difference"
    if any(k in text for k in ("赔付", "补偿")):
        return "customer_compensation"
    if "维修" in text:
        return "repair_service"
    if any(k in text for k in ("送装", "直达")):
        return "onsite_service"
    if any(k in text for k in ("返厂", "拆板返", "返床头柜")):
        return "return_service"
    if "售后" in text:
        return "misc_after_sales"
    return None


def _explicit_order_nos(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"(?<!\d)(\d{19})(?!\d)", text)))


def _fallback_name(text: str) -> Optional[str]:
    cleaned = re.sub(r"(?<!\d)\d{19}(?!\d)", "", text).strip(" -_?:：")
    markers = (
        "客户差价补偿", "客户差价", "餐桌差价", "差价",
        "客户图评返", "客户图返", "图评返", "返床头柜",
        "客户床维修费", "维修", "拆板返", "送装", "直达", "售后",
    )
    positions = [cleaned.find(m) for m in markers if cleaned.find(m) > 0]
    if not positions:
        return None
    name = cleaned[: min(positions)].strip(" -_?:：")
    # 不把一整句产品描述当成客户姓名。
    return name if 2 <= len(name) <= 12 else None


def _order_snapshot(order: Optional[Order]) -> dict:
    if order is None:
        return {}
    return {
        "id": order.id,
        "order_no": order.order_no,
        "customer_name": order.customer_name,
        "product_name": order.product_name,
        "order_date": order.order_date.isoformat() if order.order_date else None,
        "status": order.status,
        "paid_amount": str(order.paid_amount) if order.paid_amount is not None else None,
        "refund_amount": str(order.refund_amount) if order.refund_amount is not None else None,
    }


def _candidate_for_flow(db: Session, flow: AlipayFlow) -> Optional[Candidate]:
    category = classify_remark(flow.remark)
    if category is None or (flow.amount or 0) >= 0:
        return None
    text = _dedupe_repeated_text(flow.remark)
    explicit = _explicit_order_nos(text)
    flow_day = flow.transaction_time.date() if flow.transaction_time else None
    all_orders = db.execute(select(Order)).scalars().all()
    chosen: Optional[Order] = None
    name: Optional[str] = None
    method = "unmatched"
    confidence = Decimal("0")
    reason = "没有找到唯一订单"
    candidate_orders: list[Order] = []
    cancelled_candidates: list[Order] = []

    if len(explicit) == 1:
        chosen = next((o for o in all_orders if o.order_no == explicit[0]), None)
        if chosen is not None:
            method, confidence, reason = "order_no_exact", Decimal("1"), "备注含唯一精确订单号"
        else:
            method, reason = "external_order_missing", "备注有订单号，但 ERP 订单库缺该单"
    elif len(explicit) > 1:
        method, reason = "multiple_order_numbers", "备注同时含多个订单号"

    # 某些支付宝结构化导入已经给出 related_order_no。它仍必须在 ERP
    # 订单表精确存在，不能因为一个非空字符串就制造孤儿售后。
    related_order_no = (flow.related_order_no or "").strip()
    if chosen is None and not explicit and related_order_no:
        chosen = next((o for o in all_orders if o.order_no == related_order_no), None)
        if chosen is not None:
            method, confidence, reason = (
                "flow_related_order_exact", Decimal("0.9800"), "支付宝结构化字段含 ERP 精确订单号",
            )
        else:
            method, reason = "flow_related_order_missing", "支付宝结构化订单号在 ERP 中不存在"

    if chosen is None and not explicit and not related_order_no:
        # 先用 ERP 真实姓名反向识别备注前缀，比自由文本分词更安全。
        names = sorted(
            {str(o.customer_name or "").strip() for o in all_orders if len(str(o.customer_name or "").strip()) >= 2},
            key=len, reverse=True,
        )
        name = next((n for n in names if text.startswith(n)), None) or _fallback_name(text)
        if name:
            same_name = [o for o in all_orders if (o.customer_name or "").strip() == name]
            for order in same_name:
                if order.status == "cancelled":
                    cancelled_candidates.append(order)
                    continue
                if flow_day and order.order_date:
                    age = (flow_day - order.order_date).days
                    if age < 0 or age > 400:
                        continue
                candidate_orders.append(order)
            if len(candidate_orders) == 1:
                chosen = candidate_orders[0]
                method, confidence, reason = "customer_name_unique_active", Decimal("0.9200"), "客户全名仅命中一个未取消订单"
            elif len(candidate_orders) > 1:
                method, confidence, reason = "customer_name_multiple", Decimal("0.5000"), "同名客户有多个可能订单"
            elif cancelled_candidates:
                method, confidence, reason = "cancelled_orders_only", Decimal("0.3000"), "姓名只命中已取消订单，不能自动入账"
        else:
            reason = "备注中无可与 ERP 客户全名精确对上的名称"

    wsf: Optional[WanshifuOrder] = None
    wsf_candidates: list[WanshifuOrder] = []
    if chosen is not None and category in {"repair_service", "onsite_service", "return_service"}:
        wsf_candidates = db.execute(
            select(WanshifuOrder).where(WanshifuOrder.matched_order_no == chosen.order_no)
        ).scalars().all()
        active_wsf = [w for w in wsf_candidates if "关闭" not in (w.status or "")]
        if len(active_wsf) == 1:
            wsf = active_wsf[0]

    order_is_active = bool(chosen is not None and chosen.status != "cancelled")
    if chosen is not None and not order_is_active:
        reason += "；订单已取消，不能自动入账"
    auto_eligible = bool(
        order_is_active
        and category in AUTO_CATEGORIES
        and method in {"order_no_exact", "flow_related_order_exact", "customer_name_unique_active"}
        and not wsf_candidates
    )
    if chosen is not None and category not in AUTO_CATEGORIES:
        reason += "；该类型可能是正常履约或与万师傅重复，必须复核"

    evidence = {
        "rule_version": "personal-alipay-aftersales-v1",
        "normalized_remark": text,
        "flow_previous_status": flow.reconciliation_status,
        "flow_previous_type": flow.reconciliation_type,
        "explicit_order_nos": explicit,
        "candidate_orders": [_order_snapshot(o) for o in candidate_orders],
        "cancelled_candidates": [_order_snapshot(o) for o in cancelled_candidates],
        "chosen_order": _order_snapshot(chosen),
        "wanshifu_candidates": [
            {"id": w.id, "order_no": w.wsf_order_no, "status": w.status,
             "matched_order_no": w.matched_order_no, "net_amount": str(w.net_amount)}
            for w in wsf_candidates
        ],
        "auto_eligible": auto_eligible,
    }
    return Candidate(
        alipay_flow_id=flow.id,
        transaction_no=flow.transaction_no,
        transaction_time=flow.transaction_time.isoformat() if flow.transaction_time else None,
        amount=str(_q2(flow.amount)), counterparty=flow.counterparty, remark=flow.remark,
        category=category, category_label=CATEGORY_LABELS[category],
        extracted_order_no=explicit[0] if len(explicit) == 1 else None,
        extracted_customer_name=name,
        order_id=chosen.id if chosen else None,
        order_no=chosen.order_no if chosen else None,
        order_customer_name=chosen.customer_name if chosen else None,
        order_product_name=chosen.product_name if chosen else None,
        wanshifu_order_id=wsf.id if wsf else None,
        wanshifu_order_no=wsf.wsf_order_no if wsf else None,
        match_method=method, confidence=str(confidence), auto_eligible=auto_eligible,
        reason=reason, evidence=evidence,
    )


def preview(
    db: Session, *, start_date: Optional[date] = None, end_date: Optional[date] = None,
    created_since: Optional[datetime] = None,
) -> list[Candidate]:
    stmt = select(AlipayFlow).where(
        AlipayFlow.account == PERSONAL_ACCOUNT,
        AlipayFlow.amount < 0,
    )
    if start_date:
        stmt = stmt.where(func.date(AlipayFlow.transaction_time) >= start_date)
    if end_date:
        stmt = stmt.where(func.date(AlipayFlow.transaction_time) <= end_date)
    if created_since:
        stmt = stmt.where(AlipayFlow.created_at >= created_since)
    flows = db.execute(stmt.order_by(AlipayFlow.transaction_time, AlipayFlow.id)).scalars().all()
    existing = {
        flow_id for flow_id in db.execute(select(AfterSalesPaymentLink.alipay_flow_id)).scalars().all()
    }
    out: list[Candidate] = []
    for flow in flows:
        if flow.id in existing:
            continue
        candidate = _candidate_for_flow(db, flow)
        if candidate is not None:
            out.append(candidate)
    return out


def _candidate_evidence(candidate: Candidate) -> dict:
    return {**candidate.evidence, "proposal": {
        k: v for k, v in asdict(candidate).items() if k != "evidence"
    }}


def persist_scan(
    db: Session, *, start_date: Optional[date] = None, end_date: Optional[date] = None,
    created_since: Optional[datetime] = None, auto_confirm_safe: bool = False,
    actor: str = "automation:alipay-ingest",
) -> dict:
    candidates = preview(db, start_date=start_date, end_date=end_date, created_since=created_since)
    created = confirmed = auto_confirm_errors = 0
    ids: list[int] = []
    for candidate in candidates:
        link = AfterSalesPaymentLink(
            alipay_flow_id=candidate.alipay_flow_id,
            allocation_key=ALLOCATION_FULL,
            order_id=candidate.order_id,
            wanshifu_order_id=candidate.wanshifu_order_id,
            category=candidate.category,
            allocated_amount=Decimal(candidate.amount),
            status="proposed",
            match_method=candidate.match_method,
            confidence=Decimal(candidate.confidence),
            extracted_order_no=candidate.extracted_order_no,
            extracted_customer_name=candidate.extracted_customer_name,
            evidence_json=_candidate_evidence(candidate),
            decision_note=candidate.reason,
            created_by=actor,
        )
        db.add(link)
        db.flush()
        created += 1
        ids.append(link.id)
        if auto_confirm_safe and candidate.auto_eligible:
            try:
                # 单条失败只降级为待核对，不让一条旧脏数据拖垮整批新流水。
                with db.begin_nested():
                    confirm(db, link.id, expected_version=link.version, actor=actor, auto=True)
                confirmed += 1
            except ValueError as exc:
                auto_confirm_errors += 1
                db.refresh(link)
                evidence = dict(link.evidence_json or {})
                evidence["auto_confirm_error"] = str(exc)
                link.evidence_json = evidence
                link.decision_note = f"{candidate.reason}；自动确认已停止：{exc}"
                db.flush()
    return {
        "scanned": len(candidates), "created": created, "confirmed": confirmed,
        "auto_confirm_errors": auto_confirm_errors, "link_ids": ids,
    }


def _resolve_order(db: Session, link: AfterSalesPaymentLink, order_no: Optional[str]) -> Order:
    if order_no:
        order = db.execute(select(Order).where(Order.order_no == order_no.strip())).scalar_one_or_none()
    else:
        order = db.get(Order, link.order_id) if link.order_id else None
    if order is None:
        raise ValueError("必须先选定 ERP 中真实存在的订单")
    if order.status == "cancelled":
        raise ValueError("不能把个人打款自动挂到已取消订单")
    return order


def _resolve_wanshifu(
    db: Session, order: Order, wanshifu_order_no: Optional[str], existing_id: Optional[int],
) -> Optional[WanshifuOrder]:
    if wanshifu_order_no:
        row = db.execute(
            select(WanshifuOrder).where(WanshifuOrder.wsf_order_no == wanshifu_order_no.strip())
        ).scalar_one_or_none()
    else:
        row = db.get(WanshifuOrder, existing_id) if existing_id else None
    if row is not None and row.matched_order_no != order.order_no:
        raise ValueError("万师傅单与选定的 ERP 订单不一致")
    return row


def _managed_aftersales_for_link(
    db: Session, link: AfterSalesPaymentLink, flow: AlipayFlow, order: Order,
) -> AfterSales:
    if link.after_sales_id:
        row = db.get(AfterSales, link.after_sales_id)
        if row is None or not row.payment_link_managed:
            raise ValueError("关联账指向的售后行不是系统托管行")
        return row
    same_flow = db.execute(
        select(AfterSales).where(AfterSales.alipay_flow_no == flow.transaction_no)
    ).scalars().all()
    if len(same_flow) > 1:
        raise ValueError("同一支付宝流水已对应多条旧售后，必须先人工理清")
    if same_flow:
        row = same_flow[0]
        old_no = (row.platform_order_no or "").strip()
        auto_origin = row.status == "auto" and (row.remark or "").startswith("自动从支付宝流水")
        if old_no and old_no != order.order_no:
            raise ValueError("该流水的旧售后行已挂到另一订单")
        if not auto_origin and not row.payment_link_managed:
            raise ValueError("该流水已被人工售后行使用，不自动覆盖")
        row.payment_link_managed = True
    else:
        row = AfterSales(
            platform_order_no=order.order_no,
            alipay_flow_no=flow.transaction_no,
            payment_link_managed=True,
        )
        db.add(row)
        db.flush()
    row.platform_order_no = order.order_no
    row.alipay_flow_no = flow.transaction_no
    link.after_sales_id = row.id
    return row


def _apply_managed_amount(row: AfterSales, *, category: str, amount: Decimal, flow: AlipayFlow) -> None:
    for field in _LINK_AMOUNT_FIELDS:
        setattr(row, field, None)
    target = {
        "price_difference": "good_review_refund",
        "review_refund": "good_review_refund",
        "customer_compensation": "direct_compensation",
        "repair_service": "second_visit_fee",
        "onsite_service": "second_visit_fee",
        "return_service": "return_pack_freight",
        "misc_after_sales": "direct_compensation",
    }[category]
    setattr(row, target, amount)
    row.out_platform_total = amount
    row.in_platform_total = None
    row.reason = f"{CATEGORY_LABELS[category]}（个人支付宝）"
    row.processed_at = flow.transaction_time.date() if flow.transaction_time else date.today()
    row.status = "auto_linked"
    row.remark = f"关联账自动同步；支付宝流水 {flow.transaction_no}"


def confirm(
    db: Session, link_id: int, *, expected_version: int, actor: str,
    order_no: Optional[str] = None, category: Optional[str] = None,
    wanshifu_order_no: Optional[str] = None, decision_note: Optional[str] = None,
    auto: bool = False,
) -> AfterSalesPaymentLink:
    link = db.execute(
        select(AfterSalesPaymentLink).where(AfterSalesPaymentLink.id == link_id).with_for_update()
    ).scalar_one_or_none()
    if link is None:
        raise ValueError("关联账不存在")
    if link.status == "confirmed":
        return link
    if link.status != "proposed":
        raise ValueError(f"当前状态 {link.status} 不能确认")
    if link.version != expected_version:
        raise ValueError("候选已变化，请刷新后重新核对")
    evidence = dict(link.evidence_json or {})
    if auto and not bool(evidence.get("auto_eligible") or evidence.get("proposal", {}).get("auto_eligible")):
        raise ValueError("该候选不满足无人值守确认条件")

    final_category = (category or link.category).strip()
    if final_category not in CATEGORY_LABELS:
        raise ValueError("未知售后类型")
    flow = db.get(AlipayFlow, link.alipay_flow_id)
    if flow is None or (flow.amount or 0) >= 0:
        raise ValueError("只能确认真实存在的支出流水")
    confirmed_other = db.execute(
        select(func.coalesce(func.sum(AfterSalesPaymentLink.allocated_amount), 0)).where(
            AfterSalesPaymentLink.alipay_flow_id == flow.id,
            AfterSalesPaymentLink.status == "confirmed",
            AfterSalesPaymentLink.id != link.id,
        )
    ).scalar_one()
    if _q2(confirmed_other) + _q2(link.allocated_amount) > _q2(flow.amount):
        raise ValueError("确认的分摊合计超过原流水支出")

    order = _resolve_order(db, link, order_no)
    wsf = _resolve_wanshifu(db, order, wanshifu_order_no, link.wanshifu_order_id)
    row = _managed_aftersales_for_link(db, link, flow, order)
    link.order_id = order.id
    link.wanshifu_order_id = wsf.id if wsf else None
    link.category = final_category
    _apply_managed_amount(row, category=final_category, amount=_q2(link.allocated_amount), flow=flow)

    link.status = "confirmed"
    link.decided_by = actor
    link.decided_at = datetime.now(timezone.utc)
    link.decision_note = decision_note or link.decision_note
    link.version += 1
    flow.reconciliation_status = "matched"
    flow.reconciliation_type = "aftersales"
    db.flush()
    return link


def reject(
    db: Session, link_id: int, *, expected_version: int, actor: str, decision_note: str,
) -> AfterSalesPaymentLink:
    link = db.get(AfterSalesPaymentLink, link_id)
    if link is None:
        raise ValueError("关联账不存在")
    if link.status == "rejected":
        return link
    if link.status != "proposed" or link.version != expected_version:
        raise ValueError("候选状态已变化，请刷新")
    link.status = "rejected"
    link.decided_by = actor
    link.decided_at = datetime.now(timezone.utc)
    link.decision_note = decision_note.strip()
    link.version += 1
    db.flush()
    return link


def void(
    db: Session, link_id: int, *, expected_version: int, actor: str, decision_note: str,
) -> AfterSalesPaymentLink:
    link = db.get(AfterSalesPaymentLink, link_id)
    if link is None:
        raise ValueError("关联账不存在")
    if link.status == "voided":
        return link
    if link.status != "confirmed" or link.version != expected_version:
        raise ValueError("只能作废当前版本的已确认关联")
    row = db.get(AfterSales, link.after_sales_id) if link.after_sales_id else None
    if row is None or not row.payment_link_managed:
        raise ValueError("找不到可安全回撤的系统托管售后行")
    for field in _LINK_AMOUNT_FIELDS:
        setattr(row, field, None)
    row.out_platform_total = None
    row.status = "link_voided"
    row.remark = f"关联账已作废：{decision_note.strip()}"
    link.status = "voided"
    link.decided_by = actor
    link.decided_at = datetime.now(timezone.utc)
    link.decision_note = decision_note.strip()
    link.version += 1
    flow = db.get(AlipayFlow, link.alipay_flow_id)
    still_confirmed = db.execute(
        select(func.count(AfterSalesPaymentLink.id)).where(
            AfterSalesPaymentLink.alipay_flow_id == link.alipay_flow_id,
            AfterSalesPaymentLink.status == "confirmed",
            AfterSalesPaymentLink.id != link.id,
        )
    ).scalar_one()
    if flow is not None and not still_confirmed:
        evidence = link.evidence_json or {}
        flow.reconciliation_status = evidence.get("flow_previous_status") or "open"
        flow.reconciliation_type = evidence.get("flow_previous_type")
    db.flush()
    return link


def serialize(db: Session, link: AfterSalesPaymentLink) -> dict:
    flow = db.get(AlipayFlow, link.alipay_flow_id)
    order = db.get(Order, link.order_id) if link.order_id else None
    wsf = db.get(WanshifuOrder, link.wanshifu_order_id) if link.wanshifu_order_id else None
    return {
        "id": link.id, "version": link.version, "status": link.status,
        "allocation_key": link.allocation_key, "category": link.category,
        "category_label": CATEGORY_LABELS.get(link.category, link.category),
        "allocated_amount": float(link.allocated_amount),
        "match_method": link.match_method, "confidence": float(link.confidence or 0),
        "extracted_order_no": link.extracted_order_no,
        "extracted_customer_name": link.extracted_customer_name,
        "decision_note": link.decision_note,
        "order": _order_snapshot(order) or None,
        "after_sales_id": link.after_sales_id,
        "wanshifu_order": ({"id": wsf.id, "order_no": wsf.wsf_order_no,
                             "status": wsf.status, "matched_order_no": wsf.matched_order_no}
                            if wsf else None),
        "flow": ({"id": flow.id, "transaction_no": flow.transaction_no,
                  "time": flow.transaction_time.isoformat() if flow.transaction_time else None,
                  "amount": float(flow.amount), "counterparty": flow.counterparty,
                  "remark": flow.remark, "reconciliation_status": flow.reconciliation_status,
                  "reconciliation_type": flow.reconciliation_type} if flow else None),
        "evidence": link.evidence_json or {},
        "created_by": link.created_by, "decided_by": link.decided_by,
        "decided_at": link.decided_at.isoformat() if link.decided_at else None,
        "created_at": link.created_at.isoformat() if link.created_at else None,
        "updated_at": link.updated_at.isoformat() if link.updated_at else None,
    }


def list_links(db: Session, *, status: Optional[str] = None, limit: int = 500) -> list[dict]:
    stmt = select(AfterSalesPaymentLink)
    if status:
        if status not in LINK_STATES:
            raise ValueError("未知关联状态")
        stmt = stmt.where(AfterSalesPaymentLink.status == status)
    rows = db.execute(
        stmt.order_by(AfterSalesPaymentLink.id.desc()).limit(min(max(limit, 1), 1000))
    ).scalars().all()
    return [serialize(db, row) for row in rows]
