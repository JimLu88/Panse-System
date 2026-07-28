"""剩余流水（可用资金）测算 + 投资回收 — 业务需求自定义公式。

可用资金 = Σ加项 − Σ减项  (总投资费用不在内 — 它是沉没本金, 单列做投资回收对比)

加项 (现在还有多少能动用的钱):
  (a) 平台保证金            — 手动常量 (settings: cashflow_shop_deposit)
  (b) 支付宝账户余额(全部)  — AccountBalance 最新期末, 账户名含「支付宝」全部账户
  (c) 淘宝聚合结算账户余额  — 账户名含「聚合」
  (d) 推广账户余额          — 账户名含「推广」
  (e) 其他账户余额(银行卡等)— 其余账户 (银行卡/个体户私账等也是真金, 计入)
  (f) 订单待确认收货金额    — Σ paid_amount, status=shipped (已发货未签收)
  (g) 订单未发货金额        — Σ paid_amount, status=paid   (已付款未发货)

减项 (即将要付出去的钱):
  (a) 待扣平台服务费        — (待确认收货 + 未发货) × 千分之六 (卖家服务费列常空, 故估算)
  (b) 工厂打样费(未付)      — Σ factory_bill_amount, 未付 + 无平台单号 (待账单)
  (c) 工厂结算(已开账单未付)— Σ factory_bill_amount, 未付 + 有平台单号
  (d) 工厂结算(未开账单预估)— 活跃单(待发货+待确认)的预测工厂成本; 定制单按基础成本
  (e) 代付补单佣金(未结)    — 补单记录 commission, 无支付宝流水号(未付)

投资回收 (单列, 不进可用资金):
  总投资费用 (手动) ↔ 累计总利润 (compute_total_profit) → 回收率 / 是否回本。
  「总投资」的对应面是「总利润」而非「可用资金」, 二者不能混在一个减法里。

设计要点:
  - 不做「手动结算」，每次实时计算；订单/流水/余额一更新即反映。
  - 同时返回各数据源的「截止时间 + 新鲜度红绿灯」(账户余额日期问题见 AccountBalance.as_of_date)。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, AlipayFlow, RefillRecord
from app.models.order import FactoryOrder, Order
from app.models.shop_deposit import ShopDeposit
from app.services import factory_payment_service, settings_service

# ── 手动常量配置键 ────────────────────────────────────────────
SETTING_SHOP_DEPOSIT = "cashflow_shop_deposit"
SETTING_TOTAL_INVESTMENT = "cashflow_total_investment"
# 税费按季度缴(每季一次); 已缴季度存 JSON 列表如 ["2026-Q1"] → 不再计入减项。当季恒计(未缴)。
SETTING_TAX_PAID_QUARTERS = "cashflow_tax_paid_quarters"
DEFAULT_TOTAL_INVESTMENT = Decimal("669871")
DEFAULT_SHOP_DEPOSIT = Decimal("0")

# 平台服务费率 ~千分之六 (软件服务费); 淘宝补贴税 2%
_PLATFORM_FEE_RATE = Decimal("0.006")
_TAX_RATE = Decimal("0.02")

# 新鲜度阈值（天）
FRESH_DAYS = 7
AGING_DAYS = 31

_Q = Decimal("0.01")


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def _get_setting_decimal(db: Session, key: str, default: Decimal) -> Decimal:
    raw = settings_service.get(db, key, env_fallback=False)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return Decimal(str(raw))
    except Exception:
        return default


# ── 账户余额：取每个账户最新一期的期末余额，按账户名归类 ──────────
def _latest_balances(db: Session) -> list[AccountBalance]:
    rows = db.execute(select(AccountBalance)).scalars().all()
    latest: dict[str, AccountBalance] = {}
    for r in rows:
        cur = latest.get(r.account_name)
        if cur is None or (r.period_year, r.period_month) > (cur.period_year, cur.period_month):
            latest[r.account_name] = r
    return list(latest.values())


def _classify_account(name: str) -> str:
    n = name or ""
    if "支付宝" in n:
        return "alipay"
    if "聚合" in n:
        return "aggregate"
    if "推广" in n:
        return "promotion"
    return "other"


# ── 新鲜度 ────────────────────────────────────────────────────
def _freshness(
    label: str,
    as_of: Optional[datetime],
    *,
    fresh_days: int = FRESH_DAYS,
    aging_days: int = AGING_DAYS,
) -> dict:
    """返回 {source, as_of, days_ago, status}. status: fresh / aging / stale / unknown."""
    if as_of is None:
        return {"source": label, "as_of": None, "days_ago": None, "status": "unknown"}
    # 财务新鲜度看“业务日”而不是精确小时。数据库里的旧流水多为本地朴素时间，
    # 直接强贴 UTC 会把上海时间平移 8 小时，导致两天前的数据被误判成只过 1 天。
    local_date = (
        as_of.astimezone().date() if as_of.tzinfo is not None else as_of.date()
    )
    days = (datetime.now().astimezone().date() - local_date).days
    if days <= fresh_days:
        status = "fresh"
    elif days <= aging_days:
        status = "aging"
    else:
        status = "stale"
    return {"source": label, "as_of": as_of.isoformat(), "days_ago": days, "status": status}


def _as_datetime(d) -> Optional[datetime]:
    """date / datetime → tz-aware datetime (供新鲜度按业务日期比较); None 透传。"""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _sum_paid(db: Session, status: str) -> Decimal:
    """某状态(非历史)订单的买家实付合计 — 现金流"待确认收货/未发货"在途金额。"""
    return _d(db.execute(
        select(func.coalesce(func.sum(Order.paid_amount), 0)).where(
            Order.status == status, Order.is_historical == False,  # noqa: E712
            Order.is_refill == False,  # 刷单是假单, 不算在途真实资金 (2026-06-19)
        )
    ).scalar())


def _classify_unpaid_factory_bills(db: Session) -> tuple[Decimal, Decimal, set, set]:
    """未付·未作废·已开账单(factory_bill_amount 非空)的工厂单, 按【是否挂真实订单】分流:

    - 已开结算账单(billed): 有平台单号 **或** 有 source_order_id(挂了真实订单, 只是没解析出平台号);
    - 真打样费(sample): 既无平台单号也无 source_order_id(纯打样, 不对应任何客户订单)。

    返回 (sample_total, billed_total, billed_order_nos, billed_order_ids)。
    billed_order_nos/ids 供"未开账单预测成本"去重 —— 同一笔工厂义务不再被二次预估,
    根治"已开账单但平台单号为空"的单同时进打样费+预测成本的双扣 (C9, 用户 2026-07-01)。
    """
    rows = db.execute(
        select(FactoryOrder).where(
            FactoryOrder.payment_status == "unpaid",
            FactoryOrder.voided_at.is_(None),
            FactoryOrder.factory_bill_amount.isnot(None),
        )
    ).scalars().all()
    sample = billed = Decimal("0")
    billed_nos: set[str] = set()
    billed_ids: set[int] = set()
    for fo in rows:
        amt = _d(fo.factory_bill_amount)
        if fo.platform_order_no or fo.source_order_id:   # 挂了真实订单 → 是结算账单, 非打样
            billed += amt
            if fo.platform_order_no:
                billed_nos.add(fo.platform_order_no)
            if fo.source_order_id:
                billed_ids.add(fo.source_order_id)
        else:
            sample += amt
    return sample, billed, billed_nos, billed_ids


def _parts_costs_row(db: Session, o: Order):
    """订单 → 定价表配件明细行(pricing_sku_costs): SKU 直取; 无行(定制/咨询码) →
    同产品按木作最接近(锚=wood_cost_est)的规格行(与配件阶梯同法)。返回 (row, sku_code)。"""
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuCosts
    from app.services import sku_utils
    sc = sku_utils.strip_custom_suffix(o.sku_code) if o.sku_code else None
    if sc:
        r = db.execute(select(PricingSkuCosts).where(PricingSkuCosts.sku_code == sc)).scalar_one_or_none()
        if r is not None:
            return r, sc
    if o.product_code:
        rows = db.execute(
            select(PricingSku.sku_code, PricingSku.wood_cost)
            .where(PricingSku.product_code == o.product_code, PricingSku.wood_cost.isnot(None))
            .order_by(PricingSku.id)
        ).all()
        if rows:
            anchor = _d(getattr(o, "wood_cost_est", None))
            best = min(rows, key=lambda x: abs(Decimal(str(x[1])) - anchor)) if anchor > 0 else rows[0]
            r = db.execute(select(PricingSkuCosts).where(PricingSkuCosts.sku_code == best[0])).scalar_one_or_none()
            if r is not None:
                return r, best[0]
    return None, None


# 五金 = 金属件族(用户 2026-07-14 汇总口径): 抽屉轨+铁销+连接件+铝轨+塑轨+小拉手
_HW_COLS = ("drawer_rail", "iron_pin", "connector", "aluminum_rail", "plastic_rail", "mini_handle")


def _factory_estimate_unbilled(db: Session, billed_nos: set, billed_ids: set) -> tuple[Decimal, int]:
    """未开账单工厂结算预估: 活跃单(待发货+待确认收货)预测物理成本合计。

    成本口径与"月度经营/逐单核对"完全一致 —— 用 order_financials.physical_cost(工厂实报优先,
    否则定价表/BOM 推算, 含定制兜底 实付×85% / 推演封顶 实付×85%)。故"算不出工厂成本"的单
    (定制/无定价的远期压货单)不再被静默丢弃、营收却已全额进加项 → 消除单边高估 (C3, 用户 2026-07-01)。

    已开账单的单跳过(平台单号 或 source_order_id 命中 billed_nos/billed_ids), 防与"已开账单未付"双算。
    返回 (合计, 计入单数)。"""
    from app.services import order_financials as ofin
    orders = db.execute(
        select(Order).where(
            Order.status.in_(("paid", "shipped")),
            Order.is_refill == False,  # noqa: E712
            Order.is_historical == False,  # noqa: E712
        )
    ).scalars().all()
    total = Decimal("0")
    counted = 0
    for o in orders:
        if o.order_no in billed_nos or o.id in billed_ids:
            continue  # 已开账单 → 不重复预估
        c = ofin.physical_cost(o)
        if c and c > 0:
            total += c
            counted += 1
    return total.quantize(_Q), counted


def _factory_estimate_split(db: Session, billed_nos: set, billed_ids: set) -> tuple[dict, int]:
    """未开账单预估按类拆分 (用户 2026-07-14): 木作/岩板/五金/玻璃/电力轨道/其他配件/打包/物流安装
    + 封顶兜底调整(=最终口径 − 毛分类合计, 片段实付×85%压缩为负、定制兜底抬高为正)。
    分类值订单缺实际就读定价表配件明细(pricing_sku_costs), 件数按 est_parts/单件外配件 等比折算
    (防差价单大数量爆表)。九项之和恒等于原单行"未开账单·预测成本" → 剩余流水分毫不变。"""
    from app.models.pricing import PricingSku
    from app.services import order_financials as ofin
    orders = db.execute(
        select(Order).where(
            Order.status.in_(("paid", "shipped")),
            Order.is_refill == False,  # noqa: E712
            Order.is_historical == False,  # noqa: E712
        )
    ).scalars().all()
    agg = {k: Decimal("0") for k in
           ("wood", "rock_slab", "hardware", "glass", "electric_rail",
            "other_parts", "packing", "logi_inst")}
    total = Decimal("0")
    counted = 0
    for o in orders:
        if o.order_no in billed_nos or o.id in billed_ids:
            continue
        pb = ofin.physical_cost_breakdown(o)
        f = pb["final"]
        if not f or f <= 0:
            continue
        counted += 1
        total += f
        agg["wood"] += _d(o.actual_cost) if _d(o.actual_cost) > 0 else _d(getattr(o, "wood_cost_est", None))
        agg["packing"] += pb["packing"]
        comp = pb["logistics_component"] + pb["install_component"]
        agg["logi_inst"] += comp if comp > 0 else _d(o.est_logistics) + _d(o.est_install)
        ep = _d(getattr(o, "est_parts", None))
        row, sc = _parts_costs_row(db, o)
        if row is None or ep <= 0:
            agg["other_parts"] += ep
            continue
        unit = _d(db.execute(
            select(PricingSku.external_parts_cost).where(PricingSku.sku_code == sc)).scalar())
        factor = (ep / unit) if unit > 0 else Decimal("1")
        g = lambda a: _d(getattr(row, a, None))  # noqa: E731
        rock = g("rock_slab") * factor
        gl = g("glass") * factor
        el = g("electric_rail") * factor
        hw = sum((g(a) for a in _HW_COLS), Decimal("0")) * factor
        agg["rock_slab"] += rock
        agg["glass"] += gl
        agg["electric_rail"] += el
        agg["hardware"] += hw
        agg["other_parts"] += max(ep - (rock + gl + el + hw), Decimal("0"))
    split = {k: v.quantize(_Q) for k, v in agg.items()}
    # 调整行 = 总额 − 其余八行【取整后】之和 → 九行之和与原单行分毫不差(取整尾差全归此行)
    split["cap_adjust"] = (total.quantize(_Q) - sum(split.values())).quantize(_Q)
    split["_total"] = total.quantize(_Q)
    return split, counted


def _platform_activity_fee(db: Session) -> Decimal:
    """平台活动抽成(在途·按生效月): 活跃单(paid/shipped)中 order_date 落在财务设置活动窗口的,
    按 paid_amount × activity_rate 估。窗口 = fin_platform_activity_since..until
    (默认 2026-05-01..06-30) → 1-4月不计、5月起计(用户拍板 2026-07-01)。与利润口径活动抽成同源。"""
    from app.services import order_financials as ofin
    coef = ofin.load_coefficients(db)
    rate = _d(coef.get("activity_rate"))
    since = coef.get("activity_since")
    until = coef.get("activity_until")
    if rate <= 0 or since is None:
        return Decimal("0")
    conds = [
        Order.status.in_(("paid", "shipped")),
        Order.is_historical == False,  # noqa: E712
        Order.is_refill == False,  # noqa: E712
        Order.order_date.isnot(None),
        Order.order_date >= since,
    ]
    if until is not None:
        conds.append(Order.order_date <= until)
    base = _d(db.execute(
        select(func.coalesce(func.sum(Order.paid_amount), 0)).where(*conds)
    ).scalar())
    return (base * rate).quantize(_Q)


def _refill_unpaid_commission(db: Session) -> Decimal:
    """代付补单佣金(未结): 补单记录 commission 合计, 取尚无支付宝流水号(未付)的。"""
    return _d(db.execute(
        select(func.coalesce(func.sum(RefillRecord.commission), 0)).where(
            RefillRecord.alipay_flow_no.is_(None),
        )
    ).scalar())


def _quarter_of(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def _tax_paid_quarters(db: Session) -> set:
    """已缴税季度集合 (用户在减项手选「已缴纳」); 存 system_settings JSON 列表。"""
    import json
    raw = settings_service.get(db, SETTING_TAX_PAID_QUARTERS, env_fallback=False)
    if not raw:
        return set()
    try:
        v = json.loads(raw)
        return set(v) if isinstance(v, list) else set()
    except Exception:
        return set()


def _quarterly_tax(db: Session, *, today: Optional[date] = None) -> dict:
    """待缴税费(按季度): 销售税 = 已成交真实销售(排补单/取消/全退) 的 (实付−退款) × 税率, 按 order_date 季度累计。

    税费每季缴一次(口径从支付宝出账看): **当季(=今天所在自然季)恒计(未缴, 不可标已缴)**;
    **已过去的季度手选「已缴纳」→ 不计入减项**。季度一翻页(如 7/1 进 Q3), 上一季 Q2 立即变成可勾的过去季。
    今天所在季用日历日期判定(不是最新订单日期), 否则季度结束后旧季会一直卡在"当季"。
    返回 {quarters:[{quarter, tax, is_current, paid}], counted_total, current_quarter}。
    """
    from app.services import order_financials as ofin
    from app.services.sales_analytics import settled_sale_clause
    rate = _d(ofin.load_coefficients(db).get("tax_rate")) or _TAX_RATE
    rows = db.execute(
        select(Order.order_date, Order.paid_amount, Order.refund_amount, Order.status).where(
            Order.is_refill == False,  # noqa: E712
            Order.order_date.isnot(None),
            settled_sale_clause(),
        )
    ).all()
    # 三层口径 (用户拍板 2026-07-15): 当季估算只计【已落袋】(signed/aftersales≈已打款代理),
    # 在途单(paid/shipped)的 2% 由减项单列"待扣2%补贴税(在途)"计提 —— 两边不双算;
    # 打款后订单转 signed 自动从"在途"滚进"当季"。过去季度仍全量估(报送一到即覆盖)。
    _cur_q_pre = _quarter_of(today or date.today())
    by_q: dict[str, Decimal] = {}
    for od, paid, refund, st in rows:
        if od is None:
            continue
        q = _quarter_of(od)
        if q == _cur_q_pre and st in ("paid", "shipped"):
            continue   # 当季在途 → 在途税行计提, 此处不计
        by_q[q] = by_q.get(q, Decimal("0")) + (_d(paid) - _d(refund))
    # 报送口径叠加 (用户拍板 2026-07-14): 税务局按【打款/结算】口径, 唯一真源=千牛涉税报送页
    # (收入净额=收入总额−退款)。已报送季度 → 基数用报送净额(basis=报送); 未报送(当季在途)
    # → 沿用订单估算(basis=估算)。Web-Agent 任务 tax_information 每周自动抓取落库。
    from app.services import tax_report_service
    reported = tax_report_service.get_reported(db)
    for q, v in reported.items():
        try:
            by_q[q] = Decimal(str(v.get("net_income")))
        except Exception:  # noqa: BLE001 - 单季坏数据回退估算
            continue
    current_q = _quarter_of(today or date.today())   # 当季 = 今天所在自然季 (日历判定)
    paid_set = _tax_paid_quarters(db)
    quarters = []
    counted = Decimal("0")
    for q in sorted(by_q.keys()):
        tax = (by_q[q] * rate).quantize(_Q)
        is_current = (q == current_q)
        paid_flag = (not is_current) and (q in paid_set)   # 当季不可标已缴; 上季手选
        if is_current or not paid_flag:
            counted += tax
        _basis = "估算"
        if q in reported:
            _basis = "预计算" if (reported[q] or {}).get("provisional") else "报送"
        quarters.append({"quarter": q, "tax": tax, "is_current": is_current, "paid": paid_flag,
                         "basis": _basis})
    return {"quarters": quarters, "counted_total": counted.quantize(_Q), "current_quarter": current_q}


def compute_total_profit(db: Session) -> dict:
    """累计总利润 — 与总投资对比看回本。统一会计口径 (与 accounting_summary/逐单核对/资产公式B 一致):

    成交口径 = settled_sale_clause (已付款·非待付款/取消/关闭·非全额退款) + 排补单;
    单单净利 = (实付−退款) − 会计总成本(物理+物流+安装+平台扣点+税+额外售后)。
    旧版营收用店铺实收、成本不含平台/税、售后单列, 与月度报表对不上, 故 2026-06-18 统一。
    """
    from app.services import order_financials as ofin
    from app.services.sales_analytics import settled_sale_clause
    coef = ofin.load_coefficients(db)
    as_by_order = ofin.extra_aftersales_by_order(db)
    orders = db.execute(
        select(Order).where(
            Order.is_refill == False,  # noqa: E712
            settled_sale_clause(),
        )
    ).scalars().all()
    revenue = cost = Decimal("0")
    missing_cost = 0
    for o in orders:
        revenue += _d(o.paid_amount) - _d(o.refund_amount)
        if o.actual_cost is None and o.theoretical_cost is None:
            missing_cost += 1
        cost += ofin.accounting_cost(o, coef, aftersales=_d(as_by_order.get(o.order_no, 0)))
    net = (revenue - cost).quantize(_Q)
    return {
        "order_count": len(orders),
        "revenue": revenue.quantize(_Q),
        "cost": cost.quantize(_Q),
        "expense": Decimal("0.00"),   # 物流/安装/平台/税/售后已并入会计总成本, 不再单列
        "net_profit": net,
        "orders_missing_cost": missing_cost,
    }


def compute_summary(db: Session) -> dict:
    """实时测算剩余流水(可用资金) + 投资回收(总利润vs总投资) + 数据新鲜度。

    可用资金 = Σ加项 − Σ减项 (总投资费用不在内 — 它是沉没本金, 单列做投资回收对比)。
    """
    # ── 加项 ─────────────────────────────────────────────
    # 平台保证金: 优先用多店铺条目(ShopDeposit)求和; 无条目时回退旧单常量(向后兼容)
    deposit_total, deposit_count = _shop_deposit_entries(db)
    if deposit_count > 0:
        shop_deposit = deposit_total
        deposit_source = f"保证金条目({deposit_count} 店铺)"
    else:
        shop_deposit = _get_setting_decimal(db, SETTING_SHOP_DEPOSIT, DEFAULT_SHOP_DEPOSIT)
        deposit_source = "手动维护(单值)"

    bal_alipay = bal_aggregate = bal_promotion = bal_other = Decimal("0")
    balances = _latest_balances(db)
    latest_period: Optional[tuple[int, int]] = None
    bal_as_of: Optional[date] = None   # 余额统计日期 (新鲜度按此算, 不用入库时间)
    for b in balances:
        cat = _classify_account(b.account_name)
        amt = _d(b.closing_balance)
        if cat == "alipay":
            bal_alipay += amt
        elif cat == "aggregate":
            bal_aggregate += amt
        elif cat == "promotion":
            bal_promotion += amt
        else:
            bal_other += amt
        period = (b.period_year, b.period_month)
        if latest_period is None or period > latest_period:
            latest_period = period
        if b.as_of_date and (bal_as_of is None or b.as_of_date > bal_as_of):
            bal_as_of = b.as_of_date

    awaiting_receipt = _sum_paid(db, "shipped")   # 待确认收货 (已发货未签收)
    not_shipped = _sum_paid(db, "paid")           # 未发货 (已付款未发货)
    order_active = awaiting_receipt + not_shipped

    # ── 减项 ─────────────────────────────────────────────
    # 平台服务费: 卖家服务费列常为空, 直接按在途订单金额 ×千分之六 估
    platform_fee = (order_active * _PLATFORM_FEE_RATE).quantize(_Q)
    # 在途 2% 补贴税 (三层口径第③层, 用户拍板 2026-07-15): 在途钱全额在加项, 打款时必被扣 2% —
    # 不计提可用资金虚高在途×2%。当季税费估算已剔除在途单(见 _quarterly_tax), 两边不双算。
    tax_inflight = (order_active * _TAX_RATE).quantize(_Q)
    activity_fee = _platform_activity_fee(db)   # 平台活动抽成 2% (按下单月活动窗口, 5月起)
    # 已开账单按"是否挂真实订单"分流(打样 vs 结算), 并产出去重集供预测成本防双扣 (C9)
    factory_sample, factory_billed, _billed_nos, _billed_ids = _classify_unpaid_factory_bills(db)
    # 未开账单预估按类拆行 (用户 2026-07-14 "把配件拆解修改进减项落库"): 九项之和=原单行, 剩余流水不变
    fe_split, fe_counted = _factory_estimate_split(db, _billed_nos, _billed_ids)
    refill_commission = _refill_unpaid_commission(db)
    tax_q = _quarterly_tax(db)   # 待缴税费(季度): 当季必扣 + 上季未标已缴的

    additions = [
        {"key": "shop_deposit", "label": "平台保证金", "amount": shop_deposit, "manual": True, "source": deposit_source},
        {"key": "alipay_balance", "label": "支付宝账户余额(全部)", "amount": bal_alipay, "manual": False, "source": "账户余额汇总"},
        {"key": "aggregate_balance", "label": "淘宝聚合结算余额", "amount": bal_aggregate, "manual": False, "source": "账户余额汇总"},
        {"key": "promotion_balance", "label": "推广账户余额", "amount": bal_promotion, "manual": False, "source": "账户余额汇总"},
        {"key": "other_balance", "label": "其他账户余额(银行卡等)", "amount": bal_other, "manual": False, "source": "账户余额汇总"},
        {"key": "awaiting_receipt", "label": "订单待确认收货金额", "amount": awaiting_receipt, "manual": False, "source": "订单(已发货)"},
        {"key": "not_shipped", "label": "订单未发货金额", "amount": not_shipped, "manual": False, "source": "订单(已付款)"},
    ]
    subtractions = [
        {"key": "platform_fee", "label": "待扣平台服务费(在途×0.6%)", "amount": platform_fee, "manual": False, "source": "在途订单估算"},
        {"key": "tax_inflight", "label": "待扣2%补贴税(在途)", "amount": tax_inflight, "manual": False,
         "source": "在途订单(待确认+未发货)×2%; 打款后自动滚入当季税费"},
        {"key": "platform_activity", "label": "平台活动抽成(在途·生效月×2%)", "amount": activity_fee, "manual": False, "source": "在途订单(下单月在活动期)×2%"},
        {"key": "tax_quarterly", "label": "待缴税费(季度·当季必扣)", "amount": tax_q["counted_total"], "manual": True,
         "source": f"成交销售×2% 按季累计; 当季{tax_q['current_quarter'] or '—'}必扣, 上季手选已缴则不计", "detail": tax_q["quarters"]},
        {"key": "factory_sample", "label": "工厂打样费(未付·待账单)", "amount": factory_sample, "manual": False, "source": "工厂订单(未付·无平台单号无订单链接)"},
        {"key": "factory_billed", "label": "工厂结算(已开账单未付)", "amount": factory_billed, "manual": False, "source": "工厂订单(未付·有平台单号或挂真实订单)"},
        {"key": "factory_estimate_wood", "label": "未开账单·预估木作", "amount": fe_split["wood"], "manual": False,
         "source": f"活跃单{fe_counted}单·定价木作估(有账单用账单)"},
        {"key": "factory_estimate_rock", "label": "未开账单·预估岩板", "amount": fe_split["rock_slab"], "manual": False,
         "source": "定价表配件明细(rock_slab)"},
        {"key": "factory_estimate_hw", "label": "未开账单·预估五金", "amount": fe_split["hardware"], "manual": False,
         "source": "定价表配件明细(抽屉轨+铁销+连接件+铝/塑轨+小拉手)"},
        {"key": "factory_estimate_glass", "label": "未开账单·预估玻璃", "amount": fe_split["glass"], "manual": False,
         "source": "定价表配件明细(glass)"},
        {"key": "factory_estimate_elec", "label": "未开账单·预估电力轨道", "amount": fe_split["electric_rail"], "manual": False,
         "source": "定价表配件明细(electric_rail)"},
        {"key": "factory_estimate_other_parts", "label": "未开账单·预估其他配件", "amount": fe_split["other_parts"], "manual": False,
         "source": "灯带/背板/脚/软包/雕刻等 + 无明细行的单"},
        {"key": "factory_estimate_packing", "label": "未开账单·预估打包", "amount": fe_split["packing"], "manual": False,
         "source": "同月度经营口径"},
        {"key": "factory_estimate_logi", "label": "未开账单·预估物流安装", "amount": fe_split["logi_inst"], "manual": False,
         "source": "同月度经营口径"},
        {"key": "factory_estimate_cap", "label": "未开账单·封顶兜底调整", "amount": fe_split["cap_adjust"], "manual": False,
         "source": "片段实付×85%压缩(负=抵减)/定制兜底抬高(正); 九项合计=原预测总额"},
        {"key": "refill_commission", "label": "代付补单佣金(未结)", "amount": refill_commission, "manual": False, "source": "补单记录"},
    ]

    total_add = sum((a["amount"] for a in additions), Decimal("0"))
    total_sub = sum((s["amount"] for s in subtractions), Decimal("0"))
    total = (total_add - total_sub).quantize(_Q)

    # ── 投资回收 (总投资 vs 累计总利润; 不进可用资金) ──────────
    total_investment = _get_setting_decimal(db, SETTING_TOTAL_INVESTMENT, DEFAULT_TOTAL_INVESTMENT)
    profit = compute_total_profit(db)
    net_profit = Decimal(str(profit["net_profit"]))
    recovery_rate = float(net_profit / total_investment) if total_investment else None
    investment = {
        "total_investment": total_investment,
        "total_profit": net_profit,
        "recovered": (net_profit >= total_investment) if total_investment else None,
        "recovery_rate": round(recovery_rate, 4) if recovery_rate is not None else None,
        "remaining": (total_investment - net_profit).quantize(_Q),
        "profit_detail": profit,
    }

    # ── 新鲜度: 用业务日期(数据截止)而非入库时间 ──────────────
    # 订单 → 最后下单日; 账户余额 → 统计日期(as_of_date); 都不再用 updated_at(=导入那天=今天)。
    orders_latest = db.execute(select(func.max(Order.order_date))).scalar()
    factory_latest = db.execute(select(func.max(FactoryOrder.order_date))).scalar()
    enterprise_flow_latest = db.execute(
        select(func.max(AlipayFlow.transaction_time)).where(
            AlipayFlow.account.in_(("企业号", "支付宝-企业账号"))
        )
    ).scalar()
    main_flow_latest = db.execute(
        select(func.max(AlipayFlow.transaction_time)).where(
            AlipayFlow.account == "主力号"
        )
    ).scalar()
    deposit_row_at = _setting_updated_at(db, SETTING_SHOP_DEPOSIT)
    investment_at = _setting_updated_at(db, SETTING_TOTAL_INVESTMENT)

    bal_label = "账户余额"
    if bal_as_of:
        bal_label = f"账户余额（统计日 {bal_as_of.isoformat()}）"
    elif latest_period:
        bal_label = f"账户余额（{latest_period[0]}-{latest_period[1]:02d}·缺统计日期）"

    freshness = [
        _freshness("订单数据(最后下单日)", _as_datetime(orders_latest)),
        _freshness(bal_label, _as_datetime(bal_as_of)),
        _freshness("工厂订单(最后下单日)", _as_datetime(factory_latest)),
        _freshness(
            "支付宝流水·企业号(最后流水日)",
            _as_datetime(enterprise_flow_latest),
            fresh_days=1,
            aging_days=1,
        ),
        _freshness(
            "支付宝流水·主力号(最后流水日)",
            _as_datetime(main_flow_latest),
            fresh_days=1,
            aging_days=1,
        ),
        _freshness("总投资费用", investment_at),
    ]
    if deposit_row_at is not None:
        freshness.append(_freshness("店铺保证金", deposit_row_at))

    return {
        "total": total,
        "total_additions": total_add.quantize(_Q),
        "total_subtractions": total_sub.quantize(_Q),
        "additions": additions,
        "subtractions": subtractions,
        "investment": investment,
        "other_account_balance": Decimal("0"),  # 银行卡等已并入加项, 不再单列
        "freshness": freshness,
        "manual": {
            "shop_deposit": shop_deposit,
            "total_investment": total_investment,
            "factory_settlement_days": factory_payment_service.get_settlement_days(db),
            "tax_current_quarter": tax_q["current_quarter"],
            "tax_quarters": tax_q["quarters"],          # [{quarter, tax, is_current, paid}] 供前端手选已缴
            "tax_paid_quarters": sorted(_tax_paid_quarters(db)),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _shop_deposit_entries(db: Session) -> tuple[Decimal, int]:
    """多店铺保证金条目: 返回 (合计, 条目数)。无条目时 (0, 0) → 调用方回退旧单常量。"""
    total, count = db.execute(
        select(func.coalesce(func.sum(ShopDeposit.amount), 0), func.count(ShopDeposit.id))
    ).one()
    return (Decimal(total or 0), int(count or 0))


def _setting_updated_at(db: Session, key: str) -> Optional[datetime]:
    from app.models.settings import SystemSetting
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    return row.updated_at if row else None


def update_manual(
    db: Session,
    *,
    shop_deposit: Optional[Decimal] = None,
    total_investment: Optional[Decimal] = None,
    factory_settlement_days: Optional[int] = None,
    tax_paid_quarters: Optional[list] = None,
) -> None:
    """更新手动常量（店铺保证金 / 总投资费用 / 工厂结算周期 / 已缴税季度）。"""
    if shop_deposit is not None:
        settings_service.set_value(
            db, SETTING_SHOP_DEPOSIT, str(shop_deposit), description="剩余流水-店铺保证金",
        )
    if total_investment is not None:
        settings_service.set_value(
            db, SETTING_TOTAL_INVESTMENT, str(total_investment), description="剩余流水-总投资费用",
        )
    if factory_settlement_days is not None:
        factory_payment_service.set_settlement_days(db, factory_settlement_days)
    if tax_paid_quarters is not None:
        import json
        clean = sorted({str(q) for q in tax_paid_quarters if str(q).strip()})
        settings_service.set_value(
            db, SETTING_TAX_PAID_QUARTERS, json.dumps(clean),
            description="剩余流水-已缴税季度(手选, 不计入减项)",
        )
