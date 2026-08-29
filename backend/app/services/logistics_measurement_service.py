"""物流账单实测数据 → SKU 打包重量/体积。

只接受能唯一落到一个标准 SKU 的发货记录。计费重量永不参与计算；裸品重量/体积
没有来源时保持空白，不能用包裹数据冒充。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import LogisticsBill
from app.models.order import Order, OrderDetail
from app.models.pricing import PricingSku
from app.services import sku_utils


_CUSTOM_TEXT_MARKERS = (
    "定制", "补差", "专拍", "改尺寸", "尺寸改", "改颜色", "颜色改",
    "加高", "加宽", "加长", "追加", "非标",
)


@dataclass(frozen=True)
class _Observation:
    sku_code: str
    weight_kg: Decimal | None
    volume_m3: Decimal | None
    tracking_no: str | None
    bill_date: object


def _positive_decimal(value) -> Decimal | None:
    if value is None:
        return None
    value = Decimal(str(value))
    return value if value > 0 else None


def _is_semantic_custom(order: Order) -> bool:
    text = " ".join(str(v or "") for v in (
        order.sku, order.remark, order.buyer_message, order.seller_memo,
    ))
    return any(marker in text for marker in _CUSTOM_TEXT_MARKERS)


def _median(values: list[Decimal], quantum: str) -> Decimal | None:
    if not values:
        return None
    return Decimal(median(values)).quantize(Decimal(quantum), rounding=ROUND_HALF_UP)


def refresh_sku_shipping_measurements(db: Session) -> dict[str, int]:
    """从已配单的逐单物流账单刷新 SKU 打包参数。

    安全门：一条物流记录只能对应一个标准 SKU，数量仅接受 1~3；多商品、定制、
    占位 SKU 全部跳过。人工改过的打包字段不会被自动刷新覆盖。
    """
    # Import/matching and aggregation can run in the same transaction.  Flush
    # first so the safety filter below sees the latest match_method values.
    db.flush()
    bills = db.execute(
        select(LogisticsBill).where(
            LogisticsBill.row_type == "line",
            LogisticsBill.order_no.is_not(None),
            LogisticsBill.match_method.in_(("track", "manual")),
        )
    ).scalars().all()
    bills = [b for b in bills if b.actual_weight_kg is not None or b.volume_m3 is not None]

    order_nos = {str(b.order_no) for b in bills if b.order_no}
    orders = {
        o.order_no: o for o in db.execute(
            select(Order).where(Order.order_no.in_(order_nos))
        ).scalars().all()
    }
    details_by_order: dict[str, list[OrderDetail]] = defaultdict(list)
    for detail in db.execute(
        select(OrderDetail).where(
            OrderDetail.order_no.in_(order_nos),
            OrderDetail.source == "import",
            OrderDetail.sku_code.is_not(None),
        )
    ).scalars().all():
        details_by_order[str(detail.order_no)].append(detail)

    candidate_codes: set[str] = set()
    resolved: list[tuple[LogisticsBill, Order, str, int]] = []
    skipped = 0
    for bill in bills:
        order = orders.get(str(bill.order_no))
        if order is None or order.is_custom or _is_semantic_custom(order):
            skipped += 1
            continue
        detail_rows = details_by_order.get(order.order_no, [])
        if detail_rows:
            codes = {str(d.sku_code).strip() for d in detail_rows if d.sku_code}
            if len(codes) != 1:
                skipped += 1
                continue
            sku_code = next(iter(codes))
            # 主订单 SKU 与导入明细冲突时，无法证明物流实测属于哪一个，宁可不回填。
            if order.sku_code and sku_code != order.sku_code.strip():
                skipped += 1
                continue
            qty = sum(max(0, int(d.qty or 0)) for d in detail_rows if str(d.sku_code).strip() == sku_code)
            if qty <= 0:
                qty = int(order.qty or 0)
        else:
            sku_code = (order.sku_code or "").strip()
            qty = int(order.qty or 0)
        if not sku_code or not 1 <= qty <= 3 or sku_utils.is_custom_sku_code(sku_code, order.product_code):
            skipped += 1
            continue
        candidate_codes.add(sku_code)
        resolved.append((bill, order, sku_code, qty))

    auto_skus = db.execute(
        select(PricingSku).where(
            (PricingSku.packaged_weight_source == "bill")
            | (PricingSku.packaged_volume_source == "bill")
        )
    ).scalars().all()
    candidate_codes.update(sku.sku_code for sku in auto_skus)
    skus = {
        sku.sku_code: sku for sku in db.execute(
            select(PricingSku).where(PricingSku.sku_code.in_(candidate_codes))
        ).scalars().all()
    } if candidate_codes else {}

    observations: dict[str, list[_Observation]] = defaultdict(list)
    seen: set[tuple] = set()
    for bill, _order, sku_code, qty in resolved:
        sku = skus.get(sku_code)
        if sku is None or sku.is_custom_placeholder:
            skipped += 1
            continue
        weight = _positive_decimal(bill.actual_weight_kg)
        volume = _positive_decimal(bill.volume_m3)
        weight = weight / qty if weight is not None else None
        volume = volume / qty if volume is not None else None
        dedupe_key = (sku_code, bill.tracking_no or bill.id, weight, volume)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        observations[sku_code].append(_Observation(
            sku_code=sku_code,
            weight_kg=weight,
            volume_m3=volume,
            tracking_no=bill.tracking_no,
            bill_date=bill.bill_date,
        ))

    updated = 0
    for sku_code in set(observations) | {sku.sku_code for sku in auto_skus}:
        samples = observations.get(sku_code, [])
        sku = skus[sku_code]
        weights = [sample.weight_kg for sample in samples if sample.weight_kg is not None]
        volumes = [sample.volume_m3 for sample in samples if sample.volume_m3 is not None]
        changed = False
        if weights and sku.packaged_weight_source != "manual":
            value = _median(weights, "0.001")
            if sku.packaged_weight_kg != value or sku.packaged_weight_source != "bill":
                sku.packaged_weight_kg = value
                sku.packaged_weight_source = "bill"
                changed = True
        elif not weights and sku.packaged_weight_source == "bill":
            sku.packaged_weight_kg = None
            sku.packaged_weight_source = None
            changed = True
        if volumes and sku.packaged_volume_source != "manual":
            value = _median(volumes, "0.0001")
            if sku.packaged_volume_m3 != value or sku.packaged_volume_source != "bill":
                sku.packaged_volume_m3 = value
                sku.packaged_volume_source = "bill"
                changed = True
        elif not volumes and sku.packaged_volume_source == "bill":
            sku.packaged_volume_m3 = None
            sku.packaged_volume_source = None
            changed = True
        if changed:
            if samples and (sku.packaged_weight_source == "bill" or sku.packaged_volume_source == "bill"):
                representative = max(samples, key=lambda sample: (sample.bill_date is not None, sample.bill_date))
                sku.shipping_measure_source_tracking_no = representative.tracking_no
                sku.shipping_measure_source_date = representative.bill_date
                sku.shipping_measure_sample_count = len(samples)
            elif sku.packaged_weight_source != "manual" and sku.packaged_volume_source != "manual":
                sku.shipping_measure_source_tracking_no = None
                sku.shipping_measure_source_date = None
                sku.shipping_measure_sample_count = None
            updated += 1
    db.flush()
    return {
        "eligible_bills": len(bills),
        "matched_skus": len(observations),
        "updated_skus": updated,
        "skipped": skipped,
    }
