"""库存需求统一清洗与「长期底座 + 短期动量」预测。

这是成品库存、ABC、波动、安全库存、未发占用、30 天预测和月度备货报告的
唯一订单口径。任何调用方都不应再直接 sum(Order.qty)。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import product_coder, sku_utils

WINDOWS = (7, 15, 30, 60, 90)
SHORT_WEIGHTS = {7: 0.50, 15: 0.30, 30: 0.20}
LONG_WEIGHTS = {60: 0.60, 90: 0.40}
SHORT_BLEND = 0.55
LONG_BLEND = 0.45

DEFAULT_PROMO_PERIODS = [
    {"key": "618", "name": "618", "start": "05-20", "end": "06-20", "multiplier": 3.0},
    {"key": "double11", "name": "双11", "start": "10-20", "end": "11-13", "multiplier": 4.25},
    {"key": "double12", "name": "双12", "start": "12-01", "end": "12-12", "multiplier": 2.125},
]

# 春节日期会漂移，按农历正月初一维护；窗口默认前 14 天至后 15 天。
# 数据不删除：春节订单单独形成 cny_daily，普通月份基线跳过这些场景日。
_CNY_DATES = {
    2026: date(2026, 2, 17),
    2027: date(2027, 2, 6),
    2028: date(2028, 1, 26),
    2029: date(2029, 2, 13),
    2030: date(2030, 2, 3),
    2031: date(2031, 1, 23),
    2032: date(2032, 2, 11),
    2033: date(2033, 1, 31),
    2034: date(2034, 2, 19),
    2035: date(2035, 2, 8),
}

_NON_PRODUCT_KW = (
    "邮费", "运费", "定金", "样块", "小样", "样品",
)
_CUSTOM_SKU_KW = (
    "定制", "定做", "改尺寸", "改色", "其它材质", "其他材质", "联系客服",
)
_CUSTOM_PRODUCT_KW = (
    "全屋定制", "定制专拍", "定制链接", "补差", "差价", "补拍", "专拍", "专链", "改价",
)
_CUSTOM_NOTE_NEGATIONS = ("不定制", "无需定制", "不是定制", "非定制")
_SETTLED_STATUSES = ("paid", "shipped", "signed", "completed", "success", "finished")


@dataclass(frozen=True)
class DemandObservation:
    order_id: int
    order_no: str
    order_date: date
    product_code: str
    product_name: str
    sku_code: str
    sku: str
    kind: str                 # standard / custom / skip
    raw_qty: int
    effective_qty: int
    anomaly: Optional[str] = None


def _as_float(v, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def promo_periods(cfg: Optional[dict] = None) -> list[dict]:
    rows = (cfg or {}).get("promo_periods_v2") or DEFAULT_PROMO_PERIODS
    return rows if isinstance(rows, list) and rows else DEFAULT_PROMO_PERIODS


def _month_day_period(d: date, row: dict) -> bool:
    try:
        sm, sd = (int(x) for x in str(row.get("start", "")).split("-"))
        em, ed = (int(x) for x in str(row.get("end", "")).split("-"))
        start = date(d.year, sm, sd)
        end = date(d.year, em, ed)
    except (TypeError, ValueError):
        return False
    if end >= start:
        return start <= d <= end
    return d >= start or d <= end


def promo_for_date(d: date, cfg: Optional[dict] = None) -> Optional[dict]:
    for row in promo_periods(cfg):
        if _month_day_period(d, row):
            return row
    return None


def promo_factor_for_date(d: date, cfg: Optional[dict] = None) -> float:
    row = promo_for_date(d, cfg)
    return max(0.01, _as_float(row.get("multiplier"), 1.0)) if row else 1.0


def cny_window(year: int, cfg: Optional[dict] = None) -> Optional[tuple[date, date]]:
    configured = (cfg or {}).get("cny_periods") or []
    for row in configured:
        try:
            start, end = date.fromisoformat(row["start"]), date.fromisoformat(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if start.year == year or end.year == year:
            return start, end
    new_year = _CNY_DATES.get(year)
    if not new_year:
        return None
    before = int(_as_float((cfg or {}).get("cny_before_days"), 14))
    after = int(_as_float((cfg or {}).get("cny_after_days"), 15))
    return new_year - timedelta(days=before), new_year + timedelta(days=after)


def is_cny_date(d: date, cfg: Optional[dict] = None) -> bool:
    for year in (d.year - 1, d.year, d.year + 1):
        window = cny_window(year, cfg)
        if window and window[0] <= d <= window[1]:
            return True
    return False


def _is_settled(o: Order) -> bool:
    if (o.status or "") not in _SETTLED_STATUSES:
        return False
    paid = Decimal(str(o.paid_amount or 0))
    refund = Decimal(str(o.refund_amount or 0))
    if paid <= 0:
        return False
    return not (refund > 0 and refund >= paid * Decimal("0.99"))


def _placeholder_codes(db: Session) -> set[str]:
    return set(db.execute(
        select(PricingSku.sku_code).where(PricingSku.is_custom_placeholder == True)  # noqa: E712
    ).scalars().all())


def _confirmed_bulk(cfg: Optional[dict]) -> set[str]:
    rows = (cfg or {}).get("confirmed_bulk_order_nos") or []
    return {str(x) for x in rows if str(x).strip()}


def classify_order(
    o: Order, *, placeholder_codes: set[str], cfg: Optional[dict] = None,
) -> DemandObservation:
    raw_qty = max(1, int(o.qty or 1))
    product_name = str(o.product_name or "")
    sku = str(o.sku or "")
    sku_code = str(o.sku_code or "")
    notes = " ".join(str(x or "") for x in (o.remark, o.seller_memo, o.buyer_message))
    combined = f"{product_name} {sku}"

    if any(k in combined for k in _NON_PRODUCT_KW):
        kind = "skip"
    else:
        custom_note = ("定制" in notes and not any(k in notes for k in _CUSTOM_NOTE_NEGATIONS))
        stripped = sku_utils.strip_custom_suffix(sku_code) or sku_code
        custom = (
            bool(o.is_custom)
            or sku_utils.is_custom_sku_code(sku_code, o.product_code)
            or sku_code in placeholder_codes
            or stripped in placeholder_codes
            or any(k in sku for k in _CUSTOM_SKU_KW)
            or any(k in product_name for k in _CUSTOM_PRODUCT_KW)
            or custom_note
        )
        kind = "custom" if custom else "standard"

    anomaly = None
    effective_qty = raw_qty
    confirmed = str(o.order_no or "") in _confirmed_bulk(cfg)
    if raw_qty > 5 and not confirmed:
        # 超过 5 件从成品热销统计隔离，只保留 1 个生产任务。
        anomaly = "qty_gt5"
        effective_qty = 1
        if kind != "skip":
            kind = "custom"
    elif 4 <= raw_qty <= 5:
        anomaly = "qty_4_5"

    return DemandObservation(
        order_id=int(o.id or 0),
        order_no=str(o.order_no or ""),
        order_date=o.order_date,
        product_code=str(o.product_code or ""),
        product_name=product_name,
        sku_code=sku_code,
        sku=sku,
        kind=kind,
        raw_qty=raw_qty,
        effective_qty=effective_qty,
        anomaly=anomaly,
    )


def load_observations(
    db: Session, *, start: date, end: date,
    cfg: Optional[dict] = None,
    product_codes: Optional[Iterable[str]] = None,
) -> list[DemandObservation]:
    stmt = select(Order).where(
        Order.order_date >= start,
        Order.order_date <= end,
        Order.order_date.isnot(None),
        Order.is_refill == False,  # noqa: E712
        Order.status.in_(_SETTLED_STATUSES),
    )
    if product_codes:
        stmt = stmt.where(Order.product_code.in_(set(product_codes)))
    placeholders = _placeholder_codes(db)
    out = []
    for o in db.execute(stmt).scalars().all():
        if not _is_settled(o) or not o.product_code:
            continue
        out.append(classify_order(o, placeholder_codes=placeholders, cfg=cfg))
    return out


def _days(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def build_profile(
    observations: list[DemandObservation], *, as_of: date,
    cfg: Optional[dict] = None, kind: str = "standard",
) -> dict:
    selected = [o for o in observations if o.kind == kind]
    rates: dict[int, float] = {}
    units: dict[int, float] = {}
    sale_days: dict[int, int] = {}
    for window in WINDOWS:
        start = as_of - timedelta(days=window - 1)
        eligible_days = sum(1 for d in _days(start, as_of) if not is_cny_date(d, cfg))
        qty = 0.0
        sold: set[date] = set()
        for o in selected:
            if start <= o.order_date <= as_of and not is_cny_date(o.order_date, cfg):
                qty += o.effective_qty / promo_factor_for_date(o.order_date, cfg)
                sold.add(o.order_date)
        units[window] = round(qty, 4)
        rates[window] = qty / max(1, eligible_days)
        sale_days[window] = len(sold)

    short_daily = sum(rates[w] * SHORT_WEIGHTS[w] for w in SHORT_WEIGHTS)
    long_daily = sum(rates[w] * LONG_WEIGHTS[w] for w in LONG_WEIGHTS)
    normal_daily = short_daily * SHORT_BLEND + long_daily * LONG_BLEND

    cny_obs = [o for o in selected if is_cny_date(o.order_date, cfg)]
    cny_years = {o.order_date.year for o in observations}
    cny_days = 0
    for year in cny_years:
        window = cny_window(year, cfg)
        if window:
            cny_days += sum(1 for _ in _days(*window))
    cny_units = sum(o.effective_qty for o in cny_obs)
    cny_daily = (cny_units / cny_days) if cny_days else 0.0

    return {
        "normal_daily": round(normal_daily, 4),
        "short_daily": round(short_daily, 4),
        "long_daily": round(long_daily, 4),
        "window_daily": {str(k): round(v, 4) for k, v in rates.items()},
        "window_units": {str(k): round(v, 2) for k, v in units.items()},
        "sale_days": {str(k): v for k, v in sale_days.items()},
        "cny_daily": round(cny_daily, 4),
        "cny_units": cny_units,
        "cny_days": cny_days,
        "anomalies": [
            {"order_no": o.order_no, "raw_qty": o.raw_qty, "reason": o.anomaly}
            for o in observations if o.anomaly
        ],
    }


def forecast_daily(profile: dict, target: date, cfg: Optional[dict] = None) -> float:
    normal = float(profile.get("normal_daily") or 0)
    if is_cny_date(target, cfg):
        cny = float(profile.get("cny_daily") or 0)
        return cny if cny > 0 else normal * _as_float((cfg or {}).get("cny_fallback_factor"), 0.25)
    return normal * promo_factor_for_date(target, cfg)


def forecast_period(
    profile: dict, start: date, end: date, cfg: Optional[dict] = None,
) -> float:
    return sum(forecast_daily(profile, d, cfg) for d in _days(start, end))


def profile_for_product(
    db: Session, product_code: str, *, as_of: Optional[date] = None,
    cfg: Optional[dict] = None, sku_contains: Optional[str] = None,
    kind: str = "standard",
) -> dict:
    as_of = as_of or date.today()
    # 从 2026-01-01 读起，保留历史春节场景；普通基线仍只使用最近 90 天。
    start = min(as_of - timedelta(days=89), date(2026, 1, 1))
    variants = product_coder.brand_variants(product_code) or {product_code}
    obs = load_observations(db, start=start, end=as_of, cfg=cfg, product_codes=variants)
    if sku_contains:
        obs = [o for o in obs if sku_contains in (o.sku or "")]
    return build_profile(obs, as_of=as_of, cfg=cfg, kind=kind)


def profiles_by_sku(
    db: Session, *, as_of: Optional[date] = None,
    cfg: Optional[dict] = None, kind: str = "standard",
) -> list[dict]:
    as_of = as_of or date.today()
    start = min(as_of - timedelta(days=89), date(2026, 1, 1))
    observations = load_observations(db, start=start, end=as_of, cfg=cfg)
    groups: dict[tuple[str, str], list[DemandObservation]] = {}
    for o in observations:
        if o.kind != kind:
            continue
        core = product_coder.core_of(o.product_code) or o.product_code
        key = (core, o.sku_code or o.sku or o.product_code)
        groups.setdefault(key, []).append(o)
    out = []
    for (core, sku_key), rows in groups.items():
        p = build_profile(rows, as_of=as_of, cfg=cfg, kind=kind)
        latest = max(rows, key=lambda x: x.order_date)
        out.append({
            **p,
            "product_core": core,
            "product_code": latest.product_code,
            "product_name": latest.product_name,
            "sku_code": latest.sku_code,
            "sku": latest.sku,
            "sku_key": sku_key,
        })
    return sorted(out, key=lambda x: x["normal_daily"], reverse=True)


def current_unshipped_standard_qty(
    db: Session, product_code: str, *, cfg: Optional[dict] = None,
    sku_contains: Optional[str] = None,
) -> float:
    variants = product_coder.brand_variants(product_code) or {product_code}
    stmt = select(Order).where(
        Order.product_code.in_(variants),
        Order.ship_date.is_(None),
        Order.is_refill == False,  # noqa: E712
        Order.status.in_(_SETTLED_STATUSES),
    )
    placeholders = _placeholder_codes(db)
    total = 0
    for o in db.execute(stmt).scalars().all():
        if not _is_settled(o):
            continue
        row = classify_order(o, placeholder_codes=placeholders, cfg=cfg)
        if row.kind == "standard" and (not sku_contains or sku_contains in row.sku):
            total += row.effective_qty
    return float(total)


def clean_daily_series(
    db: Session, product_code: str, *, days: int,
    cfg: Optional[dict] = None, sku_contains: Optional[str] = None,
) -> list[float]:
    as_of = date.today()
    start = as_of - timedelta(days=days - 1)
    variants = product_coder.brand_variants(product_code) or {product_code}
    obs = load_observations(db, start=start, end=as_of, cfg=cfg, product_codes=variants)
    by_day: dict[date, float] = {}
    for o in obs:
        if o.kind != "standard" or (sku_contains and sku_contains not in o.sku):
            continue
        if is_cny_date(o.order_date, cfg):
            continue
        by_day[o.order_date] = by_day.get(o.order_date, 0.0) + (
            o.effective_qty / promo_factor_for_date(o.order_date, cfg)
        )
    return [by_day.get(as_of - timedelta(days=i), 0.0) for i in range(days)]


def product_normal_daily_map(
    db: Session, *, cfg: Optional[dict] = None, as_of: Optional[date] = None,
) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in profiles_by_sku(db, as_of=as_of, cfg=cfg, kind="standard"):
        core = row["product_core"]
        out[core] = out.get(core, 0.0) + float(row["normal_daily"])
    return out


def sync_quantity_anomalies(
    db: Session, *, cfg: Optional[dict] = None, as_of: Optional[date] = None,
    lookback_days: int = 90,
) -> dict:
    """把 4~5 件提醒和 >5 件隔离结果写入异常中心，供人工确认真实批量采购。"""
    from app.models.exception import DataException

    as_of = as_of or date.today()
    observations = load_observations(
        db,
        start=as_of - timedelta(days=max(1, lookback_days) - 1),
        end=as_of,
        cfg=cfg,
    )
    current = {str(o.order_id): o for o in observations if o.anomaly}
    existing = {
        str(row.source_pk): row
        for row in db.execute(
            select(DataException).where(
                DataException.source_table == "orders",
                DataException.exception_type == "inventory_demand_qty_anomaly",
            )
        ).scalars()
    }
    created = updated = resolved = 0
    for source_pk, obs in current.items():
        row = existing.get(source_pk)
        description = (
            f"订单 {obs.order_no} 数量 {obs.raw_qty}，预测按 1 个定制生产任务隔离"
            if obs.anomaly == "qty_gt5"
            else f"订单 {obs.order_no} 数量 {obs.raw_qty}，按实际数量计算但需确认是否批量采购"
        )
        context = {
            "order_no": obs.order_no,
            "product_code": obs.product_code,
            "sku_code": obs.sku_code,
            "raw_qty": obs.raw_qty,
            "effective_qty": obs.effective_qty,
            "classification": obs.kind,
            "rule": obs.anomaly,
        }
        if row is None:
            db.add(DataException(
                source_table="orders",
                source_pk=source_pk,
                exception_type="inventory_demand_qty_anomaly",
                severity="warning",
                description=description,
                suggestion_action="确认是真实批量采购后，将订单号加入已确认批量订单清单。",
                context=context,
                status="open",
            ))
            created += 1
        else:
            changed = (
                row.description != description
                or row.context != context
                or row.status != "open"
            )
            row.description = description
            row.context = context
            row.status = "open"
            if changed:
                updated += 1
    for source_pk, row in existing.items():
        if row.status == "open" and source_pk not in current:
            row.status = "resolved"
            row.resolved_by = "inventory_demand_engine"
            row.resolved_at = as_of.isoformat()
            resolved += 1
    db.flush()
    return {
        "scanned": len(observations),
        "open": len(current),
        "created": created,
        "updated": updated,
        "resolved": resolved,
    }
