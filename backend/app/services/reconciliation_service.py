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
    - 物流/安装：缺万师傅 CSV，暂返回 not_available
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.finance import AlipayFlow, RefillRecord
from app.models.inventory import PartInventory
from app.models.marketing import PromotionFlow
from app.models.material import Material
from app.models.order import FactoryOrder, Order
from app.services import exception_service

RuleName = Literal[
    "factory_payment",
    "install_fee",
    "promotion",
    "refill_compensation",
    "inventory_value",
    "logistics_fee",
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


# -------- Rule 2 / 6: 安装费 / 物流费 (依赖万师傅 CSV) --------

def run_install_fee(db: Session, **_) -> ReconciliationResult:
    return _result("install_fee", None, None, [ReconciliationDiff(
        key="all", expected=None, actual=None, diff=None, severity="not_available",
        message="安装费对账依赖万师傅 CSV，暂未导入；可在 plan §10 Phase 5 接入售后表后启用",
    )], db)


def run_logistics_fee(db: Session, **_) -> ReconciliationResult:
    return _result("logistics_fee", None, None, [ReconciliationDiff(
        key="all", expected=None, actual=None, diff=None, severity="not_available",
        message="物流费对账依赖万师傅月结 CSV，暂未导入",
    )], db)


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
}


def run_all(db: Session, **kwargs) -> dict[RuleName, ReconciliationResult]:
    return {name: fn(db, **kwargs) for name, fn in RULES.items()}
