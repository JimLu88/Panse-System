"""对账服务 — plan §8 的 6 条规则统一接口。

每条规则的 run_* 函数：
    - 返回 ReconciliationResult（一组 ReconciliationDiff）
    - 当差异超阈值时，往 data_exceptions 写一条 reconciliation_diff 异常
        （AI 抹平模块会读这条记录后给建议；见 plan §6.2）

阈值：
    - 货款对账（工厂）：±0.5% 或 ±5 元
    - 推广支出：±0.5% 或 ±5 元
    - 补单赔实付：单条订单 ±1 元
    - 库存资产：仅汇总数，差异由人工/AI 判断
    - 安装费/物流费：±0.5% 或 ±5 元；账单未导入时回退到售后表/订单运费
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.finance import AlipayFlow, LogisticsBill, RefillRecord, WanshifuBill
from app.models.inventory import PartInventory
from app.models.marketing import AfterSales, BrandMarketing, DailyOperation, OutsourcingExpense, PromotionFlow
from app.models.material import Material
from app.models.order import FactoryOrder, Order, PartPurchase
from app.services import exception_service

RuleName = Literal[
    "factory_payment",
    "install_fee",
    "promotion",
    "refill_compensation",
    "inventory_value",
    "logistics_fee",
    "revenue_alipay",
    "operating_expense",
    "purchase_payment",
]

DiffSeverity = Literal["ok", "warning", "error", "not_available"]


@dataclass
class ReconciliationDiff:
    """一条对账差异 / 一条对账结果。"""
    key: str                   # 业务键: 工厂名 / 订单号 / 物料编码 / ...
    expected: Optional[Decimal]  # 主表数额
    actual: Optional[Decimal]    # 校验表数额
    diff: Optional[Decimal]      # actual - expected
    severity: DiffSeverity
    message: str
    related_records: list[str] = field(default_factory=list)


@dataclass
class ReconciliationResult:
    rule: RuleName
    period_start: Optional[date]
    period_end: Optional[date]
    total_diffs: int
    ok_count: int
    warning_count: int
    error_count: int
    diffs: list[ReconciliationDiff]
    unresolved_count: int = 0   # open 异常池中本规则的未对清条数


def _within(diff: Decimal, *, pct: Decimal = Decimal("0.005"), abs_floor: Decimal = Decimal("5")) -> bool:
    """diff 是否在容差内（取百分比与绝对值的较大者）。"""
    return abs(diff) <= abs_floor


def _classify(diff: Decimal, *, pct: Decimal = Decimal("0.005"), abs_floor: Decimal = Decimal("5")) -> DiffSeverity:
    if abs(diff) <= abs_floor:
        return "ok"
    if abs(diff) <= abs_floor * 10:
        return "warning"
    return "error"


def _record_exception(db, *, rule: RuleName, key: str, diff_amount: Decimal, message: str):
    """幂等写: 同 rule:key 已有 open 异常则跳过, 避免每日 cron 重复堆积。"""
    from app.models.exception import DataException as _DE
    existing = db.query(_DE).filter_by(
        source_table="reconciliation",
        source_pk=f"{rule}:{key}",
        exception_type="reconciliation_diff",
        status="open",
    ).first()
    if existing:
        return
    exception_service.record(
        db,
        source_table="reconciliation",
        source_pk=f"{rule}:{key}",
        exception_type="reconciliation_diff",
        severity="warning" if abs(diff_amount) < Decimal("50") else "error",
        description=message,
        suggestion_action="ai_smoothing_or_manual_review",
        context={"rule": rule, "key": key, "diff": str(diff_amount)},
    )


# -------- Rule 1: 货款对账 (工厂应付 ↔ 支付宝流水) --------

def run_factory_payment(
    db: Session,
    *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """按工厂汇总 (factory_orders 应付) vs (alipay_flows 类型=factory_payment 的支出)."""
    fo_stmt = select(
        FactoryOrder.factory_name,
        func.coalesce(func.sum(FactoryOrder.factory_bill_amount), 0).label("billed"),
    ).group_by(FactoryOrder.factory_name)
    if period_start:
        fo_stmt = fo_stmt.where(FactoryOrder.order_date >= period_start)
    if period_end:
        fo_stmt = fo_stmt.where(FactoryOrder.order_date <= period_end)

    billed_by_factory: dict[str, Decimal] = {
        (name or "(未知工厂)"): Decimal(billed or 0) for name, billed in db.execute(fo_stmt).all()
    }

    # 流水里查 reconciliation_type = factory_payment 的支出 (amount < 0)
    flow_stmt = select(
        AlipayFlow.counterparty,
        func.coalesce(func.sum(-AlipayFlow.amount), 0).label("paid"),
    ).where(AlipayFlow.reconciliation_type == "factory_payment").group_by(AlipayFlow.counterparty)
    paid_by_factory: dict[str, Decimal] = {
        (name or "(未匹配)"): Decimal(paid or 0) for name, paid in db.execute(flow_stmt).all()
    }

    diffs: list[ReconciliationDiff] = []
    for factory in set(billed_by_factory) | set(paid_by_factory):
        billed = billed_by_factory.get(factory, Decimal("0"))
        paid = paid_by_factory.get(factory, Decimal("0"))
        diff = paid - billed
        sev = _classify(diff)
        msg = f"{factory}: 应付 ¥{billed}, 实付 ¥{paid}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=factory, expected=billed, actual=paid, diff=diff, severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="factory_payment", key=factory, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("factory_payment", period_start, period_end, diffs, db)


# -------- Rule 3: 推广支出 (Phase 5 实装) --------

def run_promotion(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """推广记录 (15) ↔ 支付宝(reconciliation_type=promotion)。

    按月汇总比较：promotion_flows 里的 ‘支出’ 与同期 alipay_flows.amount<0 的
    promotion 类型流水。差异超阈值入异常。period_start/period_end 限定账期。
    """
    from sqlalchemy import extract
    # 推广表的支出按月聚合
    pf_stmt = select(
        extract("year", PromotionFlow.transaction_date).label("y"),
        extract("month", PromotionFlow.transaction_date).label("m"),
        func.coalesce(func.sum(PromotionFlow.amount), 0).label("spent"),
    ).where(PromotionFlow.flow_type == "支出")
    if period_start:
        pf_stmt = pf_stmt.where(PromotionFlow.transaction_date >= period_start)
    if period_end:
        pf_stmt = pf_stmt.where(PromotionFlow.transaction_date <= period_end)
    pf_stmt = pf_stmt.group_by("y", "m")
    by_month_pf: dict[tuple[int, int], Decimal] = {}
    for y, m, spent in db.execute(pf_stmt).all():
        if y is None or m is None:
            continue
        by_month_pf[(int(y), int(m))] = Decimal(spent or 0)

    # 支付宝里 reconciliation_type='promotion' 的支出按月
    af_stmt = select(
        extract("year", AlipayFlow.transaction_time).label("y"),
        extract("month", AlipayFlow.transaction_time).label("m"),
        func.coalesce(func.sum(-AlipayFlow.amount), 0).label("paid"),
    ).where(AlipayFlow.reconciliation_type == "promotion")
    if period_start:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time <= period_end)
    af_stmt = af_stmt.group_by("y", "m")
    by_month_af: dict[tuple[int, int], Decimal] = {}
    for y, m, paid in db.execute(af_stmt).all():
        if y is None or m is None:
            continue
        by_month_af[(int(y), int(m))] = Decimal(paid or 0)

    diffs: list[ReconciliationDiff] = []
    for key in sorted(set(by_month_pf) | set(by_month_af)):
        y, m = key
        expected = by_month_pf.get(key, Decimal("0"))
        actual = by_month_af.get(key, Decimal("0"))
        diff = actual - expected
        sev = _classify(diff)
        period_key = f"{y}-{m:02d}"
        msg = f"{period_key}: 推广支出 ¥{expected}, 支付宝 ¥{actual}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=period_key, expected=expected, actual=actual, diff=diff,
            severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="promotion", key=period_key, diff_amount=diff, message=msg)

    if not diffs:
        diffs = [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None,
            severity="not_available",
            message="无推广记录数据可对账 (空表)",
        )]
    if record_exceptions:
        db.flush()
    return _result("promotion", period_start, period_end, diffs, db)


# -------- Rule 4: 补单赔实付 --------

def run_refill_compensation(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """补单成本 ↔ 关联订单实付金额差额。

    对每条 refill_record，比较 total_cost 与同 order_no 主订单 paid_amount
    （主订单可能不存在 → severity=warning）。period_start/period_end 按补单日筛。
    """
    # 一次性建订单实付映射, 避免逐行查 (N+1)
    paid_by_order: dict[str, Optional[Decimal]] = {
        order_no: paid
        for order_no, paid in db.execute(
            select(Order.order_no, Order.paid_amount).where(Order.order_no.isnot(None))
        ).all()
    }

    stmt = select(RefillRecord)
    if period_start:
        stmt = stmt.where(RefillRecord.refill_date >= period_start)
    if period_end:
        stmt = stmt.where(RefillRecord.refill_date <= period_end)
    rows = db.execute(stmt).scalars().all()

    diffs: list[ReconciliationDiff] = []
    for r in rows:
        if r.order_no not in paid_by_order:
            diffs.append(ReconciliationDiff(
                key=r.order_no,
                expected=r.total_cost,
                actual=None,
                diff=None,
                severity="warning",
                message=f"补单 {r.order_no}: 找不到对应主订单",
            ))
            continue
        expected = r.total_cost or Decimal("0")
        actual = paid_by_order[r.order_no] or Decimal("0")
        diff = actual - expected
        sev = _classify(diff, abs_floor=Decimal("1"))
        msg = f"补单 {r.order_no}: 总成本 ¥{expected}, 主单实付 ¥{actual}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=r.order_no, expected=expected, actual=actual, diff=diff, severity=sev, message=msg,
        ))
        if sev not in ("ok", "not_available") and record_exceptions:
            _record_exception(db, rule="refill_compensation", key=r.order_no, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("refill_compensation", period_start, period_end, diffs, db)


# -------- Rule 5: 库存资产估值 --------

def run_inventory_value(db: Session, *, record_exceptions: bool = True, **_) -> ReconciliationResult:
    """库存资产 = Σ (配件可用库存 × 物料单价)。账面快照, 不受账期影响 (忽略 period)。
    成品库存先不计入（需 SKU 级总成本，留 Phase 4 末做）。
    """
    stmt = (
        select(
            PartInventory.material_code,
            func.coalesce(func.sum(PartInventory.physical_qty - func.coalesce(PartInventory.locked_qty, 0)), 0).label("avail"),
            Material.price,
            Material.name,
        )
        .join(Material, PartInventory.material_code == Material.code, isouter=True)
        .group_by(PartInventory.material_code, Material.price, Material.name)
    )
    total_value = Decimal("0")
    diffs: list[ReconciliationDiff] = []
    missing_price = 0
    for code, avail, price, name in db.execute(stmt).all():
        avail_d = Decimal(int(avail or 0))
        if price is None:
            if avail_d > 0:
                missing_price += 1
            continue
        v = (price * avail_d).quantize(Decimal("0.01"))
        total_value += v
        diffs.append(ReconciliationDiff(
            key=code, expected=v, actual=v, diff=Decimal("0"), severity="ok",
            message=f"{code} {name}: {avail_d} × ¥{price} = ¥{v}",
        ))

    # 汇总条目
    diffs.append(ReconciliationDiff(
        key="TOTAL",
        expected=total_value, actual=total_value, diff=Decimal("0"),
        severity="ok" if missing_price == 0 else "warning",
        message=f"账面价值合计 ¥{total_value} ({len(diffs)} 项物料；{missing_price} 项缺价格未计入)",
    ))
    return _result("inventory_value", None, None, diffs, db)


# -------- Rule 2: 安装费对账 (万师傅账单 ↔ 支付宝 install 支出) --------

def _month_key(d: Optional[date]) -> Optional[str]:
    return f"{d.year}-{d.month:02d}" if d else None


def _sum_by_month(rows) -> dict[str, Decimal]:
    """rows: 可迭代的 (date, amount); 按 YYYY-MM 聚合, date 为空归入 '(无日期)'。"""
    out: dict[str, Decimal] = {}
    for d, amt in rows:
        key = _month_key(d) or "(无日期)"
        out[key] = out.get(key, Decimal("0")) + Decimal(amt or 0)
    return out


def run_install_fee(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """安装费对账: 万师傅账单 (按月) ↔ 支付宝 install 支出 (按月)。

    优先用 wanshifu_bills (导入的万师傅后台账单); 若该表为空, 回退到
    售后表 after_sales.wanshifu_deduction (万师傅扣款) 作为应付口径。
    """
    has_bills = db.execute(select(func.count(WanshifuBill.id))).scalar_one() > 0
    if has_bills:
        wb_stmt = select(WanshifuBill.bill_date, WanshifuBill.amount)
        if period_start:
            wb_stmt = wb_stmt.where(WanshifuBill.bill_date >= period_start)
        if period_end:
            wb_stmt = wb_stmt.where(WanshifuBill.bill_date <= period_end)
        billed = _sum_by_month(db.execute(wb_stmt).all())
        source = "万师傅账单"
    else:
        # 回退: 售后表万师傅扣款 (用 processed_at 作账期)
        as_stmt = select(AfterSales.processed_at, AfterSales.wanshifu_deduction).where(
            AfterSales.wanshifu_deduction.isnot(None),
        )
        if period_start:
            as_stmt = as_stmt.where(AfterSales.processed_at >= period_start)
        if period_end:
            as_stmt = as_stmt.where(AfterSales.processed_at <= period_end)
        billed = _sum_by_month(db.execute(as_stmt).all())
        source = "售后表万师傅扣款 (账单未导入, 回退口径)"

    # 支付宝 install 支出 (amount<0 → 取负)
    af_stmt = select(AlipayFlow.transaction_time, -AlipayFlow.amount).where(
        AlipayFlow.reconciliation_type == "install",
    )
    if period_start:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time <= period_end)
    paid = _sum_by_month(
        (t.date() if hasattr(t, "date") else t, a) for t, a in db.execute(af_stmt).all()
    )

    if not billed and not paid:
        return _result("install_fee", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无万师傅账单 / 售后扣款 / install 流水可对账 (空数据)",
        )], db)

    diffs: list[ReconciliationDiff] = []
    for key in sorted(set(billed) | set(paid)):
        exp = billed.get(key, Decimal("0"))
        act = paid.get(key, Decimal("0"))
        diff = act - exp
        sev = _classify(diff)
        msg = f"{key}: 应付安装费 ¥{exp} ({source}), 支付宝实付 ¥{act}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=key, expected=exp, actual=act, diff=diff, severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="install_fee", key=key, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("install_fee", period_start, period_end, diffs, db)


# -------- Rule 6: 物流费核销 (物流账单 ↔ 支付宝 logistics 支出) --------

def run_logistics_fee(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """物流费对账: 物流公司账单 (按月) ↔ 支付宝 logistics 支出 (按月)。

    优先用 logistics_bills (导入的物流月结账单); 若该表为空, 回退到
    订单表 orders.actual_freight (按 order_date) 作为应付口径。
    """
    has_bills = db.execute(select(func.count(LogisticsBill.id))).scalar_one() > 0
    if has_bills:
        lb_stmt = select(LogisticsBill.bill_date, LogisticsBill.freight_amount)
        if period_start:
            lb_stmt = lb_stmt.where(LogisticsBill.bill_date >= period_start)
        if period_end:
            lb_stmt = lb_stmt.where(LogisticsBill.bill_date <= period_end)
        billed = _sum_by_month(db.execute(lb_stmt).all())
        source = "物流公司账单"
    else:
        o_stmt = select(Order.order_date, Order.actual_freight).where(
            Order.actual_freight.isnot(None),
        )
        if period_start:
            o_stmt = o_stmt.where(Order.order_date >= period_start)
        if period_end:
            o_stmt = o_stmt.where(Order.order_date <= period_end)
        billed = _sum_by_month(db.execute(o_stmt).all())
        source = "订单实际运费 (账单未导入, 回退口径)"

    af_stmt = select(AlipayFlow.transaction_time, -AlipayFlow.amount).where(
        AlipayFlow.reconciliation_type == "logistics",
    )
    if period_start:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time <= period_end)
    paid = _sum_by_month(
        (t.date() if hasattr(t, "date") else t, a) for t, a in db.execute(af_stmt).all()
    )

    if not billed and not paid:
        return _result("logistics_fee", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无物流账单 / 订单运费 / logistics 流水可对账 (空数据)",
        )], db)

    diffs: list[ReconciliationDiff] = []
    for key in sorted(set(billed) | set(paid)):
        exp = billed.get(key, Decimal("0"))
        act = paid.get(key, Decimal("0"))
        diff = act - exp
        sev = _classify(diff)
        msg = f"{key}: 应付物流费 ¥{exp} ({source}), 支付宝实付 ¥{act}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=key, expected=exp, actual=act, diff=diff, severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="logistics_fee", key=key, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("logistics_fee", period_start, period_end, diffs, db)


# -------- Rule 7: 收入对账 (订单营收 ↔ 支付宝收入) --------

def run_revenue_alipay(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """订单营收 (按月) ↔ 支付宝订单收入 (amount>0 且关联订单, 按月)。

    前置检查: 若支付宝流水中 related_order_no 回填率 < 50%, 返回 not_available
    (说明 alipay_backfill 尚未运行, 比对无意义, 不写异常)。
    容差 ±50 元, 因到账时间与下单月可能错位。
    """
    # 前置: 检查 related_order_no 回填率
    total_flows = db.execute(select(func.count(AlipayFlow.id))).scalar_one() or 0
    if total_flows > 0:
        linked_flows = db.execute(
            select(func.count(AlipayFlow.id)).where(AlipayFlow.related_order_no.isnot(None))
        ).scalar_one() or 0
        link_rate = linked_flows / total_flows
        if link_rate < 0.5:
            return _result("revenue_alipay", period_start, period_end, [ReconciliationDiff(
                key="all", expected=None, actual=None, diff=None, severity="not_available",
                message=f"支付宝流水 related_order_no 回填率仅 {link_rate:.0%} (<50%), "
                        "请先在支付宝流水页面运行「重新核销」后再对账。",
            )], db)

    o_stmt = select(Order.order_date, Order.paid_amount).where(
        Order.is_historical == False,  # noqa: E712
        Order.status.notin_(["cancelled", "pending_payment"]),
        Order.order_date.isnot(None),
    )
    if period_start:
        o_stmt = o_stmt.where(Order.order_date >= period_start)
    if period_end:
        o_stmt = o_stmt.where(Order.order_date <= period_end)
    revenue = _sum_by_month(db.execute(o_stmt).all())

    af_stmt = select(AlipayFlow.transaction_time, AlipayFlow.amount).where(
        AlipayFlow.amount > 0,
        AlipayFlow.related_order_no.isnot(None),
    )
    if period_start:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time <= period_end)
    income = _sum_by_month(
        (t.date() if hasattr(t, "date") else t, a) for t, a in db.execute(af_stmt).all()
    )

    if not revenue and not income:
        return _result("revenue_alipay", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无订单营收 / 支付宝订单收入可对账 (空数据)",
        )], db)

    diffs: list[ReconciliationDiff] = []
    for key in sorted(set(revenue) | set(income)):
        exp = revenue.get(key, Decimal("0"))
        act = income.get(key, Decimal("0"))
        diff = act - exp
        sev = _classify(diff, abs_floor=Decimal("50"))
        msg = f"{key}: 订单营收 ¥{exp}, 支付宝收入 ¥{act}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=key, expected=exp, actual=act, diff=diff, severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="revenue_alipay", key=key, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("revenue_alipay", period_start, period_end, diffs, db)


# -------- Rule 8: 经营支出对账 (日常经营/人员外包/品牌营销 ↔ 支付宝) --------

def _alipay_flow_amount_map(db: Session) -> dict[str, Decimal]:
    """transaction_no → 支出绝对金额 (amount<0 取负值)。用于按流水号反查实付。"""
    out: dict[str, Decimal] = {}
    for no, amt in db.execute(
        select(AlipayFlow.transaction_no, AlipayFlow.amount).where(
            AlipayFlow.transaction_no.isnot(None), AlipayFlow.transaction_no != "",
        )
    ).all():
        if not no:
            continue
        out[no] = abs(Decimal(amt or 0))
    return out


def run_operating_expense(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """日常经营 + 人员外包 + 品牌营销 三表支出 ↔ 支付宝 (按 alipay_flow_no 匹配)。

    逻辑:
    - 有 alipay_flow_no 且流水存在 → 比对金额是否一致 (汇总到按月差异);
    - 有 alipay_flow_no 但流水找不到 → 写 warning (可能录错号或流水未导入);
    - 无 alipay_flow_no → 跳过 (字段可选, 不强制填写, 不写异常).
    """
    flow_map = _alipay_flow_amount_map(db)

    do_stmt = select(DailyOperation)
    op_stmt = select(OutsourcingExpense)
    bm_stmt = select(BrandMarketing)
    if period_start:
        do_stmt = do_stmt.where(DailyOperation.record_date >= period_start)
        op_stmt = op_stmt.where(OutsourcingExpense.payment_date >= period_start)
        bm_stmt = bm_stmt.where(BrandMarketing.payment_date >= period_start)
    if period_end:
        do_stmt = do_stmt.where(DailyOperation.record_date <= period_end)
        op_stmt = op_stmt.where(OutsourcingExpense.payment_date <= period_end)
        bm_stmt = bm_stmt.where(BrandMarketing.payment_date <= period_end)

    records: list[tuple[str, Optional[date], Decimal, Optional[str], str]] = []
    for r in db.execute(do_stmt).scalars().all():
        if r.alipay_flow_no:  # 只处理有流水号的记录
            records.append(("日常经营", r.record_date, Decimal(r.amount or 0), r.alipay_flow_no, f"日常#{r.id}"))
    for r in db.execute(op_stmt).scalars().all():
        if r.alipay_flow_no:
            records.append(("人员外包", r.payment_date, Decimal(r.amount or 0), r.alipay_flow_no, f"外包#{r.id}({r.payee})"))
    for r in db.execute(bm_stmt).scalars().all():
        if r.alipay_flow_no:
            records.append(("品牌营销", r.payment_date, Decimal(r.actual_spend or 0), r.alipay_flow_no, f"品牌#{r.id}"))

    if not records:
        return _result("operating_expense", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无已关联支付宝流水号的经营记录可对账 (填写 alipay_flow_no 后自动启用)",
        )], db)

    # 只对"有流水号但流水找不到"的记录报 warning；有流水且匹配则按月汇总差异
    expected: dict[str, Decimal] = {}
    actual: dict[str, Decimal] = {}
    diffs: list[ReconciliationDiff] = []
    for source, d, amt, flow_no, key in records:
        if amt <= 0:
            continue
        month = _month_key(d) or "(无日期)"
        expected[month] = expected.get(month, Decimal("0")) + amt
        if flow_no in flow_map:
            actual[month] = actual.get(month, Decimal("0")) + flow_map[flow_no]
        else:
            # 有流水号但在支付宝表里找不到: 可能号录错 或 流水未导入
            msg = f"[{source}] {key}: 流水号 {flow_no} 无对应支付宝记录, 请确认号码或补导入流水"
            diffs.append(ReconciliationDiff(
                key=key, expected=amt, actual=None, diff=None, severity="warning", message=msg,
            ))
            if record_exceptions:
                _record_exception(db, rule="operating_expense", key=key, diff_amount=amt, message=msg)

    for month in sorted(set(expected) | set(actual)):
        exp = expected.get(month, Decimal("0"))
        act = actual.get(month, Decimal("0"))
        diff = act - exp
        sev = _classify(diff)
        msg = f"{month}: 经营已关联支出 ¥{exp}, 支付宝实付 ¥{act}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=month, expected=exp, actual=act, diff=diff, severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="operating_expense", key=month, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("operating_expense", period_start, period_end, diffs, db)


# -------- Rule 9: 采购付款对账 (配件采购单 ↔ 支付宝) --------

def run_purchase_payment(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """配件采购单 ↔ 支付宝 (按 alipay_flow_no 匹配)。

    应付 = 采购单 total_amount (缺则 amount); 按 payment_date(缺则 purchase_date) 月聚合。
    实付 = 按 alipay_flow_no 匹配到的支付宝支出。
    已标记付款 (payment_status 含 '付') 但无流水号 → orphan(差异)，写异常。
    """
    flow_map = _alipay_flow_amount_map(db)

    stmt = select(PartPurchase)
    rows = db.execute(stmt).scalars().all()
    if not rows:
        return _result("purchase_payment", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无配件采购单可对账 (空数据)",
        )], db)

    expected: dict[str, Decimal] = {}
    actual: dict[str, Decimal] = {}
    diffs: list[ReconciliationDiff] = []
    for p in rows:
        d = p.payment_date or p.purchase_date
        if period_start and (d is None or d < period_start):
            continue
        if period_end and (d is None or d > period_end):
            continue
        amt = Decimal(p.total_amount if p.total_amount is not None else (p.amount or 0))
        if amt <= 0:
            continue
        month = _month_key(d) or "(无日期)"
        if p.alipay_flow_no and p.alipay_flow_no in flow_map:
            # 有流水且能匹配 → 计入月度汇总对比
            expected[month] = expected.get(month, Decimal("0")) + amt
            actual[month] = actual.get(month, Decimal("0")) + flow_map[p.alipay_flow_no]
        elif "付" in (p.payment_status or "") and not p.alipay_flow_no:
            # 已标记付款但未填流水号 → 独立 warning, 不进月度汇总 (避免双重计数)
            msg = f"采购单 {p.purchase_no}: 已标记付款 ¥{amt} 但未填支付宝流水号"
            diffs.append(ReconciliationDiff(
                key=p.purchase_no, expected=amt, actual=None, diff=None, severity="warning", message=msg,
            ))
            if record_exceptions:
                _record_exception(db, rule="purchase_payment", key=p.purchase_no, diff_amount=amt, message=msg)
        elif p.alipay_flow_no and p.alipay_flow_no not in flow_map:
            # 填了流水号但流水找不到 → 独立 warning, 不进月度汇总
            msg = f"采购单 {p.purchase_no}: 流水号 {p.alipay_flow_no} 无对应支付宝记录"
            diffs.append(ReconciliationDiff(
                key=p.purchase_no, expected=amt, actual=None, diff=None, severity="warning", message=msg,
            ))
            if record_exceptions:
                _record_exception(db, rule="purchase_payment", key=p.purchase_no, diff_amount=amt, message=msg)
        # 未付款且无流水号 → 跳过, 正常状态

    for month in sorted(set(expected) | set(actual)):
        exp = expected.get(month, Decimal("0"))
        act = actual.get(month, Decimal("0"))
        diff = act - exp
        sev = _classify(diff)
        msg = f"{month}: 采购应付 ¥{exp}, 支付宝匹配 ¥{act}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=month, expected=exp, actual=act, diff=diff, severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="purchase_payment", key=month, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("purchase_payment", period_start, period_end, diffs, db)


def _count_open_exceptions(db: Session, rule: str) -> int:
    """统计异常池中本对账规则尚未解决的条数."""
    from sqlalchemy import func as _func
    from app.models.exception import DataException
    row = db.execute(
        select(_func.count(DataException.id)).where(
            DataException.source_table == "reconciliation",
            DataException.source_pk.like(f"{rule}:%"),
            DataException.status == "open",
        )
    ).scalar_one()
    return int(row or 0)


def _result(rule, ps, pe, diffs, db: Optional[Session] = None) -> ReconciliationResult:
    unresolved = _count_open_exceptions(db, rule) if db is not None else 0
    return ReconciliationResult(
        rule=rule,
        period_start=ps,
        period_end=pe,
        total_diffs=len(diffs),
        ok_count=sum(1 for d in diffs if d.severity == "ok"),
        warning_count=sum(1 for d in diffs if d.severity == "warning"),
        error_count=sum(1 for d in diffs if d.severity == "error"),
        diffs=diffs,
        unresolved_count=unresolved,
    )


# 规则注册表
RULES: dict[RuleName, callable] = {
    "factory_payment": run_factory_payment,
    "install_fee": run_install_fee,
    "promotion": run_promotion,
    "refill_compensation": run_refill_compensation,
    "inventory_value": run_inventory_value,
    "logistics_fee": run_logistics_fee,
    "revenue_alipay": run_revenue_alipay,
    "operating_expense": run_operating_expense,
    "purchase_payment": run_purchase_payment,
}


def run_all(db: Session, **kwargs) -> dict[RuleName, ReconciliationResult]:
    return {name: fn(db, **kwargs) for name, fn in RULES.items()}
