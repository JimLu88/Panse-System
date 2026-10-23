"""Read-only, explicit SKU basis for custom quotes; never mutates catalog rows."""
from decimal import Decimal
from types import SimpleNamespace
import math


def positive(value, name):
    if value is None or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{name}须为已确认的正数")
    return float(value)


def reference_costs(sku, price):
    if sku is None:
        raise ValueError("必须精确选择原SKU，不能按名称猜配")
    physical = positive(sku.physical_cost, "原SKU物理成本")
    price = positive(price, "原SKU指定到手价")
    fees = {}
    for field in ("packaging_cost", "logistics_cost", "install_cost"):
        value = getattr(sku, field, None)
        if value is None or not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"原SKU缺少有效{field}，不回退通用费用")
        fees[field] = float(value)
    return {"sku_code": sku.sku_code, "buyer_price": price,
            "physical_cost": physical, "cost_to_buyer_ratio": price / physical,
            "fees": fees, "price_tier": "big_buyer", "price_field": "big_buyer_price",
            "platform_discount_applied_again": False}


def outer_reference(sku, dimensions, tier, prices, legacy_length, legacy_width, height):
    from app.services.custom_quote_v2_service import _parse_size_info
    if tier != "big_buyer":
        raise ValueError("外尺寸原款报价当前仅支持明确的大促到手价 big_buyer")
    if legacy_length is not None or legacy_width is not None or height is not None:
        raise ValueError("outer_dimensions不能与旧长宽高字段混用；床头删除请用部件，不缩放整床高度")
    basis = reference_costs(sku, prices.get(sku.sku_code) if sku else None)
    positive(sku.wood_cost, "原SKU木作成本")
    length, width, h = _parse_size_info(sku.size_info)
    length, width = positive(length, "原SKU外长"), positive(width, "原SKU外宽")
    tl = positive(dimensions.get("length_cm"), "目标床体外长")
    tw = positive(dimensions.get("width_cm"), "目标床体外宽")
    rl, rw = dimensions.get("reference_length_cm"), dimensions.get("reference_width_cm")
    if (rl is None) != (rw is None):
        raise ValueError("覆盖原款外形须同时提供外长、外宽和来源")
    source = "PricingSku.size_info"
    if rl is not None:
        source = str(dimensions.get("reference_source") or "").strip()
        if not source:
            raise ValueError("原款外形覆盖缺少来源")
        rl, rw = positive(rl, "原款外长"), positive(rw, "原款外宽")
    else:
        rl, rw = length, width
    basis.update(reference_length_cm=rl, reference_width_cm=rw,
                 stored_length_cm=length, stored_width_cm=width,
                 reference_source=source, dimension_conflict=(rl != length or rw != width),
                 target_length_cm=tl, target_width_cm=tw,
                 reference_area_m2=rl * rw / 10000,
                 estimate_only=True, limitation="面积法仅为木作估算，非完整工厂板单；铺板采购价保持原款")
    # A plain projection, not an ORM copy: no attached state or DB dirty fields.
    projection = SimpleNamespace(**{c.name: getattr(sku, c.name) for c in sku.__table__.columns})
    projection.quote_outer_length_m = rl / 100
    projection.size_info = f"长度：{rl*10}mm；宽度：{rw*10}mm；高度：{(h or 0)*10}mm"
    return projection, basis


def board_reference(db, product_code, sku_code, price_tier):
    from app.models.pricing import PricingSku
    from app.services.custom_quote_v2_service import _tier_price_map, _is_quoteable_sku
    if not product_code or not sku_code or price_tier != "big_buyer":
        raise ValueError("原款板单报价须明确产品、SKU和 big_buyer 大促到手价")
    sku = db.query(PricingSku).filter(PricingSku.product_code == product_code, PricingSku.sku_code == sku_code).one_or_none()
    prices = _tier_price_map(db, [sku], price_tier) if sku else {}
    price = prices.get(sku_code)
    if sku is None or not _is_quoteable_sku(sku, resolved_price=price):
        raise ValueError("原SKU不存在、占位或缺少有效大促到手价")
    return reference_costs(sku, price)
