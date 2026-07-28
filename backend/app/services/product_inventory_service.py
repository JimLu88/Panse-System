"""成品库存智能分析服务。

功能:
  compute_product_stats   — 按 product_code/sku 从订单历史推算日均销量、提前期等
  refresh_all_inventory   — 批量更新 ProductInventory 表的推算字段 (幂等, 可定时跑)
  get_inventory_with_stats — 返回带计算字段的库存列表 (用于 API 响应)

计算逻辑:
  近30天日均 (daily_sales_30d) = 近 30 天统一清洗销量 / 30
  预测日均 (forecast_daily)    = 7/15/30/60/90 天近端加权并去除大促峰值
  提前期 (lead_time_days)     = 工厂订单 actual_delivery - order_date 中位数 (天)
  目标库存 (target_stock)      = 常规可备产品未来完整 30 天预测；大件/定制为 0
  库存预警状态 (warning_status):
    critical — available_qty ≤ 0
    danger   — available_qty < reorder_point
    warning  — days_of_stock < slow_moving_threshold / 2 (快用完)
    excess   — days_of_stock > slow_moving_days (滞销)
    ok       — 正常
  备货量推荐 (auto_reorder_qty):
    = max(0, target_stock - 当前现货 - 自由在产)
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

# 从库存行的 sku 名里抽尺寸口令(如 1.4米 / 80cm / 1200mm), 用来把日均/波动算到「该尺寸」自己头上,
# 不再让同产品各尺寸共用产品总日均(否则每个尺寸都按整产品销量备货, 合计翻几倍)。
_SIZE_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:米|cm|mm|CM|MM|m|M)")


def _size_token(name: Optional[str]) -> Optional[str]:
    """从 sku 名抽出尺寸口令(如 '榉木餐桌-1.4米' → '1.4米'); 抽不到返回 None(退回产品级)。"""
    if not name:
        return None
    m = _SIZE_RE.search(str(name))
    return m.group(0).replace(" ", "") if m else None

from app.models.inventory import ProductInventory
from app.models.order import FactoryOrder, Order
from app.models.product import Product
from app.services import product_coder

_D = Decimal
_ZERO = _D("0")
_DEFAULT_SLOW_MOVING_DAYS = 60
# 一般家具的默认提前期(天): 既无手填也无工厂历史时, 用它测算安全库存/预警线。
# 实木定制家具下单到入库通常 2~4 周, 取 30 天稳健兜底。
_DEFAULT_LEAD_TIME_DAYS = 30

# 重点备货月季节系数种子(用户经验值, 相对平常月=1.0): 1..12 月
_DEFAULT_SEASONAL_FACTORS = [1.0, 0.3, 1.0, 1.0, 6.0, 6.0, 0.7, 1.0, 1.0, 5.0, 7.0, 5.0]


# ── 销量公式配置 (用户拍板 2026-06-11: 默认加权 — 越近的日期权重越高; 大促时段可配) ──
_DEFAULT_PROMO_PERIODS = [
    {"key": "618", "name": "618", "start": "05-20", "end": "06-20", "multiplier": 3.0},
    {"key": "double11", "name": "双11", "start": "10-20", "end": "11-13", "multiplier": 4.25},
    {"key": "double12", "name": "双12", "start": "12-01", "end": "12-12", "multiplier": 2.125},
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
    raw = settings_service.get(db, "inventory_promo_periods_v2", env_fallback=False)
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
    # 重点备货月 / 季节系数: 12 个月各一个相对系数(平常月=1.0, 峰月 5-8, 淡月<1)。
    # 备货用「目标月(今天+提前期落在的月)」的系数, 把最近日均去季节化后按目标月放大/压缩,
    # 从而 4 月自动为 5-6 月峰期备货、7 月自动比 6 月峰回落。默认种子(用户经验值):
    #   1月1 / 2月0.3(过年) / 3-4月1 / 5-6月6(618) / 7月0.7(618后) / 8-9月1 / 10月5 / 11月7(双11) / 12月5
    raw_sf = settings_service.get(db, "seasonal_factors", env_fallback=False)
    try:
        sf = json.loads(raw_sf) if raw_sf else list(_DEFAULT_SEASONAL_FACTORS)
        if not (isinstance(sf, list) and len(sf) == 12):
            sf = list(_DEFAULT_SEASONAL_FACTORS)
        sf = [max(0.0, float(x)) for x in sf]
    except Exception:
        sf = list(_DEFAULT_SEASONAL_FACTORS)
    _es = settings_service.get(db, "enable_seasonal", env_fallback=False)
    enable_seasonal = (str(_es).strip().lower() in ("1", "true", "yes", "on")
                       if _es not in (None, "") else True)   # 默认开(用户明确要季节备货)
    _sa = settings_service.get(db, "seasonal_auto", env_fallback=False)
    seasonal_auto = (str(_sa).strip().lower() in ("1", "true", "yes", "on")
                     if _sa not in (None, "") else False)    # 自动进化默认关(历史数据够了再开)
    raw_bulk = settings_service.get(db, "inventory_confirmed_bulk_order_nos", env_fallback=False)
    try:
        confirmed_bulk = json.loads(raw_bulk) if raw_bulk else []
        if not isinstance(confirmed_bulk, list):
            confirmed_bulk = []
    except Exception:
        confirmed_bulk = []
    return {"mode": "hybrid_momentum", "legacy_mode": mode,
            "halflife_days": halflife, "window_days": 90,
            "promo_periods": periods, "promo_periods_v2": periods,
            "confirmed_bulk_order_nos": confirmed_bulk,
            "cny_before_days": 14, "cny_after_days": 15, "cny_fallback_factor": 0.25,
            "service_level": service_level,
            "batch_cover_days": batch_cover_days, "abc_a_share": abc_a_share,
            "abc_b_share": abc_b_share, "stock_min_daily": stock_min_daily,
            "enable_semi_finished": enable_semi_finished,
            "seasonal_factors": sf, "enable_seasonal": enable_seasonal,
            "seasonal_auto": seasonal_auto}


def save_forecast_config(db: Session, cfg: dict) -> dict:
    import json
    from app.services import settings_service
    if cfg.get("mode") in ("weighted", "simple"):
        settings_service.set_value(db, "daily_sales_mode", cfg["mode"])
    if cfg.get("halflife_days"):
        settings_service.set_value(db, "daily_sales_halflife", str(int(cfg["halflife_days"])))
    if cfg.get("window_days"):
        settings_service.set_value(db, "daily_sales_window", str(int(cfg["window_days"])))
    periods = cfg.get("promo_periods_v2", cfg.get("promo_periods"))
    if isinstance(periods, list):
        settings_service.set_value(
            db, "inventory_promo_periods_v2", json.dumps(periods, ensure_ascii=False))
    if isinstance(cfg.get("confirmed_bulk_order_nos"), list):
        settings_service.set_value(
            db, "inventory_confirmed_bulk_order_nos",
            json.dumps([str(x) for x in cfg["confirmed_bulk_order_nos"]], ensure_ascii=False))
    for k in ("service_level", "batch_cover_days", "abc_a_share", "abc_b_share", "stock_min_daily"):
        if cfg.get(k) is not None:
            settings_service.set_value(db, f"stock_{k}" if not k.startswith("stock_") else k, str(cfg[k]))
    if cfg.get("enable_semi_finished") is not None:   # R5 半成品开关(默认关)
        settings_service.set_value(db, "enable_semi_finished",
                                   "1" if cfg["enable_semi_finished"] else "0")
    sf = cfg.get("seasonal_factors")                  # 重点备货月 季节系数(12个)
    if isinstance(sf, list) and len(sf) == 12:
        settings_service.set_value(db, "seasonal_factors",
                                   json.dumps([max(0.0, float(x)) for x in sf]))
    if cfg.get("enable_seasonal") is not None:
        settings_service.set_value(db, "enable_seasonal",
                                   "1" if cfg["enable_seasonal"] else "0")
    if cfg.get("seasonal_auto") is not None:
        settings_service.set_value(db, "seasonal_auto",
                                   "1" if cfg["seasonal_auto"] else "0")
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


def _compute_sales_profile(
    db: Session, product_code: str, sku: Optional[str] = None,
    cfg: Optional[dict] = None,
) -> dict:
    """返回该产品/尺寸统一清洗后的销量画像。"""
    if cfg is None:
        cfg = get_forecast_config(db)
    from app.services import inventory_demand_service as demand
    return demand.profile_for_product(
        db,
        product_code,
        cfg=cfg,
        sku_contains=_size_token(sku),
        kind="standard",
    )


def _compute_daily_sales(db: Session, product_code: str, sku: Optional[str] = None,
                         days: int = 30, cfg: Optional[dict] = None) -> float:
    """该产品的日均发货量 (产品级, 所有尺寸合计)。

    公式 (成品库存页可改, 存 system_settings):
      weighted (默认) — 指数加权移动平均: 每天销量乘 0.5^(距今天数/半衰期),
                        越近的日期权重越高 (用户拍板)。窗口/半衰期可配。
      simple           — 旧口径: 窗口期总量 ÷ 窗口天数。
    注: 按 product_code(含 PPS/PFG/P 品牌变体)汇总到产品级。不排除 is_historical。
    """
    profile = _compute_sales_profile(db, product_code, sku, cfg)
    return round(float(profile["normal_daily"]), 3)


def _unshipped_demand(db: Session, product_code: str, sku: Optional[str] = None) -> float:
    """该成品被『已付未发』常规订单占用的量 (用户 2026-07-05 口径: 可用=现货−已付未发)。

    - 已付 = 有实付入账(paid_amount>0)且非取消/非待付款; 未发 = ship_date 为空。
    - 只算常规订单(is_custom=False): 定制单现产不吃现货, 不占用; 补单(刷单)也排除。
    - 按 product_code(含品牌变体)聚合; 库存行有尺寸口令则只算该尺寸(与日均销量同口径)。
    - 发货后 ship_date 一填, 该单自动掉出本汇总 → 可用实时回补(负9→负8), 无需额外逻辑。
    """
    from app.services import inventory_demand_service as demand
    return demand.current_unshipped_standard_qty(
        db,
        product_code,
        cfg=get_forecast_config(db),
        sku_contains=_size_token(sku),
    )


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


def _daily_series(db: Session, product_code: str, cfg: dict,
                  sku: Optional[str] = None) -> list[float]:
    """窗口内按天发货量序列(缺销当天补0), 用于算日销标准差 σ。
    sku 带尺寸口令时只算该尺寸自己的波动(与 _compute_daily_sales 同口径)。"""
    from app.services import inventory_demand_service as demand
    return demand.clean_daily_series(
        db,
        product_code,
        days=int(cfg.get("window_days") or 90),
        cfg=cfg,
        sku_contains=_size_token(sku),
    )


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
    from app.services import inventory_demand_service as demand
    raw = demand.product_normal_daily_map(db, cfg=cfg)
    agg = {f"P{core}": qty for core, qty in raw.items() if qty > 0}
    total = sum(agg.values())
    if total <= 0:
        return {}
    a_line, b_line = cfg["abc_a_share"] * total, cfg["abc_b_share"] * total
    out, cum = {}, 0.0
    for pc, q in sorted(agg.items(), key=lambda x: x[1], reverse=True):
        before = cum
        cum += q
        out[pc] = "A" if before < a_line else ("B" if before < b_line else "C")
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


def _seasonal_effective_daily(cfg: dict, base_daily: float, effective_lead: int,
                              today: Optional[date] = None) -> tuple:
    """把「最近日均」换成「目标月预期日均」, 用于前瞻备货(重点备货月机制)。

    目标月 = 今天 + 提前期 落在的月(现在下的货那会儿到货、开始卖那个月)。
    公式: 目标日均 = 最近日均 × (目标月系数 ÷ 最近窗口平均系数)。
      · 分母把「当前这段时间的季节虚高/虚低」还原成常态(如 7 月分母含 6 月峰→大, 把最近日均压下来);
      · 分子按目标月放大/缩小(如 4 月目标是 5 月→系数高→放大, 提前为峰期备货)。
    未开启季节 / 系数全 1 → 原样返回。返回 (目标日均, 目标月, 倍数)。
    """
    if not cfg.get("enable_seasonal"):
        return base_daily, None, 1.0
    from app.services import inventory_demand_service as demand
    today = today or date.today()
    target = today + timedelta(days=int(effective_lead or _DEFAULT_LEAD_TIME_DAYS))
    effective = demand.forecast_daily(
        {"normal_daily": base_daily, "cny_daily": 0.0},
        target,
        cfg,
    )
    mult = (
        effective / base_daily
        if base_daily > 0
        else demand.promo_factor_for_date(target, cfg)
    )
    return effective, target.month, round(mult, 3)


def compute_product_stats(
    db: Session,
    inv: ProductInventory,
    abc_map: Optional[dict] = None,
    cfg: Optional[dict] = None,
    in_production_split: Optional[tuple] = None,
    restock_plan_row: Optional[dict] = None,
    restock_qty: Optional[float] = None,
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
    if restock_plan_row is None:
        from app.services import inventory_restock_service
        today = date.today()
        plan = inventory_restock_service.build_restock_plan(
            db,
            start=today + timedelta(days=1),
            end=today + timedelta(days=30),
            as_of=today,
        )
        restock_plan_row = inventory_restock_service.product_map(plan).get(
            product_coder.core_of(inv.product_code) or inv.product_code
        ) or {}
        if restock_plan_row is not None and restock_qty is None:
            restock_qty = float(restock_plan_row.get("suggested_restock") or 0)
    free_map, alloc_map = in_production_split
    sales_profile = _compute_sales_profile(db, inv.product_code, inv.sku, cfg)
    daily = round(float(sales_profile["normal_daily"]), 3)
    sales_qty_30d = float(sales_profile["actual_window_units"]["30"])
    sales_amount_30d = float(sales_profile["actual_window_sales"]["30"])
    actual_daily_30d = float(sales_profile["actual_daily_30d"])
    abc_class = abc_map.get(_canon_code(inv.product_code), "C")
    in_prod_free = float(free_map.get(_canon_code(inv.product_code), 0.0))   # 备货在产, 抵推荐
    in_prod_alloc = float(alloc_map.get(_canon_code(inv.product_code), 0.0)) # 客户单在产, 仅展示

    lead_time = inv.lead_time_days
    if lead_time is None:
        lead_time = _compute_lead_time(db, inv.product_code)
    effective_lead = lead_time if lead_time is not None else _DEFAULT_LEAD_TIME_DAYS
    slow_days = inv.slow_moving_days or _DEFAULT_SLOW_MOVING_DAYS
    # 展示「可用」= 现货 − 已付未发单(用户 2026-07-05 口径, 可负): 现货已被现有订单占用就不该显示有货。
    # 物理可用(现货−locked)另留一份, 只给『备货推荐』用 —— 推荐不受未发单影响, 防超卖单在产致过量下单。
    physical_available = float(inv.available_qty)
    unshipped = _unshipped_demand(db, inv.product_code, inv.sku)
    available = physical_available - unshipped

    # 重点备货月(季节前瞻): 备货「数量」用目标月(今天+提前期)的预期日均, 而不是最近日均 ——
    # 4月自动为5-6月峰备货、7月自动比6月峰回落。是否备货仍看真实近况, 只放大/缩小数量。
    daily_forward, season_target_month, season_mult = _seasonal_effective_daily(
        cfg, daily, effective_lead)

    # 是否自动备货: A类畅销 且 日均≥下限; 或人工设过安全库存/预警线(强制备货口)
    manual = inv.safety_stock is not None or inv.reorder_point is not None
    do_stock = manual or (abc_class == "A" and daily >= float(cfg.get("stock_min_daily", 0.2)))

    # 安全库存: 手填优先; 否则 A类=服务水平统计法(Z×σ×√提前期), 非备货类=0
    safety = float(inv.safety_stock or 0)
    if inv.safety_stock is None and do_stock:
        z = _z_for_service_level(cfg.get("service_level", 0.95))
        sigma = _std(_daily_series(db, inv.product_code, cfg, sku=inv.sku))
        safety = round(z * sigma * (effective_lead ** 0.5), 2)

    # 预警线: 手填优先; 否则 A类=日均×提前期+安全库存; 非备货类=0(不报缺货)
    if inv.reorder_point is not None:
        reorder_pt = float(inv.reorder_point)
    elif do_stock:
        reorder_pt = round(effective_lead * daily_forward + safety, 2)   # 季节前瞻日均
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
    # 用【物理可用(现货−locked)】而非展示可用: 已付未发单已在产/待产, 不该再叠进备货量(否则重复下单)。
    # R1(ATP): 只扣「自由在产(备货单, 会入库)」; 客户单在产是发给下单客户的, 不抵、避免误消成 0
    auto_reorder = 0.0
    if do_stock and reorder_pt > 0 and physical_available < reorder_pt:
        batch = float(cfg.get("batch_cover_days", 30)) * daily_forward   # 季节前瞻日均
        auto_reorder = max(0.0, (reorder_pt + batch) - physical_available - in_prod_free)

    # 最终建议只能来自唯一备货计划引擎。上面的安全库存/提前期继续作为解释性指标，
    # 但不得再产生第二套 auto_reorder_qty。
    if restock_plan_row is not None:
        auto_reorder = float(
            restock_plan_row.get("suggested_restock") or 0
            if restock_qty is None
            else restock_qty
        )
        target_stock = float(restock_plan_row.get("target_stock") or 0)
        reorder_pt = target_stock
        policy = restock_plan_row.get("policy") or "按需生产"
        if policy in ("大件按单生产", "按需生产") or not restock_plan_row.get("qualified_hot"):
            status = (
                "excess"
                if days_of_stock is not None and available > 0 and days_of_stock > slow_days
                else "mto"
            )
        elif auto_reorder > 0:
            status = "critical" if physical_available <= 0 else "danger"
        elif days_of_stock is not None and days_of_stock > slow_days:
            status = "excess"
        else:
            status = "ok"

    return {
        # 真实30天销量与预测日均分开显示，避免把加权预测误叫成“30天日均”。
        "daily_sales_30d": round(actual_daily_30d, 3),
        "sales_qty_30d": round(sales_qty_30d, 2),
        "sales_amount_30d": round(sales_amount_30d, 2),
        "forecast_daily": round(daily, 3),
        "lead_time_days_computed": lead_time,
        "safety_stock_computed": round(safety, 2),
        "reorder_point_computed": round(reorder_pt, 2),
        "available_qty": round(available, 2),                # 展示可用=现货−已付未发(可负, 用户口径)
        "physical_available": round(physical_available, 2),  # 物理可用=现货−locked(备货推荐口径)
        "unshipped_demand": round(unshipped, 2),             # 已付未发单占用(可用的扣减项)
        "in_production_free": round(in_prod_free, 2),        # 备货在产(会入库, 已抵推荐)
        "in_production_allocated": round(in_prod_alloc, 2),  # 客户单在产(发客户, 仅展示)
        "days_of_stock": days_of_stock,
        "warning_status": status,
        "auto_reorder_qty": round(auto_reorder, 0),
        "slow_moving_days": slow_days,
        "abc_class": abc_class,
        "season_target_month": season_target_month,   # 备货瞄准的月(今天+提前期); 未开季节=None
        "season_multiplier": season_mult,             # 目标月系数 ÷ 最近窗口平均系数
        "restock_policy": restock_plan_row.get("policy") if restock_plan_row else None,
        "target_stock": (
            float(restock_plan_row.get("target_stock") or 0)
            if restock_plan_row is not None
            else round(reorder_pt, 2)
        ),
        "qualified_hot": (
            bool(restock_plan_row.get("qualified_hot"))
            if restock_plan_row is not None
            else do_stock
        ),
        "forecast_30d": (
            int(restock_plan_row.get("forecast_30d") or 0)
            if restock_plan_row is not None
            else None
        ),
        "product_restock_total": (
            float(restock_plan_row.get("suggested_restock") or 0)
            if restock_plan_row is not None
            else round(auto_reorder, 0)
        ),
    }


def ensure_all_product_inventory_rows(
    db: Session, *, default_warehouse: Optional[str] = None,
) -> int:
    """确保产品主表里的每个产品至少有一条成品库存行。

    现有库存行完全保留；只为缺失产品补一条 0 库存基线。默认仓库沿用当前库存表
    使用最多的仓库，空库时回退“江西仓库”。该函数幂等。
    """
    existing_codes = set(
        db.execute(select(ProductInventory.product_code).distinct()).scalars().all()
    )
    if default_warehouse is None:
        warehouse_row = db.execute(
            select(ProductInventory.warehouse, func.count(ProductInventory.id))
            .group_by(ProductInventory.warehouse)
            .order_by(func.count(ProductInventory.id).desc(), ProductInventory.warehouse)
            .limit(1)
        ).first()
        default_warehouse = (
            str(warehouse_row[0]) if warehouse_row and warehouse_row[0] else "江西仓库"
        )
    created = 0
    for product in db.execute(
        select(Product).where(Product.code.notin_(existing_codes)).order_by(Product.code)
    ).scalars():
        db.add(ProductInventory(
            warehouse=default_warehouse,
            product_code=product.code,
            product_name=product.name,
            sku=product.sku,
            unit="件",
            physical_qty=Decimal("0"),
            locked_qty=Decimal("0"),
            slow_moving_days=_DEFAULT_SLOW_MOVING_DAYS,
            remark="系统自动建档（产品主表联动）",
        ))
        existing_codes.add(product.code)
        created += 1
    if created:
        db.flush()
    return created


def recompute_seasonal_factors(db: Session, *, min_units: int = 80) -> dict:
    """自动进化(种子→实测): 用历史标品成交件重算 12 个月季节系数, **只对攒够干净数据的月**给实测值,
    数据不足的月保留当前(手填种子)值。默认只「建议」不自动覆盖, 供人工确认后再保存。

    月系数 = 该月历史平均(各年份同月, 标品成交件) / 全月平均。数据够 = 该月累计成交件 ≥ min_units。
    返回 {factors(建议), current(现值), updated_months(用了实测的月), note}。
    """
    from collections import defaultdict
    from app.services.sales_analytics import _is_non_product
    cfg = get_forecast_config(db)
    cur = list(cfg.get("seasonal_factors") or _DEFAULT_SEASONAL_FACTORS)
    rows = db.execute(
        select(Order.order_date, Order.product_name, Order.qty).where(
            Order.order_date.isnot(None),
            Order.is_refill == False,  # noqa: E712
            Order.is_custom == False,  # noqa: E712
            Order.status.in_(["paid", "shipped", "signed"]),
        )
    ).all()
    ym: dict = defaultdict(int)
    for d, name, q in rows:
        if d is None:
            continue
        # 差价/邮费/补拍/专链 等非产品单: qty=拍的¥1件数(如补7660差价拍7660件), 不是家具件数,
        # 计入会把当月销量灌成天文数字(实测 2026-01 因此虚出 6 万+件) → 一律排除。
        if _is_non_product(name):
            continue
        ym[(d.year, d.month)] += int(q or 0)
    by_month: dict = defaultdict(list)
    for (y, m), v in ym.items():
        by_month[m].append(v)
    monthly_mean = {m: (sum(v) / len(v)) for m, v in by_month.items() if v}
    overall = (sum(monthly_mean.values()) / len(monthly_mean)) if monthly_mean else 0.0
    new = list(cur)
    updated: list = []
    if overall > 0:
        for m in range(1, 13):
            units = sum(by_month.get(m, []))
            if units >= min_units and m in monthly_mean:
                new[m - 1] = round(monthly_mean[m] / overall, 2)
                updated.append(m)
    return {"factors": new, "current": cur, "updated_months": updated,
            "note": (f"仅 {updated or '无'} 月数据够(≥{min_units}件)用实测值, 其余保留手填种子; "
                     f"数据攒满一年后建议再重算")}


def build_inventory_restock_context(
    db: Session, rows: Optional[list[ProductInventory]] = None,
    *, as_of: Optional[date] = None,
) -> tuple[dict[str, dict], dict[int, int]]:
    """一次生成库存页所需产品计划和逐库存行分配，避免每行重复跑全引擎。"""
    from app.services import inventory_restock_service

    today = as_of or date.today()
    plan = inventory_restock_service.build_restock_plan(
        db,
        start=today + timedelta(days=1),
        end=today + timedelta(days=30),
        as_of=today,
    )
    pmap = inventory_restock_service.product_map(plan)
    all_rows = rows
    if all_rows is None:
        all_rows = db.execute(
            select(ProductInventory).order_by(
                ProductInventory.product_code, ProductInventory.sku
            )
        ).scalars().all()
    groups: dict[str, list[tuple[ProductInventory, float]]] = {}
    cfg = get_forecast_config(db)
    for inv in all_rows:
        core = product_coder.core_of(inv.product_code) or inv.product_code
        weight = _compute_daily_sales(db, inv.product_code, inv.sku, cfg=cfg)
        groups.setdefault(core, []).append((inv, weight))
    allocation: dict[int, int] = {}
    for core, weighted_rows in groups.items():
        total = int((pmap.get(core) or {}).get("suggested_restock") or 0)
        allocation.update(
            inventory_restock_service.allocate_product_restock(total, weighted_rows)
        )
    return pmap, allocation


def refresh_all_inventory(db: Session) -> int:
    """批量刷新推算字段。幂等。

    ⚠只回填 lead_time_days(稳定, 便于展示); **不再自动回填 安全库存/预警线** ——
    这两者现按 ABC+服务水平动态算, 自动写库会被误判成"人工设置"从而绕过 ABC 分层。
    """
    cfg = get_forecast_config(db)
    abc_map = compute_abc_map(db, cfg)
    in_prod_split = compute_in_production_split(db)
    rows = db.execute(select(ProductInventory)).scalars().all()
    restock_map, restock_allocation = build_inventory_restock_context(db, rows)
    updated = 0
    for inv in rows:
        core = product_coder.core_of(inv.product_code) or inv.product_code
        stats = compute_product_stats(
            db,
            inv,
            abc_map=abc_map,
            cfg=cfg,
            in_production_split=in_prod_split,
            restock_plan_row=restock_map.get(core, {}),
            restock_qty=restock_allocation.get(id(inv), 0),
        )
        if inv.lead_time_days is None and stats["lead_time_days_computed"] is not None:
            inv.lead_time_days = stats["lead_time_days_computed"]
        updated += 1
    db.flush()
    return updated
