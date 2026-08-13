"""物流账单的产品关联与只读统计分析。

口径：一票物流费属于整票发运。一个订单只有一个唯一实体商品规格时，
才进入“按产品”统计；多品合箱只展示产品清单，不把整票运费重复归给每个商品。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from statistics import median
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import LogisticsBill
from app.models.order import Order, OrderDetail


_SERVICE_KEYWORDS = ("送货", "入户", "安装", "上门", "运费", "补差", "差价")
_REFUND_NEGATIONS = ("没有申请退款", "未申请退款", "无退款", "退款关闭", "退款失败", "撤销退款")
_MUNICIPALITIES = ("北京", "上海", "天津", "重庆")
_PROVINCES = (
    "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西", "山东",
    "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门", "台湾",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: Any, digits: int = 2) -> Optional[float]:
    number = _num(value)
    return round(number, digits) if number is not None else None


def _is_non_physical_name(product_name: Any, sku_name: Any = None) -> bool:
    name = f"{_text(product_name)} {_text(sku_name)}"
    return not name.strip() or any(word in name for word in _SERVICE_KEYWORDS)


def _is_physical_line(line: OrderDetail) -> bool:
    if _is_non_physical_name(line.product_name, line.sku_name):
        return False
    status = _text(line.line_status).lower()
    refund_status = _text(line.refund_status)
    if status in {"cancelled", "aftersales"}:
        return False

    has_refund_status = (
        any(word in refund_status for word in ("退款", "退货"))
        and not any(word in refund_status for word in _REFUND_NEGATIONS)
    )
    if "退货" in refund_status and not any(word in refund_status for word in _REFUND_NEGATIONS):
        return False
    refund_amount = Decimal(str(line.refund_amount or 0))
    line_amount = Decimal(str(line.amount)) if line.amount is not None else None
    # 已知行金额时，只有达到行金额的退款才视为商品已全退；较小退款可能是价保、
    # 部分赔付或差额退款，实体商品仍已发运。必须同时有明确退款状态和可比较金额；
    # 只有退款金额、没有退款状态的聚合/重复行继续保守排除。
    if refund_amount > 0:
        return bool(
            has_refund_status
            and line_amount is not None
            and line_amount > 0
            and refund_amount < line_amount
        )
    if has_refund_status:
        return False
    return True


def _product_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        _text(row.get("product_code")),
        _text(row.get("sku_code")),
        _text(row.get("product_name")),
        _text(row.get("sku_name")),
    )


def product_context_by_order(db: Session, order_nos: Iterable[str]) -> dict[str, dict[str, Any]]:
    """批量取得订单产品信息，避免物流列表逐行 N+1 查询。"""
    numbers = {str(value).strip() for value in order_nos if str(value or "").strip()}
    if not numbers:
        return {}
    orders = {
        row.order_no: row
        for row in db.execute(select(Order).where(Order.order_no.in_(numbers))).scalars().all()
    }
    details: dict[str, list[dict[str, Any]]] = defaultdict(list)
    imported_detail_orders: set[str] = set()
    for line in db.execute(
        select(OrderDetail).where(
            OrderDetail.order_no.in_(numbers),
            OrderDetail.source == "import",
        ).order_by(OrderDetail.id.asc())
    ).scalars().all():
        order_no = _text(line.order_no)
        imported_detail_orders.add(order_no)
        if not _is_physical_line(line):
            continue
        details[order_no].append({
            "product_name": _text(line.product_name),
            "product_code": _text(line.product_code),
            "sku_name": _text(line.sku_name),
            "sku_code": _text(line.sku_code),
            "qty": int(line.qty or 1),
        })

    result: dict[str, dict[str, Any]] = {}
    for order_no in numbers:
        order = orders.get(order_no)
        unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for item in details.get(order_no, []):
            key = _product_key(item)
            if key not in unique:
                unique[key] = dict(item)
            else:
                unique[key]["qty"] += int(item.get("qty") or 1)
        items = list(unique.values())
        # 一旦存在导入明细，就以明细为准。明细全部退款/过滤时不能再回退主订单，
        # 否则会把已退款商品重新算进物流产品分析。
        if (
            not items
            and order_no not in imported_detail_orders
            and order
            and not _is_non_physical_name(order.product_name, order.sku)
        ):
            items = [{
                "product_name": _text(order.product_name),
                "product_code": _text(order.product_code),
                "sku_name": _text(order.sku),
                "sku_code": _text(order.sku_code),
                "qty": int(order.qty or 1),
            }]
        labels = []
        for item in items:
            label = item["product_name"] or item["product_code"] or item["sku_name"] or "未命名产品"
            if item.get("sku_name") and item["sku_name"] not in label:
                label = f"{label} · {item['sku_name']}"
            if int(item.get("qty") or 1) > 1:
                label = f"{label} ×{item['qty']}"
            labels.append(label)
        single = items[0] if len(items) == 1 else None
        single_qty = int(single.get("qty") or 1) if single else 0
        result[order_no] = {
            "order_exists": order is not None,
            "order_customer_name": order.customer_name if order else None,
            "order_customer_address": order.customer_address if order else None,
            "product_name": single.get("product_name") if single else None,
            "product_code": single.get("product_code") if single else None,
            "sku_name": single.get("sku_name") if single else None,
            "sku_code": single.get("sku_code") if single else None,
            "product_names": labels,
            "product_display": " + ".join(labels) if labels else None,
            "product_count": len(items),
            "product_qty": single_qty if single else sum(int(item.get("qty") or 1) for item in items),
            "is_multi_product": len(items) > 1,
            "is_multi_quantity": bool(single and single_qty != 1),
            "product_analytics_eligible": len(items) == 1 and single_qty == 1,
            "product_analytics_reason": (
                "single_product_single_qty" if len(items) == 1 and single_qty == 1
                else "same_product_multiple_qty" if single and single_qty != 1
                else "multi_product_shipment" if len(items) > 1
                else "product_unresolved"
            ),
        }
    return result


def parse_region(destination: Optional[str]) -> tuple[str, str]:
    """从承运商目的地文本提取省/直辖市与城市；无法确认时保留“未知”。"""
    raw = _text(destination).replace("-", " ").replace("/", " ")
    compact = "".join(raw.split())
    province = "未知"
    city = "未知"
    for name in _MUNICIPALITIES:
        if compact.startswith(name) or f"{name}市" in compact:
            return f"{name}市", f"{name}市"
    remainder = compact
    for name in _PROVINCES:
        if name in compact:
            suffix = "自治区" if name in {"内蒙古", "广西", "西藏", "宁夏", "新疆"} else ("特别行政区" if name in {"香港", "澳门"} else ("" if name == "台湾" else "省"))
            province = name + suffix
            remainder = compact.split(name, 1)[1]
            for prefix in ("壮族自治区", "回族自治区", "维吾尔自治区", "自治区", "特别行政区", "省"):
                if remainder.startswith(prefix):
                    remainder = remainder[len(prefix):]
                    break
            break
    import re
    matches = re.findall(r"^([\u4e00-\u9fff]{2,8}?(?:市|州|地区|盟))", remainder)
    for match in matches:
        if match != province and not match.endswith("省"):
            city = match
            break
    return province, city


def _weight_band(value: Optional[float]) -> str:
    if value is None:
        return "重量缺失"
    for ceiling, label in ((30, "≤30kg"), (60, "30–60kg"), (100, "60–100kg"), (150, "100–150kg"), (250, "150–250kg")):
        if value <= ceiling:
            return label
    return ">250kg"


def _volume_band(value: Optional[float]) -> str:
    if value is None:
        return "体积缺失"
    for ceiling, label in ((0.2, "≤0.2m³"), (0.5, "0.2–0.5m³"), (1.0, "0.5–1.0m³"), (2.0, "1.0–2.0m³")):
        if value <= ceiling:
            return label
    return ">2.0m³"


def _aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(key) for key in keys)].append(row)
    output = []
    for group_key, items in grouped.items():
        fees = [float(item["freight_amount"]) for item in items]
        weights = [item["billing_weight_kg"] for item in items if item["billing_weight_kg"] is not None]
        actual_weights = [item["actual_weight_kg"] for item in items if item["actual_weight_kg"] is not None]
        volumes = [item["volume_m3"] for item in items if item["volume_m3"] is not None]
        per_kg = [item["freight_per_billing_kg"] for item in items if item["freight_per_billing_kg"] is not None]
        record = {key: value for key, value in zip(keys, group_key)}
        record.update({
            "shipment_count": len(items),
            "total_freight": round(sum(fees), 2),
            "avg_freight": round(sum(fees) / len(fees), 2),
            "median_freight": round(float(median(fees)), 2),
            "min_freight": round(min(fees), 2),
            "max_freight": round(max(fees), 2),
            "avg_billing_weight_kg": round(sum(weights) / len(weights), 2) if weights else None,
            "avg_actual_weight_kg": round(sum(actual_weights) / len(actual_weights), 2) if actual_weights else None,
            "avg_volume_m3": round(sum(volumes) / len(volumes), 4) if volumes else None,
            "avg_freight_per_kg": round(sum(per_kg) / len(per_kg), 2) if per_kg else None,
            "weight_sample_count": len(weights),
            "actual_weight_sample_count": len(actual_weights),
            "volume_sample_count": len(volumes),
            "latest_bill_date": max((_text(item["bill_date"]) for item in items), default=""),
        })
        output.append(record)
    return output


def build_analytics(
    db: Session,
    *,
    product: Optional[str] = None,
    province: Optional[str] = None,
    carrier: Optional[str] = None,
    date_start: Optional[date] = None,
    date_end: Optional[date] = None,
) -> dict[str, Any]:
    stmt = select(LogisticsBill).where(LogisticsBill.row_type == "line")
    if carrier:
        stmt = stmt.where(LogisticsBill.carrier == carrier)
    if date_start:
        stmt = stmt.where(LogisticsBill.bill_date >= date_start)
    if date_end:
        stmt = stmt.where(LogisticsBill.bill_date <= date_end)
    bills = db.execute(stmt.order_by(LogisticsBill.bill_date.asc(), LogisticsBill.id.asc())).scalars().all()
    context = product_context_by_order(db, (bill.order_no for bill in bills if bill.order_no))
    rows: list[dict[str, Any]] = []
    for bill in bills:
        ctx = context.get(_text(bill.order_no), {})
        prov, city = parse_region(bill.destination)
        billing_weight = _num(bill.weight_kg)
        actual_weight = _num(bill.actual_weight_kg)
        volume = _num(bill.volume_m3)
        fee = float(bill.freight_amount or 0)
        row = {
            "bill_id": bill.id,
            "bill_date": bill.bill_date.isoformat() if bill.bill_date else None,
            "month": bill.bill_date.strftime("%Y-%m") if bill.bill_date else "未知月",
            "carrier": _text(bill.carrier) or "未知承运商",
            "tracking_no": bill.tracking_no,
            "order_no": bill.order_no,
            "destination": bill.destination,
            "province": prov,
            "city": city,
            "freight_amount": round(fee, 2),
            "billing_weight_kg": billing_weight,
            "actual_weight_kg": actual_weight,
            "volume_m3": volume,
            "package_count": bill.package_count,
            "freight_per_billing_kg": round(fee / billing_weight, 2) if billing_weight and billing_weight > 0 else None,
            "freight_per_m3": round(fee / volume, 2) if volume and volume > 0 else None,
            "weight_band": _weight_band(billing_weight),
            "volume_band": _volume_band(volume),
            **ctx,
        }
        rows.append(row)

    all_rows = rows
    if province:
        rows = [row for row in rows if row["province"] == province]
    if product:
        needle = product.strip().lower()
        rows = [row for row in rows if needle in _text(row.get("product_display")).lower()]

    eligible = [row for row in rows if row.get("product_analytics_eligible") and row.get("product_name")]
    fees = [row["freight_amount"] for row in rows]
    regions = _aggregate(rows, ("province", "city"))
    regions.sort(key=lambda item: (-item["shipment_count"], item["province"], item["city"]))
    weight_volume = _aggregate(rows, ("province", "weight_band", "volume_band"))
    weight_volume.sort(key=lambda item: -item["shipment_count"])
    products = _aggregate(eligible, ("product_name", "product_code", "sku_name", "sku_code"))
    for item in products:
        members = [row for row in eligible if all(row.get(key) == item.get(key) for key in ("product_name", "product_code", "sku_name", "sku_code"))]
        item["province_count"] = len({row["province"] for row in members})
    products.sort(key=lambda item: (-item["shipment_count"], item["product_name"] or ""))
    product_regions = _aggregate(eligible, ("product_name", "sku_name", "province", "city"))
    product_regions.sort(key=lambda item: (-item["shipment_count"], item["product_name"] or "", item["province"]))
    product_months = _aggregate(eligible, ("product_name", "sku_name", "month"))
    previous: dict[tuple[str, str], float] = {}
    for item in sorted(product_months, key=lambda value: (value["product_name"] or "", value["sku_name"] or "", value["month"])):
        key = (item["product_name"] or "", item["sku_name"] or "")
        prior = previous.get(key)
        item["change_pct"] = round((item["avg_freight"] - prior) / prior * 100, 1) if prior else None
        previous[key] = item["avg_freight"]
    product_months.sort(key=lambda item: (item["month"], item["product_name"] or ""), reverse=True)
    carriers = _aggregate(rows, ("carrier",))
    carriers.sort(key=lambda item: -item["shipment_count"])

    product_fee_samples: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in eligible:
        product_fee_samples[(row["product_name"], row.get("sku_name") or "", row["province"])].append(row["freight_amount"])
    anomalies = []
    for row in eligible:
        sample = product_fee_samples[(row["product_name"], row.get("sku_name") or "", row["province"])]
        base = float(median(sample)) if len(sample) >= 3 else None
        if base and row["freight_amount"] >= base * 1.5 and row["freight_amount"] - base >= 30:
            anomalies.append({
                **{key: row.get(key) for key in ("bill_id", "bill_date", "carrier", "tracking_no", "order_no", "product_name", "sku_name", "province", "city", "billing_weight_kg", "volume_m3", "freight_amount")},
                "product_median_freight": round(base, 2),
                "above_median_pct": round((row["freight_amount"] - base) / base * 100, 1),
                "sample_count": len(sample),
            })
    anomalies.sort(key=lambda item: -item["above_median_pct"])

    options = {
        "products": sorted({row.get("product_name") for row in all_rows if row.get("product_name")}),
        "provinces": sorted({row["province"] for row in all_rows if row["province"] != "未知"}),
        "carriers": sorted({row["carrier"] for row in all_rows if row["carrier"] != "未知承运商"}),
    }
    return {
        "filters": {"product": product, "province": province, "carrier": carrier, "date_start": date_start, "date_end": date_end},
        "options": options,
        "overview": {
            "shipment_count": len(rows),
            "total_freight": round(sum(fees), 2),
            "avg_freight": round(sum(fees) / len(fees), 2) if fees else 0,
            "median_freight": round(float(median(fees)), 2) if fees else 0,
            "matched_count": sum(1 for row in rows if row.get("order_exists")),
            "single_product_count": len(eligible),
            "multi_product_count": sum(1 for row in rows if row.get("is_multi_product")),
            "multi_quantity_count": sum(1 for row in rows if row.get("is_multi_quantity")),
            "unmatched_product_count": sum(1 for row in rows if not row.get("product_display")),
            "billing_weight_coverage": round(sum(1 for row in rows if row["billing_weight_kg"] is not None) / len(rows) * 100, 1) if rows else 0,
            "actual_weight_coverage": round(sum(1 for row in rows if row["actual_weight_kg"] is not None) / len(rows) * 100, 1) if rows else 0,
            "volume_coverage": round(sum(1 for row in rows if row["volume_m3"] is not None) / len(rows) * 100, 1) if rows else 0,
        },
        "regions": regions,
        "weight_volume_bands": weight_volume,
        "products": products,
        "product_regions": product_regions,
        "product_months": product_months,
        "carriers": carriers,
        "anomalies": anomalies[:100],
        "methodology": {
            "product_scope": "仅唯一实体商品订单进入产品均价；多品合箱只展示，不重复摊分整票运费。",
            "weight_scope": "重量分档使用物流公司的计费重量；实际重量和体积另行显示覆盖率。",
            "sample_scope": "所有均价必须结合样本数判断；产品异常仅在同产品同规格至少3票时识别。",
        },
    }
