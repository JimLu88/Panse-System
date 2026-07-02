"""成品库存智能分析服务。

功能:
  compute_product_stats   — 按 product_code/sku 从订单历史推算日均销量、提前期等
  refresh_all_inventory   — 批量更新 ProductInventory 表的推算字段 (幂等, 可定时跑)
  get_inventory_with_stats — 返回带计算字段的库存列表 (用于 API 响应)

计算逻辑:
  日均销量 (daily_sales_30d)  = 近 30 天真实订单出货量 / 30
  提前期 (lead_time_days)     = 工厂订单 actual_delivery - order_date 中位数 (天)
  预警线 (reorder_point)      = safety_stock + lead_time_days × daily_sales_30d
  安全库存 (safety_stock)     = 若未手动设置: lead_time_days × daily_sales_30d × 1.5
  库存预警状态 (warning_status):
    critical — available_qty ≤ 0
    danger   — available_qty < reorder_point
    warning  — days_of_stock < slow_moving_threshold / 2 (快用完)
    excess   — days_of_stock > slow_moving_days (滞销)
    ok       — 正常
  备货量推荐 (auto_reorder_qty):
    = max(0, reorder_point × 2 - available_qty)  (补到预警线的 2 倍)
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.inventory import ProductInventory
from app.models.order import FactoryOrder, Order
from app.services import product_coder

_D = Decimal
_ZERO = _D("0")
_DEFAULT_SLOW_MOVING_DAYS = 60
# 一般家具的默认提前期(天): 既无手填也无工厂历史时, 用它测算安全库存/预警线。
# 实木定制家具下单到入库通常 2~4 周, 取 30 天稳健兜底。
_DEFAULT_LEAD_TIME_DAYS = 30


# ── 销量公式配置 (用户拍板 2026-06-11: 默认加权 — 越近的日期权重越高; 大促时段可配) ──
_DEFAULT_PROMO_PERIODS = [
    {"name": "618 大促", "start": "05-13", "end": "06-18"},
    {"name": "双11 大促", "start": "10-20", "end": "11-13"},
]


def get_forecast_config(db: Session) -> dict:
    """日均销量公式 + 大促时段配置 (存 system_settings, 可在成品库存页编辑)。"""
    import json
    from app.services import settings_service
    mode = settings_service.get(db, "daily_sales_mode", env_fallback=False) or "weighted"
    try:
        halflife = int(settings_service.get(db, "daily_sales_halflife", env_fallback=False) or 14)
        window = int(settings_service.get(db, "daily_sales_window", env_fallback=False) or 60)
    except ValueError:
        halflife, window = 14, 60
    raw = settings_service.get(db, "promo_periods", env_fallback=False)
    try:
        periods = json.loads(raw) if raw else _DEFAULT_PROMO_PERIODS
        if not isinstance(periods, list):
            periods = _DEFAULT_PROMO_PERIODS
    except Exception:
        periods = _DEFAULT_PROMO_PERIODS
    # 备货策略参数 (方向4 ABC分层 + 方向2 服务水平安全库存 + 批量)
    def _f(key, dflt):
        try:
            v = settings_service.get(db, key, env_fallback=False)
            return float(v) if v not in (None, "") else dflt
        except (ValueError, TypeError):
            return dflt
    service_level = _f("stock_service_level", 0.95)      # 目标服务水平 → 安全库存 Z 值
    batch_cover_days = _f("stock_batch_cover_days", 30)  # A类每批备货覆盖天数(凑批压价)
    abc_a_share = _f("stock_abc_a_share", 0.80)          # 累计销量占比 ≤ 此 = A类
    abc_b_share = _f("stock_abc_b_share", 0.95)          # ≤ 此 = B类, 其余 C类
    stock_min_daily = _f("stock_min_daily", 0.2)         # A类还需日均≥此才备货(防长尾误入)
    # R5 半成品/白坯: 默认关闭。关=统一按标品备货(只跑R1+R2+R3); 开=出半成品(白坯)备货计划+产品打标。
    # 打开是"以后量大 + 与工厂协商好"才做的事(现在囤白胚工厂会有意见), 先建能力、默认不启用。
    _semi = settings_service.get(db, "enable_semi_finished", env_fallback=False)
    enable_semi_finished = (str(_semi).strip().lower() in ("1", "true", "yes", "on")
                            if _semi not in (None, "") else False)
    return {"mode": mode, "halflife_days": halflife, "window_days": window,
            "promo_periods": periods, "service_level": service_level,
            "batch_cover_days": batch_cover_days, "abc_a_share": abc_a_share,
            "abc_b_share": abc_b_share, "stock_min_daily": stock_min_daily,
            "enable_semi_finished": enable_semi_finished}


def save_forecast_config(db: Session, cfg: dict) -> dict:
    import json
    from app.services import settings_service
    if cfg.get("mode") in ("weighted", "simple"):
        settings_service.set_value(db, "daily_sales_mode", cfg["mode"])
    if cfg.get("halflife_days"):
        settings_service.set_value(db, "daily_sales_halflife", str(int(cfg["halflife_days"])))
    if cfg.get("window_days"):
        settings_service.set_value(db, "daily_sales_window", str(int(cfg["window_days"])))
    if isinstance(cfg.get("promo_periods"), list):
        settings_service.set_value(db, "promo_periods", json.dumps(cfg["promo_periods"], ensure_ascii=False))
    for k in ("service_level", "batch_cover_days", "abc_a_share", "abc_b_share", "stock_min_daily"):
        if cfg.get(k) is not None:
            settings_service.set_value(db, f"stock_{k}" if not k.startswith("stock_") else k, str(cfg[k]))
    if cfg.get("enable_semi_finished") is not None:   # R5 半成品开关(默认关)
        settings_service.set_value(db, "enable_semi_finished",
                                   "1" if cfg["enable_semi_finished"] else "0")
    return get_forecast_config(db)


def _resolve_period(p: dict, year: int) -> Optional[tuple[date, date]]:
    try:
        sm, sd = (int(x) for x in str(p.get("start", "")).split("-"))
        em, ed = (int(x) for x in str(p.get("end", "")).split("-"))
        s = date(year, sm, sd)
        e = date(year, em, ed)
        if e < s:   # 跨年时段 (如 12-20 ~ 01-05)
            e = date(year + 1, em, ed)
        return s, e
    except (ValueError, TypeError):
        return None


def promo_status(db: Session, *, prep_days: int = 30) -> dict:
    """大促备货状态: 当前是否在大促期内 / 是否进入备货窗口 (大促开始前 prep_days 天)。

    用户口径: 大促前要提前备货, 大促后销量会急剧下降, 不能只看平均数。
    """
    cfg = get_forecast_config(db)
    today = date.today()
    active, upcoming = [], []
    for p in cfg["promo_periods"]:
        for year in (today.year - 1, today.year, today.year + 1):
            r = _resolve_period(p, year)
            if not r:
                continue
            s, e = r
            if s <= today <= e:
                active.append({"name": p.get("name"), "start": s.isoformat(), "end": e.isoformat()})
            elif s - timedelta(days=prep_days) <= today < s:
                upcoming.append({"name": p.get("name"), "start": s.isoformat(),
                                 "end": e.isoformat(), "days_to_start": (s - today).days})
    return {"active": active, "upcoming": upcoming, "prep_days": prep_days}


def _compute_daily_sales(db: Session, product_code: str, sku: Optional[str] = None,
                         days: int = 30, cfg: Optional[dict] = None) -> float:
    """该产品的日均发货量 (产品级, 所有尺寸合计)。

    公式 (成品库存页可改, 存 system_settings):
      weighted (默认) — 指数加权移动平均: 每天销量乘 0.5^(距今天数/半衰期),
                        越近的日期权重越高 (用户拍板)。窗口/半衰期可配。
      simple           — 旧口径: 窗口期总量 ÷ 窗口天数。
    注: 按 product_code(含 PPS/PFG/P 品牌变体)汇总到产品级。不排除 is_historical。
    """
    if cfg is None:
        cfg = get_forecast_config(db)
    window = int(cfg.get("window_days") or days or 60)
    cutoff = date.today() - timedelta(days=window)
    pc_candidates = product_coder.brand_variants(product_code) or {product_code}
    base_filters = (
        Order.product_code.in_(pc_candidates),
        Order.is_refill == False,  # noqa: E712  补单不算真实销量
        Order.is_custom == False,  # noqa: E712  定制单不备成品(不能预产), 只算常规订单需求
        Order.order_date >= cutoff,
        Order.status.notin_(["cancelled", "pending_payment"]),
        ~Order.status.like("%关闭%"),
        ~Order.status.like("%取消%"),
        ~Order.status.like("%等待买家付款%"),
    )
    if cfg.get("mode") == "simple":
        total = float(db.execute(
            select(func.coalesce(func.sum(Order.qty), 0)).where(*base_filters)
        ).scalar() or 0)
        return round(total / window, 3)
    # weighted: 按天聚合后做指数衰减加权
    halflife = max(1, int(cfg.get("halflife_days") or 14))
    rows = db.execute(
        select(Order.order_date, func.coalesce(func.sum(Order.qty), 0))
        .where(*base_filters).group_by(Order.order_date)
    ).all()
    today = date.today()
    weighted_sum = 0.0
    for d, qty in rows:
        if d is None:
            continue
        age = (today - d).days
        weighted_sum += float(qty) * (0.5 ** (age / halflife))
    # 归一化分母 = 窗口内每天的权重和 → 结果仍是"每天卖几件"的口径
    denom = sum(0.5 ** (a / halflife) for a in range(window))
    return round(weighted_sum / denom, 3) if denom else 0.0


def _z_for_service_level(p: float) -> float:
    """目标服务水平 → 正态分位 Z (安全库存用)。常用档位查表, 取最近的一档。"""
    table = [(0.50, 0.0), (0.80, 0.84), (0.85, 1.04), (0.90, 1.28), (0.93, 1.48),
             (0.95, 1.65), (0.975, 1.96), (0.98, 2.05), (0.99, 2.33), (0.995, 2.58)]
    p = min(0.995, max(0.50, float(p)))
    return min(table, key=lambda t: abs(t[0] - p))[1]


def _canon_code(pc: str) -> str:
    """把品牌变体(PPS/PFG/P…)归并到一个稳定代表码, 供 ABC 聚合/查询一致。"""
    vs = product_coder.brand_variants(pc) or {pc}
    return min(vs)


def _daily_series(db: Session, product_code: str, cfg: dict) -> list[float]:
    """窗口内按天发货量序列(缺销当天补0), 用于算日销标准差 σ。"""
    window = int(cfg.get("window_days") or 60)
    cutoff = date.today() - timedelta(days=window)
    pc_candidates = product_coder.brand_variants(product_code) or {product_code}
    rows = db.execute(
        select(Order.order_date, func.coalesce(func.sum(Order.qty), 0)).where(
            Order.product_code.in_(pc_candidates),
            Order.is_refill == False,  # noqa: E712
            Order.is_custom == False,  # noqa: E712  定制单不备成品
            Order.order_date >= cutoff,
            Order.status.notin_(["cancelled", "pending_payment"]),
            ~Order.status.like("%关闭%"), ~Order.status.like("%取消%"),
            ~Order.status.like("%等待买家付款%"),
        ).group_by(Order.order_date)
    ).all()
    by_day = {d: float(q) for d, q in rows if d is not None}
    today = date.today()
    return [by_day.get(today - timedelta(days=i), 0.0) for i in range(window)]


def _std(series: list[float]) -> float:
    n = len(series)
    if n < 2:
        return 0.0
    m = sum(series) / n
    return (sum((x - m) ** 2 for x in series) / (n - 1)) ** 0.5


def compute_abc_map(db: Session, cfg: Optional[dict] = None) -> dict:
    """按窗口真实销量做 ABC 分层(方向4): 产品按销量降序累计, ≤A线=A / ≤B线=B / 其余=C。
    只有 A 类进入自动备货。返回 {规范product_code: 'A'|'B'|'C'}。"""
    if cfg is None:
        cfg = get_forecast_config(db)
    window = int(cfg.get("window_days") or 60)
    cutoff = date.today() - timedelta(days=window)
    rows = db.execute(
        select(Order.product_code, func.coalesce(func.sum(Order.qty), 0)).where(
            Order.is_refill == False,  # noqa: E712
            Order.is_custom == False,  # noqa: E712  ABC 只按常规订单排(定制不备成品)
            Order.order_date >= cutoff,
            Order.status.notin_(["cancelled", "pending_payment"]),
            ~Order.status.like("%关闭%"), ~Order.status.like("%取消%"),
            ~Order.status.like("%等待买家付款%"),
        ).group_by(Order.product_code)
    ).all()
    agg: dict = {}
    for pc, q in rows:
        if pc:
            agg[_canon_code(pc)] = agg.get(_canon_code(pc), 0.0) + float(q)
    total = sum(agg.values())
    if total <= 0:
        return {}
    a_line, b_line = cfg["abc_a_share"] * total, cfg["abc_b_share"] * total
    out, cum = {}, 0.0
    for pc, q in sorted(agg.items(), key=lambda x: x[1], reverse=True):
        cum += q
        out[pc] = "A" if cum <= a_line else ("B" if cum <= b_line else "C")
    return out


def compute_in_production_split(db: Session) -> tuple[dict, dict]:
    """在产/在途(未到货未作废工厂单)拆两类(完整 ATP), 按规范产品码归集:
       free = 备货单(source_order_id 空, 会入库) → 抵推荐备货;
       allocated = 客户单MTO(source_order_id 有值, 发客户) → 不抵、仅展示。
    与备货建议 sales_analytics._in_production_split 同口径。返回 (free_map, allocated_map)。
    """
    rows = db.execute(
        select(FactoryOrder.product_code, FactoryOrder.source_order_id,
               func.coalesce(func.sum(FactoryOrder.qty), 0)).where(
            FactoryOrder.actual_delivery.is_(None),
            FactoryOrder.voided_at.is_(None),
            FactoryOrder.product_code.isnot(None),
        ).group_by(FactoryOrder.product_code, FactoryOrder.source_order_id)
    ).all()
    free: dict = {}
    alloc: dict = {}
    for pc, source_order_id, qty in rows:
        if not pc:
            continue
        c = _canon_code(pc)
        if source_order_id is None:
            free[c] = free.get(c, 0.0) + float(qty or 0)
        else:
            alloc[c] = alloc.get(c, 0.0) + float(qty or 0)
    return free, alloc


def _compute_lead_time(db: Session, product_code: str) -> Optional[int]:
    """从工厂订单历史推算中位提前期（天）。只用有完整日期的记录。"""
    rows = db.execute(
        select(FactoryOrder.order_date, FactoryOrder.actual_delivery).where(
            FactoryOrder.product_code == product_code,
            FactoryOrder.order_date.isnot(None),
            FactoryOrder.actual_delivery.isnot(None),
            FactoryOrder.voided_at.is_(None),
        )
    ).all()
    if not rows:
        return None
    deltas = [(r.actual_delivery - r.order_date).days for r in rows if r.actual_delivery >= r.order_date]
    if not deltas:
        return None
    return int(median(deltas))


def compute_product_stats(
    db: Session,
    inv: ProductInventory,
    abc_map: Optional[dict] = None,
    cfg: Optional[dict] = None,
    in_production_split: Optional[tuple] = None,
) -> dict:
    """计算单条成品库存的推算字段(方向4 ABC分层 + 方向2 服务水平安全库存 + 批量备货)。

    - ABC: 只有 A 类畅销款自动备货; B/C 类按需生产(MTO), 推荐=0、不报缺货警(缺货是常态)。
      手动设过 安全库存/预警线 的行视为"要备"→ 无视 ABC 照常备货(留人工兜底口)。
    - A 类安全库存 = Z(服务水平) × 日销标准差σ × √提前期 (按波动定, 稳的少备、波动大多备);
      预警线 = 日均×提前期 + 安全库存; 推荐 = 补到(预警线 + 批量), 批量=覆盖N天(凑批压配件价)。
    - R1(ATP): 推荐备货再扣掉「在产/在途」(已下工厂未到货), 避免对在做的量重复下单。
    """
    if cfg is None:
        cfg = get_forecast_config(db)
    if abc_map is None:
        abc_map = compute_abc_map(db, cfg)
    if in_production_split is None:
        in_production_split = compute_in_production_split(db)
    free_map, alloc_map = in_production_split
    daily = _compute_daily_sales(db, inv.product_code, inv.sku, cfg=cfg)
    abc_class = abc_map.get(_canon_code(inv.product_code), "C")
    in_prod_free = float(free_map.get(_canon_code(inv.product_code), 0.0))   # 备货在产, 抵推荐
    in_prod_alloc = float(alloc_map.get(_canon_code(inv.product_code), 0.0)) # 客户单在产, 仅展示

    lead_time = inv.lead_time_days
    if lead_time is None:
        lead_time = _compute_lead_time(db, inv.product_code)
    effective_lead = lead_time if lead_time is not None else _DEFAULT_LEAD_TIME_DAYS
    slow_days = inv.slow_moving_days or _DEFAULT_SLOW_MOVING_DAYS
    available = float(inv.available_qty)

    # 是否自动备货: A类畅销 且 日均≥下限; 或人工设过安全库存/预警线(强制备货口)
    manual = inv.safety_stock is not None or inv.reorder_point is not None
    do_stock = manual or (abc_class == "A" and daily >= float(cfg.get("stock_min_daily", 0.2)))

    # 安全库存: 手填优先; 否则 A类=服务水平统计法(Z×σ×√提前期), 非备货类=0
    safety = float(inv.safety_stock or 0)
    if inv.safety_stock is None and do_stock:
        z = _z_for_service_level(cfg.get("service_level", 0.95))
        sigma = _std(_daily_series(db, inv.product_code, cfg))
        safety = round(z * sigma * (effective_lead ** 0.5), 2)

    # 预警线: 手填优先; 否则 A类=日均×提前期+安全库存; 非备货类=0(不报缺货)
    if inv.reorder_point is not None:
        reorder_pt = float(inv.reorder_point)
    elif do_stock:
        reorder_pt = round(effective_lead * daily + safety, 2)
    else:
        reorder_pt = 0.0

    days_of_stock: Optional[float] = round(available / daily, 1) if daily > 0 else None

    # 警告状态: 非备货(MTO)类缺货是常态不报警, 只标 滞销/按需
    if not do_stock:
        status = "excess" if (days_of_stock is not None and available > 0
                              and days_of_stock > slow_days) else "mto"
    elif available <= 0:
        status = "critical"
    elif reorder_pt > 0 and available < reorder_pt:
        status = "danger"
    elif days_of_stock is not None and days_of_stock < slow_days / 2:
        status = "warning"
    elif days_of_stock is not None and days_of_stock > slow_days:
        status = "excess"
    else:
        status = "ok"

    # 推荐备货: 只有备货类且低于预警线才补; 补到 目标位=预警线+批量(覆盖N天, 凑批压价)
    # R1(ATP): 只扣「自由在产(备货单, 会入库)」; 客户单在产是发给下单客户的, 不抵、避免误消成 0
    auto_reorder = 0.0
    if do_stock and reorder_pt > 0 and available < reorder_pt:
        batch = float(cfg.get("batch_cover_days", 30)) * daily
        auto_reorder = max(0.0, (reorder_pt + batch) - available - in_prod_free)

    return {
        "daily_sales_30d": daily,
        "lead_time_days_computed": lead_time,
        "safety_stock_computed": round(safety, 2),
        "reorder_point_computed": round(reorder_pt, 2),
        "available_qty": round(available, 2),
        "in_production_free": round(in_prod_free, 2),        # 备货在产(会入库, 已抵推荐)
        "in_production_allocated": round(in_prod_alloc, 2),  # 客户单在产(发客户, 仅展示)
        "days_of_stock": days_of_stock,
        "warning_status": status,
        "auto_reorder_qty": round(auto_reorder, 0),
        "slow_moving_days": slow_days,
        "abc_class": abc_class,
    }


def refresh_all_inventory(db: Session) -> int:
    """批量刷新推算字段。幂等。

    ⚠只回填 lead_time_days(稳定, 便于展示); **不再自动回填 安全库存/预警线** ——
    这两者现按 ABC+服务水平动态算, 自动写库会被误判成"人工设置"从而绕过 ABC 分层。
    """
    cfg = get_forecast_config(db)
    abc_map = compute_abc_map(db, cfg)
    in_prod_split = compute_in_production_split(db)
    rows = db.execute(select(ProductInventory)).scalars().all()
    updated = 0
    for inv in rows:
        stats = compute_product_stats(db, inv, abc_map=abc_map, cfg=cfg, in_production_split=in_prod_split)
        if inv.lead_time_days is None and stats["lead_time_days_computed"] is not None:
            inv.lead_time_days = stats["lead_time_days_computed"]
        updated += 1
    db.flush()
    return updated
