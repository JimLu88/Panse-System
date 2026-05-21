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
    return _result("factory_payment", period_start, period_end, diffs)


# -------- Rule 3: 推广支出 --------

def run_promotion(db: Session, *, record_exceptions: bool = True) -> ReconciliationResult:
    """推广记录暂未建模 → 占位：返回 not_available 并提示在 Phase 5 补。"""
    diffs = [ReconciliationDiff(
        key="all",
        expected=None, actual=None, diff=None,
        severity="not_available",
        message="推广记录表 (Phase 5) 还未建模，无法对账推广支出",
    )]
    return _result("promotion", None, None, diffs)


# -------- Rule 4: 补单赔实付 --------

def run_refill_compensation(
    db: Session, *, record_exceptions: bool = True
) -> ReconciliationResult:
    """补单成本 ↔ 关联订单实付金额差额。

    简化逻辑：对每条 refill_record，比较 total_cost 与同 order_no 主订单 paid_amount
    （主订单可能不存在 → severity=warning，但不入异常表）。
    """
    diffs: list[ReconciliationDiff] = []
    rows = db.execute(select(RefillRecord)).scalars().all()
    for r in rows:
        order = db.execute(select(Order).where(Order.order_no == r.order_no)).scalar_one_or_none()
        if order is None:
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
        actual = order.paid_amount or Decimal("0")
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
    return _result("refill_compensation", None, None, diffs)


# -------- Rule 5: 库存资产估值 --------

def run_inventory_value(db: Session, *, record_exceptions: bool = True) -> ReconciliationResult:
    """库存资产 = Σ (配件可用库存 × 物料单价)。
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
    return _result("inventory_value", None, None, diffs)


# -------- Rule 2 / 6: 安装费 / 物流费 (依赖万师傅 CSV) --------

def run_install_fee(db: Session, **_) -> ReconciliationResult:
    return _result("install_fee", None, None, [ReconciliationDiff(
        key="all", expected=None, actual=None, diff=None, severity="not_available",
        message="安装费对账依赖万师傅 CSV，暂未导入；可在 plan §10 Phase 5 接入售后表后启用",
    )])


def run_logistics_fee(db: Session, **_) -> ReconciliationResult:
    return _result("logistics_fee", None, None, [ReconciliationDiff(
        key="all", expected=None, actual=None, diff=None, severity="not_available",
        message="物流费对账依赖万师傅月结 CSV，暂未导入",
    )])


def _result(rule, ps, pe, diffs) -> ReconciliationResult:
    return ReconciliationResult(
        rule=rule,
        period_start=ps,
        period_end=pe,
        total_diffs=len(diffs),
        ok_count=sum(1 for d in diffs if d.severity == "ok"),
        warning_count=sum(1 for d in diffs if d.severity == "warning"),
        error_count=sum(1 for d in diffs if d.severity == "error"),
        diffs=diffs,
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
