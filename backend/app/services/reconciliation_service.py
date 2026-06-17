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
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow, LogisticsBill, RefillRecord, WanshifuBill
from app.models.inventory import PartInventory
from app.models.marketing import AfterSales, BrandMarketing, DailyOperation, OutsourcingExpense, PromotionFlow
from app.models.material import Material
from app.models.order import FactoryOrder, Order, PartPurchase
from app.models.prepay_ledger import PrepayLedger
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
    "refill_commission_payout",
    "refill_express_payout",
    "aftersales_payout",
    "refund_reconciliation",
    "ledger_check",
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


def _tolerance(base: Optional[Decimal], pct: Decimal, abs_floor: Decimal) -> Decimal:
    """容差 = 百分比与绝对值的较大者。base=对账基准额(账单/应收/应付); 缺省退化为绝对值。"""
    if base is None:
        return abs_floor
    try:
        return max(abs_floor, abs(Decimal(str(base))) * pct)
    except (InvalidOperation, ValueError, TypeError):
        return abs_floor


# 阈值可配 (对账建议 9): system_settings 的 recon_pct_threshold / recon_abs_floor
# 覆盖缺省 (0.5% / ¥5)。run_all 与单规则 API 入口处调 load_thresholds 刷新。
_TH = {"pct": Decimal("0.005"), "abs": Decimal("5")}


def load_thresholds(db: Session) -> None:
    try:
        from app.services import settings_service
        p = settings_service.get(db, "recon_pct_threshold", env_fallback=False)
        a = settings_service.get(db, "recon_abs_floor", env_fallback=False)
        if p:
            _TH["pct"] = Decimal(str(p))
        if a:
            _TH["abs"] = Decimal(str(a))
    except Exception:  # pragma: no cover - 配置读取失败用缺省
        pass


def _within(diff: Decimal, *, base: Optional[Decimal] = None,
            pct: Optional[Decimal] = None, abs_floor: Optional[Decimal] = None) -> bool:
    """diff 是否在容差内（取百分比与绝对值的较大者）。"""
    return abs(diff) <= _tolerance(base, pct or _TH["pct"], abs_floor or _TH["abs"])


def _classify(diff: Decimal, *, base: Optional[Decimal] = None,
              pct: Optional[Decimal] = None, abs_floor: Optional[Decimal] = None) -> DiffSeverity:
    tol = _tolerance(base, pct or _TH["pct"], abs_floor or _TH["abs"])
    if abs(diff) <= tol:
        return "ok"
    if abs(diff) <= tol * 10:
        return "warning"
    return "error"


# 工厂别名映射 (对账建议 3): system_settings.factory_aliases 存 JSON {"别名": "标准名"}。
# 货款对账里 工厂下单表名 与 支付宝对手方名 都先归一再比对。
def _factory_aliases(db: Session) -> dict[str, str]:
    try:
        import json
        from app.services import settings_service
        raw = settings_service.get(db, "factory_aliases", env_fallback=False)
        m = json.loads(raw) if raw else {}
        return {str(k).strip(): str(v).strip() for k, v in m.items()} if isinstance(m, dict) else {}
    except Exception:  # pragma: no cover
        return {}


def _canon_factory(name: Optional[str], aliases: dict[str, str]) -> str:
    n = (name or "").strip()
    return aliases.get(n, n) or "(未知工厂)"


# 财务起始线 (用户拍板 2026-06-11): 2025 年数据不再导入, 一切从 2026 年起算。
# 各规则未显式传账期时, 默认从这天开始 — 老年份的半截数据不再制造假差异。
def _finance_start(db: Session) -> date:
    try:
        from app.services import settings_service
        raw = settings_service.get(db, "finance_start_date", env_fallback=False)
        if raw:
            return date.fromisoformat(str(raw).strip())
    except Exception:  # pragma: no cover
        pass
    return date(2026, 1, 1)


def _record_exception(db, *, rule: RuleName, key: str, diff_amount: Decimal, message: str):
    """幂等写: 同 rule:key 已有 open 异常则跳过, 避免每日 cron 重复堆积。

    已被人工「做平」(status=ignored) 的也跳过 — 做平是永久豁免, cron 不会再翻出来。
    """
    from app.models.exception import DataException as _DE
    existing = db.query(_DE).filter(
        _DE.source_table == "reconciliation",
        _DE.source_pk == f"{rule}:{key}",
        _DE.exception_type == "reconciliation_diff",
        _DE.status.in_(("open", "ignored")),
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

    # 两侧名称都先过别名映射 (对账建议 3: "**晶"等掩码/简称对不上标准工厂名)
    aliases = _factory_aliases(db)
    billed_by_factory: dict[str, Decimal] = {}
    for name, billed in db.execute(fo_stmt).all():
        k = _canon_factory(name, aliases)
        billed_by_factory[k] = billed_by_factory.get(k, Decimal("0")) + Decimal(billed or 0)

    # 流水里查 reconciliation_type = factory_payment 的支出 (amount < 0)
    # 评审财务#1: 付款侧也要按账期过滤 (与 promotion/install/logistics/revenue 一致);
    # 原来只过滤了应付侧(order_date), 付款侧取全量 → 传 period 时"当期应付 vs 历史全量实付"虚假大额差。
    flow_stmt = select(
        AlipayFlow.counterparty,
        func.coalesce(func.sum(-AlipayFlow.amount), 0).label("paid"),
    ).where(AlipayFlow.reconciliation_type == "factory_payment")
    if period_start:
        flow_stmt = flow_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        flow_stmt = flow_stmt.where(AlipayFlow.transaction_time <= period_end)
    flow_stmt = flow_stmt.group_by(AlipayFlow.counterparty)
    paid_by_factory: dict[str, Decimal] = {}
    for name, paid in db.execute(flow_stmt).all():
        k = _canon_factory(name, aliases) if name else "(未匹配)"
        paid_by_factory[k] = paid_by_factory.get(k, Decimal("0")) + Decimal(paid or 0)

    # 明细单号 (供用户去核对到底是哪几笔): 应付侧=工厂下单单号(+淘宝单号), 实付侧=支付宝流水号
    fo_detail = select(FactoryOrder.factory_name, FactoryOrder.factory_order_no,
                       FactoryOrder.platform_order_no, FactoryOrder.factory_bill_amount)
    if period_start:
        fo_detail = fo_detail.where(FactoryOrder.order_date >= period_start)
    if period_end:
        fo_detail = fo_detail.where(FactoryOrder.order_date <= period_end)
    fo_records: dict[str, list[str]] = {}
    for fname, fono, pono, amt in db.execute(fo_detail).all():
        if amt is None or Decimal(amt or 0) == 0:
            continue
        k = _canon_factory(fname, aliases)
        lab = f"工厂单 {fono or '?'}" + (f"(淘宝{pono})" if pono else "") + f" 应付¥{Decimal(amt or 0)}"
        fo_records.setdefault(k, []).append(lab)

    flow_detail = select(AlipayFlow.counterparty, AlipayFlow.transaction_no,
                         AlipayFlow.transaction_time, AlipayFlow.amount).where(
        AlipayFlow.reconciliation_type == "factory_payment")
    if period_start:
        flow_detail = flow_detail.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        flow_detail = flow_detail.where(AlipayFlow.transaction_time <= period_end)
    flow_records: dict[str, list[str]] = {}
    for cp, tn, tt, amt in db.execute(flow_detail).all():
        k = _canon_factory(cp, aliases) if cp else "(未匹配)"
        d = tt.strftime("%Y-%m-%d") if tt else "无日期"
        flow_records.setdefault(k, []).append(f"支付宝流水 {tn} 实付¥{-Decimal(amt or 0)} ({d})")

    _CAP = 30  # 每侧最多列 30 条单号, 防止单元格过长; message 里给总笔数
    diffs: list[ReconciliationDiff] = []
    for factory in set(billed_by_factory) | set(paid_by_factory):
        billed = billed_by_factory.get(factory, Decimal("0"))
        paid = paid_by_factory.get(factory, Decimal("0"))
        diff = paid - billed
        sev = _classify(diff, base=billed)
        fos = fo_records.get(factory, [])
        fls = flow_records.get(factory, [])
        msg = (f"{factory}: 应付 ¥{billed}, 实付 ¥{paid}, 差 ¥{diff} "
               f"({len(fos)}笔工厂单 / {len(fls)}笔支付宝流水)")
        related = fos[:_CAP] + fls[:_CAP]
        if len(fos) > _CAP or len(fls) > _CAP:
            related.append(f"… 仅列前{_CAP}, 共 {len(fos)}工厂单/{len(fls)}流水")
        diffs.append(ReconciliationDiff(
            key=factory, expected=billed, actual=paid, diff=diff, severity=sev, message=msg,
            related_records=related,
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

    def _pf_by_month(flow_type: str) -> dict[tuple[int, int], Decimal]:
        stmt = select(
            extract("year", PromotionFlow.transaction_date).label("y"),
            extract("month", PromotionFlow.transaction_date).label("m"),
            func.coalesce(func.sum(PromotionFlow.amount), 0).label("amt"),
        ).where(PromotionFlow.flow_type == flow_type)
        if period_start:
            stmt = stmt.where(PromotionFlow.transaction_date >= period_start)
        if period_end:
            stmt = stmt.where(PromotionFlow.transaction_date <= period_end)
        stmt = stmt.group_by("y", "m")
        out: dict[tuple[int, int], Decimal] = {}
        for y, m, amt in db.execute(stmt).all():
            if y is None or m is None:
                continue
            out[(int(y), int(m))] = Decimal(amt or 0)
        return out

    # 对账建议 4 (充值/退款闭环): 支付宝打出去的钱对应的是推广账户「充值」,
    # 「支出(消耗)」发生在平台内部 — 推广表有充值记录时按充值口径比对, 否则退回旧口径(支出)。
    by_month_recharge = _pf_by_month("充值")
    by_month_spend = _pf_by_month("支出")
    use_recharge = bool(by_month_recharge)
    by_month_pf = by_month_recharge if use_recharge else by_month_spend
    pf_label = "推广充值" if use_recharge else "推广支出"

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

    # 每月支付宝推广流水号 (供核对): reconciliation_type='promotion' 的支出
    pf_detail = select(AlipayFlow.transaction_time, AlipayFlow.transaction_no,
                       AlipayFlow.amount).where(AlipayFlow.reconciliation_type == "promotion")
    if period_start:
        pf_detail = pf_detail.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        pf_detail = pf_detail.where(AlipayFlow.transaction_time <= period_end)
    flow_by_month: dict[tuple[int, int], list[str]] = {}
    for tt, tn, amt in db.execute(pf_detail).all():
        if tt is None:
            continue
        flow_by_month.setdefault((tt.year, tt.month), []).append(
            f"支付宝流水 {tn} ¥{-Decimal(amt or 0)} ({tt.strftime('%Y-%m-%d')})")

    diffs: list[ReconciliationDiff] = []
    for key in sorted(set(by_month_pf) | set(by_month_af)):
        y, m = key
        expected = by_month_pf.get(key, Decimal("0"))
        actual = by_month_af.get(key, Decimal("0"))
        diff = actual - expected
        sev = _classify(diff, base=expected)
        period_key = f"{y}-{m:02d}"
        fls = flow_by_month.get(key, [])
        msg = f"{period_key}: {pf_label} ¥{expected}, 支付宝打款 ¥{actual}, 差 ¥{diff} ({len(fls)}笔支付宝流水)"
        diffs.append(ReconciliationDiff(
            key=period_key, expected=expected, actual=actual, diff=diff,
            severity=sev, message=msg, related_records=fls[:50],
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="promotion", key=period_key, diff_amount=diff, message=msg)

    # 退款闭环: 支付宝推广类回流 (amount>0) ↔ 推广表「退款」(按月)
    by_month_refund_pf = _pf_by_month("退款")
    af_in_stmt = select(
        extract("year", AlipayFlow.transaction_time).label("y"),
        extract("month", AlipayFlow.transaction_time).label("m"),
        func.coalesce(func.sum(AlipayFlow.amount), 0).label("got"),
    ).where(AlipayFlow.reconciliation_type == "promotion", AlipayFlow.amount > 0)
    if period_start:
        af_in_stmt = af_in_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        af_in_stmt = af_in_stmt.where(AlipayFlow.transaction_time <= period_end)
    by_month_refund_af: dict[tuple[int, int], Decimal] = {
        (int(y), int(m)): Decimal(got or 0)
        for y, m, got in db.execute(af_in_stmt.group_by("y", "m")).all()
        if y is not None and m is not None
    }
    for key in sorted(set(by_month_refund_pf) | set(by_month_refund_af)):
        y, m = key
        exp_r = by_month_refund_pf.get(key, Decimal("0"))
        act_r = by_month_refund_af.get(key, Decimal("0"))
        d_r = act_r - exp_r
        sev_r = _classify(d_r, base=exp_r)
        k_r = f"{y}-{m:02d} 退款"
        msg_r = f"{y}-{m:02d} 推广退款: 推广表登记 ¥{exp_r}, 支付宝回流 ¥{act_r}, 差 ¥{d_r}"
        diffs.append(ReconciliationDiff(key=k_r, expected=exp_r, actual=act_r,
                                        diff=d_r, severity=sev_r, message=msg_r))
        if sev_r != "ok" and record_exceptions:
            _record_exception(db, rule="promotion", key=k_r, diff_amount=d_r, message=msg_r)

    # 余额勾稽提示: 累计充值 vs 累计消耗 (消耗 > 充值 = 推广账户在吃老本或数据缺)
    if use_recharge:
        total_recharge = sum(by_month_recharge.values(), Decimal("0"))
        total_spend = sum(by_month_spend.values(), Decimal("0"))
        diffs.append(ReconciliationDiff(
            key="充值vs消耗", expected=total_recharge, actual=total_spend,
            diff=total_spend - total_recharge,
            severity="ok" if total_spend <= total_recharge else "warning",
            message=f"账期内累计充值 ¥{total_recharge}, 累计消耗 ¥{total_spend}"
                    + (" — 消耗超过充值, 请核对推广账户期初余额或补充值记录"
                       if total_spend > total_recharge else ""),
        ))

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
            # 主订单不在订单总表 (老订单未导入) → 数据缺失提示, 不算差异
            # (2026-06-11: 此前 940 条这类记录把"提示"数刷到 1100+)
            diffs.append(ReconciliationDiff(
                key=r.order_no,
                expected=r.total_cost,
                actual=None,
                diff=None,
                severity="not_available",
                message=f"补单 {r.order_no}: 主订单未导入系统, 暂无法对比 (导入该期订单后自动恢复)",
            ))
            continue
        # 用户拍板公式 (2026-06-11): total_cost = 本金 + 佣金 + 快递 + 税金 + 淘宝手续费;
        # 主单实付 ≈ 本金 → 对比口径 = 实付 ↔ (total_cost − 佣金 − 快递 − 平台手续费)。
        # 税金无独立字段, 留在残差里 → 容差放宽到 2% 吸收。
        structural = (Decimal(r.commission or 0) + Decimal(r.refill_freight or 0)
                      + Decimal(r.platform_fee or 0))
        expected = (Decimal(r.total_cost or 0) - structural)
        paid_raw = paid_by_order[r.order_no]
        # 主单存在但实付为空/为 0 (历史导入缺字段) → 数据缺失, 不是真实差额,
        # 报提示而非严重 (2026-06-11: 此前 0 元假差额刷出 1300+ 条噪音)。
        if paid_raw is None or Decimal(paid_raw or 0) == 0:
            diffs.append(ReconciliationDiff(
                key=r.order_no, expected=expected, actual=None, diff=None,
                severity="not_available",
                message=f"补单 {r.order_no}: 主单缺实付金额 (待补订单数据), 暂无法对比。",
            ))
            continue
        actual = paid_raw or Decimal("0")
        diff = actual - expected
        sev = _classify(diff, base=expected, abs_floor=Decimal("1"), pct=Decimal("0.02"))
        msg = (f"补单 {r.order_no}: 本金应为 ¥{expected} (=总成本 {r.total_cost} − 佣金/快递/手续费 "
               f"{structural}), 主单实付 ¥{actual}, 差 ¥{diff} (容差含税金)")
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

    part_count = len(diffs)

    # 成品库存价值 (对账建议 5): 成品现货 × 该产品定价 SKU 的平均物理成本
    from app.models.inventory import ProductInventory
    from app.models.pricing import PricingSku
    cost_rows = db.execute(
        select(PricingSku.product_code, func.avg(PricingSku.physical_cost))
        .where(PricingSku.physical_cost.isnot(None))
        .group_by(PricingSku.product_code)
    ).all()
    cost_by_product = {c: Decimal(str(v)) for c, v in cost_rows if c and v is not None}
    product_value = Decimal("0")
    missing_cost = 0
    for code, qty in db.execute(
        select(ProductInventory.product_code,
               func.coalesce(func.sum(ProductInventory.physical_qty), 0))
        .group_by(ProductInventory.product_code)
    ).all():
        q = Decimal(int(qty or 0))
        if q <= 0:
            continue
        cost = cost_by_product.get(code)
        if cost is None:
            missing_cost += 1
            continue
        v = (cost * q).quantize(Decimal("0.01"))
        product_value += v
        diffs.append(ReconciliationDiff(
            key=f"成品:{code}", expected=v, actual=v, diff=Decimal("0"), severity="ok",
            message=f"成品 {code}: {q} 件 × 物理成本均值 ¥{cost.quantize(Decimal('0.01'))} = ¥{v}",
        ))

    # 汇总条目
    grand = total_value + product_value
    diffs.append(ReconciliationDiff(
        key="TOTAL",
        expected=grand, actual=grand, diff=Decimal("0"),
        severity="ok" if (missing_price == 0 and missing_cost == 0) else "warning",
        message=(f"账面价值合计 ¥{grand} = 配件 ¥{total_value} ({part_count} 项; {missing_price} 项缺价格)"
                 f" + 成品 ¥{product_value} ({missing_cost} 个产品缺成本未计入)"),
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
        # 对账建议 7: 万师傅账单未导入时不再用售后表做"假对比" (回退口径大量缺数,
        # 差额没有意义), 直接提示导账单后启用。
        return _result("install_fee", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="万师傅账单未导入, 暂不对比 (到 物流→万师傅对账单 导入后自动启用)",
        )], db)
        # (旧回退口径保留备查)
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
        sev = _classify(diff, base=exp)
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
        # 对账建议 6: 物流账单未导入时不再用订单 actual_freight 做"假对比"
        # (该字段大量为空, 回退结果系统性偏小), 直接提示导账单后启用。
        return _result("logistics_fee", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="物流账单未导入, 暂不对比 (到 物流→物流账单 导入后自动启用)",
        )], db)
        o_stmt = select(Order.order_date, Order.actual_freight).where(  # 旧回退口径保留备查
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
        sev = _classify(diff, base=exp)
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

    # ── 对账建议 1 (2026-06-11 重构): 按关联订单号逐单配对, 月汇总只做兜底 ──
    # 解决旧月度口径的天然错位: 订单按"下单月"、支付宝按"到账月", 月底单永远跨月差。
    # 补单不剔除 (用户拍板: 补单回款含税费/手续费扣除, 计入收入口径, 两侧对称)。
    # 注意: 不排 is_historical — 本库导入路径给几乎所有真实订单都打了历史标。
    from app.services.smart_matching_service import _order_key as _okey

    o_rows = db.execute(
        select(Order.order_no, Order.order_date, Order.paid_amount).where(
            Order.status.notin_(["cancelled", "pending_payment"]),
            Order.order_date.isnot(None),
            *( [Order.order_date >= period_start] if period_start else [] ),
            *( [Order.order_date <= period_end] if period_end else [] ),
        )
    ).all()
    order_paid: dict[str, Decimal] = {}
    order_month: dict[str, str] = {}
    for no, d, paid in o_rows:
        k = _okey(no)
        if not k:
            continue
        order_paid[k] = order_paid.get(k, Decimal("0")) + Decimal(paid or 0)
        order_month[k] = _month_key(d) or "(无日期)"

    from sqlalchemy import or_ as _or
    af_stmt = select(AlipayFlow.transaction_time, AlipayFlow.amount, AlipayFlow.related_order_no).where(
        AlipayFlow.amount > 0,
        AlipayFlow.related_order_no.isnot(None),
        # 用户拍板 (2026-06-11 建议3): 退款回流不算该单收入, 否则退款多的单差额虚高
        _or(AlipayFlow.reconciliation_type.is_(None),
            AlipayFlow.reconciliation_type != "refund_in"),
    )
    if period_start:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time <= period_end)
    flow_income: dict[str, Decimal] = {}        # 订单键 → 收入合计
    orphan_income_by_month: dict[str, Decimal] = {}   # 配不到订单的收入按月兜底
    for t, amt, ron in db.execute(af_stmt).all():
        k = _okey(ron)
        if k and k in order_paid:
            flow_income[k] = flow_income.get(k, Decimal("0")) + Decimal(amt or 0)
        else:
            mk = _month_key(t.date() if hasattr(t, "date") else t) or "(无日期)"
            orphan_income_by_month[mk] = orphan_income_by_month.get(mk, Decimal("0")) + Decimal(amt or 0)

    if not order_paid and not flow_income and not orphan_income_by_month:
        return _result("revenue_alipay", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无订单营收 / 支付宝订单收入可对账 (空数据)",
        )], db)

    diffs: list[ReconciliationDiff] = []
    matched_ok = 0
    unmatched_paid_by_month: dict[str, Decimal] = {}   # 没配到任何流水的订单 → 月兜底
    for k, paid in order_paid.items():
        got = flow_income.get(k)
        if got is None:
            if paid > 0:
                mk = order_month.get(k, "(无日期)")
                unmatched_paid_by_month[mk] = unmatched_paid_by_month.get(mk, Decimal("0")) + paid
            continue
        diff = got - paid
        sev = _classify(diff, base=paid, abs_floor=Decimal("50"))
        if sev == "ok":
            matched_ok += 1
            continue
        msg = f"订单 {k}: 订单实付 ¥{paid}, 支付宝该单收入 ¥{got}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(key=k, expected=paid, actual=got, diff=diff,
                                        severity=sev, message=msg))
        if record_exceptions:
            _record_exception(db, rule="revenue_alipay", key=k, diff_amount=diff, message=msg)

    diffs.insert(0, ReconciliationDiff(
        key="逐单配对", expected=None, actual=None, diff=None, severity="ok",
        message=f"逐单配对一致 {matched_ok} 单 (差额≤容差不逐条列出)",
    ))
    # 月度兜底: 两侧各自配不上的部分按月对照 (到账跨月/未导流水/未导订单)
    for mk in sorted(set(unmatched_paid_by_month) | set(orphan_income_by_month)):
        exp = unmatched_paid_by_month.get(mk, Decimal("0"))
        act = orphan_income_by_month.get(mk, Decimal("0"))
        diff = act - exp
        sev = _classify(diff, base=exp, abs_floor=Decimal("50"))
        msg = (f"{mk} (月度兜底, 仅未逐单配对部分): 未配到流水的订单实付 ¥{exp}, "
               f"未配到订单的支付宝收入 ¥{act}, 差 ¥{diff}")
        diffs.append(ReconciliationDiff(key=f"{mk} 兜底", expected=exp, actual=act,
                                        diff=diff, severity=sev, message=msg))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="revenue_alipay", key=f"{mk} 兜底", diff_amount=diff, message=msg)
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
    linked: dict[str, int] = {"日常经营": 0, "人员外包": 0, "品牌营销": 0}
    total: dict[str, int] = {"日常经营": 0, "人员外包": 0, "品牌营销": 0}
    for r in db.execute(do_stmt).scalars().all():
        total["日常经营"] += 1
        if r.alipay_flow_no:  # 只处理有流水号的记录
            linked["日常经营"] += 1
            records.append(("日常经营", r.record_date, Decimal(r.amount or 0), r.alipay_flow_no, f"日常#{r.id}"))
    for r in db.execute(op_stmt).scalars().all():
        total["人员外包"] += 1
        if r.alipay_flow_no:
            linked["人员外包"] += 1
            records.append(("人员外包", r.payment_date, Decimal(r.amount or 0), r.alipay_flow_no, f"外包#{r.id}({r.payee})"))
    for r in db.execute(bm_stmt).scalars().all():
        total["品牌营销"] += 1
        if r.alipay_flow_no:
            linked["品牌营销"] += 1
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
        sev = _classify(diff, base=exp)
        msg = f"{month}: 经营已关联支出 ¥{exp}, 支付宝实付 ¥{act}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(
            key=month, expected=exp, actual=act, diff=diff, severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="operating_expense", key=month, diff_amount=diff, message=msg)

    # 对账建议 11: 未挂流水号的记录不在对账范围内 — 显示覆盖率, 防"没报差异=都对上了"的错觉
    cover_parts = []
    for src in ("日常经营", "人员外包", "品牌营销"):
        if total[src]:
            cover_parts.append(f"{src} {linked[src]}/{total[src]}")
    if cover_parts:
        total_all = sum(total.values())
        linked_all = sum(linked.values())
        pct = (linked_all / total_all * 100) if total_all else 100
        diffs.append(ReconciliationDiff(
            key="覆盖率", expected=None, actual=None, diff=None,
            severity="ok" if pct >= 80 else "warning",
            message=(f"流水号覆盖率 {pct:.0f}% ({'; '.join(cover_parts)}) — "
                     f"未挂流水号的 {total_all - linked_all} 条不在对账范围, 建议补录流水号。"),
        ))
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
        sev = _classify(diff, base=exp)
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


# -------- Rule 10/11/12: 代付台账对账 (补单佣金 / 补单快递 / 售后 实付 ↔ 订单应摊) --------

def _prepay_income_by_month(db: Session, category: str,
                            ps: Optional[date], pe: Optional[date]) -> dict[str, Decimal]:
    """代付台账某类的实付(进项)按月。"""
    stmt = select(PrepayLedger.pay_date, PrepayLedger.amount).where(PrepayLedger.category == category)
    if ps:
        stmt = stmt.where(PrepayLedger.pay_date >= ps)
    if pe:
        stmt = stmt.where(PrepayLedger.pay_date <= pe)
    return _sum_by_month(db.execute(stmt).all())


def _run_prepay(db: Session, *, rule: RuleName, category: str,
                billed_by_month: dict[str, Decimal],
                ps: Optional[date], pe: Optional[date], record_exceptions: bool,
                noun: str = "费用", source_hint: str = "业务表") -> ReconciliationResult:
    """通用: 应摊(出项, billed_by_month) ↔ 代付台账实付(进项)。差额=实付-应摊。

    noun/source_hint 用于把差异说明写成人话 (用户反馈"应摊¥300"看不懂)。
    """
    paid = _prepay_income_by_month(db, category, ps, pe)
    if not billed_by_month and not paid:
        return _result(rule, ps, pe, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无订单应摊 / 代付台账数据可对账 (导入代付台账后启用)",
        )], db)
    diffs: list[ReconciliationDiff] = []
    for key in sorted(set(billed_by_month) | set(paid)):
        exp = billed_by_month.get(key, Decimal("0"))   # 应摊
        act = paid.get(key, Decimal("0"))               # 实际代付
        diff = act - exp
        sev = _classify(diff, base=exp)
        msg = (f"{key} 月{noun}: {source_hint}里登记该月共 ¥{exp} (=应摊), "
               f"代付台账里实际付出 ¥{act}, 两边差 ¥{diff}。")
        if act == 0 and exp > 0:
            msg += " 实付为 0 通常是该月代付台账还没导入, 或这笔钱没走代付。"
        elif exp == 0 and act > 0:
            msg += f" 应摊为 0 说明{source_hint}里没登记这笔, 请补录或核对月份归属。"
        diffs.append(ReconciliationDiff(key=key, expected=exp, actual=act, diff=diff, severity=sev, message=msg))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule=rule, key=key, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result(rule, ps, pe, diffs, db)


def run_refill_commission_payout(db: Session, *, period_start=None, period_end=None,
                                 record_exceptions: bool = True) -> ReconciliationResult:
    """补单佣金: 订单应摊 RefillRecord.commission ↔ 代付台账 refill_commission 实付 (按月)。"""
    stmt = select(RefillRecord.refill_date, RefillRecord.commission).where(RefillRecord.commission.isnot(None))
    if period_start:
        stmt = stmt.where(RefillRecord.refill_date >= period_start)
    if period_end:
        stmt = stmt.where(RefillRecord.refill_date <= period_end)
    billed = _sum_by_month(db.execute(stmt).all())
    return _run_prepay(db, rule="refill_commission_payout", category="refill_commission",
                       billed_by_month=billed, ps=period_start, pe=period_end, record_exceptions=record_exceptions,
                       noun="补单佣金", source_hint="补单记录(佣金字段)")


def run_refill_express_payout(db: Session, *, period_start=None, period_end=None,
                              record_exceptions: bool = True) -> ReconciliationResult:
    """补单快递: 订单应摊 RefillRecord.refill_freight ↔ 代付台账 refill_express 实付 (按月)。"""
    stmt = select(RefillRecord.refill_date, RefillRecord.refill_freight).where(RefillRecord.refill_freight.isnot(None))
    if period_start:
        stmt = stmt.where(RefillRecord.refill_date >= period_start)
    if period_end:
        stmt = stmt.where(RefillRecord.refill_date <= period_end)
    billed = _sum_by_month(db.execute(stmt).all())
    return _run_prepay(db, rule="refill_express_payout", category="refill_express",
                       billed_by_month=billed, ps=period_start, pe=period_end, record_exceptions=record_exceptions,
                       noun="补单快递费", source_hint="补单记录(补单运费字段)")


def run_aftersales_payout(db: Session, *, period_start=None, period_end=None,
                          record_exceptions: bool = True) -> ReconciliationResult:
    """售后: 订单应摊售后(赔付费+好评返+二次上门+返厂运费) ↔ 代付台账 aftersales 实付 (按月)。"""
    stmt = select(AfterSales)
    if period_start:
        stmt = stmt.where(AfterSales.processed_at >= period_start)
    if period_end:
        stmt = stmt.where(AfterSales.processed_at <= period_end)
    billed: dict[str, Decimal] = {}
    for a in db.execute(stmt).scalars().all():
        fee = (Decimal(a.compensation_fee or 0) + Decimal(a.good_review_refund or 0)
               + Decimal(a.second_visit_fee or 0) + Decimal(a.return_pack_freight or 0))
        if fee == 0:
            continue
        key = _month_key(a.processed_at) or "(无日期)"
        billed[key] = billed.get(key, Decimal("0")) + fee
    return _run_prepay(db, rule="aftersales_payout", category="aftersales",
                       billed_by_month=billed, ps=period_start, pe=period_end, record_exceptions=record_exceptions,
                       noun="售后赔付", source_hint="售后表(赔付+好评返+二次上门+返厂运费)")


# -------- Rule 13: 退款闭环 (订单退款应退 ↔ 支付宝实际退款流出) --------

def run_refund_reconciliation(db: Session, *, period_start=None, period_end=None,
                              record_exceptions: bool = True) -> ReconciliationResult:
    """退款闭环: 订单退款应退 (Order.refund_amount, 按 refund_date) ↔ 支付宝实际退款流出
    (AlipayFlow reconciliation_type='refund_out', 按月)。差额=实退-应退。"""
    o_stmt = select(Order.refund_date, Order.refund_amount).where(
        Order.refund_amount.isnot(None), Order.refund_amount > 0,
    )
    if period_start:
        o_stmt = o_stmt.where(Order.refund_date >= period_start)
    if period_end:
        o_stmt = o_stmt.where(Order.refund_date <= period_end)
    billed = _sum_by_month(db.execute(o_stmt).all())  # 应退

    af_stmt = select(AlipayFlow.transaction_time, func.abs(AlipayFlow.amount)).where(
        AlipayFlow.reconciliation_type == "refund_out",
    )
    if period_start:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time <= period_end)
    paid = _sum_by_month(
        (t.date() if hasattr(t, "date") else t, a) for t, a in db.execute(af_stmt).all()
    )  # 实退

    if not billed and not paid:
        return _result("refund_reconciliation", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无订单退款 / 支付宝退款流水可对账 (空)",
        )], db)

    diffs: list[ReconciliationDiff] = []
    for key in sorted(set(billed) | set(paid)):
        exp = billed.get(key, Decimal("0"))
        act = paid.get(key, Decimal("0"))
        diff = act - exp
        sev = _classify(diff, base=exp)
        msg = f"{key}: 订单应退 ¥{exp}, 支付宝实退 ¥{act}, 差 ¥{diff}"
        diffs.append(ReconciliationDiff(key=key, expected=exp, actual=act, diff=diff, severity=sev, message=msg))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="refund_reconciliation", key=key, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("refund_reconciliation", period_start, period_end, diffs, db)


# -------- Rule 14: 总账级勾稽 (账户余额月变动 ↔ 支付宝流水净额) --------

# U3 拍板 (2026-06): 爱群号曾混用私人支出, 流水天然不连续 → 不做流水勾稽, 只查账面自洽。
_LEDGER_FLOW_EXEMPT = ("爱群号",)


def run_ledger_check(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """总账级勾稽: 每个账户每月两道检查。

    ① 账面自洽: 期初 + 收入 - 支出 = 期末 (account_balances 行内勾稽);
    ② 流水勾稽: 期末 - 期初 ↔ 该账户当月支付宝流水净额 (剔除期初调整行)。
       只对已导入流水的支付宝账户做; 银行卡/推广户等无流水账户只做 ①。
    任一道超阈值 = 当月有钱进出没进系统 (漏导流水 / 余额录错 / 表外收支)。
    """
    from datetime import timedelta

    from app.models.finance import ALIPAY_ACCOUNTS, AccountBalance

    rows = db.execute(select(AccountBalance)).scalars().all()
    diffs: list[ReconciliationDiff] = []
    checked = 0
    for r in sorted(rows, key=lambda x: (x.account_name, x.period_year, x.period_month)):
        m_start = date(r.period_year, r.period_month, 1)
        m_next = (m_start + timedelta(days=32)).replace(day=1)
        if period_start and m_next <= period_start:
            continue
        if period_end and m_start > period_end:
            continue
        checked += 1
        key = f"{r.account_name} {r.period_year}-{r.period_month:02d}"
        opening = Decimal(r.opening_balance or 0)
        closing = Decimal(r.closing_balance or 0)
        income = Decimal(r.income or 0)
        expense = Decimal(r.expense or 0)

        # ① 账面自洽: 期初+收-支=期末
        book = opening + income - expense
        book_diff = closing - book
        if not _within(book_diff, base=income + expense):
            msg = (f"{key}: 账面不自洽 — 期初 ¥{opening} + 收入 ¥{income} - 支出 ¥{expense}"
                   f" = ¥{book}, 但期末记 ¥{closing}, 差 ¥{book_diff} (余额或收支有录错)")
            diffs.append(ReconciliationDiff(
                key=f"{key} 账面", expected=book, actual=closing, diff=book_diff,
                severity=_classify(book_diff, base=income + expense), message=msg,
            ))
            if record_exceptions:
                _record_exception(db, rule="ledger_check", key=f"{key} 账面",
                                  diff_amount=book_diff, message=msg)

        # ② 流水勾稽 (只对有流水的支付宝账户; 爱群号豁免)
        if r.account_name not in ALIPAY_ACCOUNTS:
            continue
        if r.account_name in _LEDGER_FLOW_EXEMPT:
            diffs.append(ReconciliationDiff(
                key=key, expected=closing - opening, actual=None, diff=None,
                severity="not_available",
                message=f"{key}: 爱群号流水不连续 (已拍板豁免), 只查账面自洽, 不做流水勾稽",
            ))
            continue
        net = Decimal(db.execute(
            select(func.coalesce(func.sum(AlipayFlow.amount), 0)).where(
                AlipayFlow.account == r.account_name,
                AlipayFlow.transaction_time >= m_start,
                AlipayFlow.transaction_time < m_next,
                # 期初调整是开账用的虚拟行, 不是当月真实进出
                AlipayFlow.reconciliation_status != "opening_balance",
            )
        ).scalar() or 0)
        delta = closing - opening
        flow_diff = net - delta
        # 容差基准用月流水规模 (而非余额变动 — 变动可能接近 0 但流水巨大)
        sev = _classify(flow_diff, base=max(abs(net), abs(delta)))
        msg = (f"{key}: 余额变动 ¥{delta} (期初 ¥{opening} → 期末 ¥{closing}),"
               f" 当月流水净额 ¥{net}, 差 ¥{flow_diff}")
        if sev != "ok":
            msg += " — 流水净额对不上余额变动: 可能漏导当月流水 / 余额快照录错 / 有表外进出"
        diffs.append(ReconciliationDiff(
            key=key, expected=delta, actual=net, diff=flow_diff, severity=sev, message=msg,
        ))
        if sev != "ok" and record_exceptions:
            _record_exception(db, rule="ledger_check", key=key, diff_amount=flow_diff, message=msg)

    if not checked:
        diffs.append(ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="账户余额表 (account_balances) 在账期内无数据, 录入月度余额后自动启用总账勾稽",
        ))
    if record_exceptions:
        db.flush()
    return _result("ledger_check", period_start, period_end, diffs, db)


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
    "refill_commission_payout": run_refill_commission_payout,
    "refill_express_payout": run_refill_express_payout,
    "aftersales_payout": run_aftersales_payout,
    "refund_reconciliation": run_refund_reconciliation,
    "ledger_check": run_ledger_check,
}


def run_all(db: Session, **kwargs) -> dict[RuleName, ReconciliationResult]:
    load_thresholds(db)   # 阈值可配 (对账建议 9)
    # 财务起始线: 未显式传账期时默认从 2026-01-01 起 (2025 不导入, 用户拍板)
    if not kwargs.get("period_start"):
        kwargs["period_start"] = _finance_start(db)
    results = {name: fn(db, **kwargs) for name, fn in RULES.items()}
    # 异常池同步时间 (对账建议 9/审计): 记录最近一次"写异常"的对账时间
    if kwargs.get("record_exceptions", True):
        try:
            from datetime import datetime, timezone

            from app.services import settings_service
            settings_service.set_value(db, "recon_exceptions_synced_at",
                                       datetime.now(timezone.utc).isoformat())
        except Exception:  # pragma: no cover
            pass
    return results


# ---------------------------------------------------------------------------
# 对账准确月份指标 (用户拍板 2026-06-15): 一眼看哪几个月财务"真核准"
# (收款/余额/对账/售后/工厂全齐、无 open 财务异常)
# ---------------------------------------------------------------------------
_FIN_RECON_EXC = ("order_missing_alipay", "alipay_balance_gap", "reconciliation_diff",
                  "alipay_flow_no_missing", "factory_recon_incomplete", "alipay_duplicate_flow")


def _exc_month(db: Session, exc) -> Optional[str]:
    """异常归到哪个月: 流水类按流水时间, 订单/售后类按订单日期, 其它取 context.month。"""
    st = exc.source_table
    if st == "alipay_flows":
        f = db.get(AlipayFlow, int(exc.source_pk)) if (exc.source_pk or "").isdigit() else None
        return f.transaction_time.strftime("%Y-%m") if (f and f.transaction_time) else None
    if st in ("orders", "after_sales"):
        o = None
        if st == "orders" and (exc.source_pk or "").isdigit():
            o = db.get(Order, int(exc.source_pk))
        if o is None:
            o = db.execute(select(Order).where(Order.order_no == exc.source_pk)).scalars().first()
        return o.order_date.strftime("%Y-%m") if (o and o.order_date) else None
    return (exc.context or {}).get("month")


def reconciliation_accuracy_by_month(db: Session) -> list[dict]:
    """按月给"对账准确度": 该月有订单且无任何 open 财务对账异常(收款/余额/对账/售后/工厂)→ 已核准。
    财务起始线(2026-01)之前不计。用户拍板 2026-06-15: 一眼看哪几个月财务是真准的。"""
    from collections import defaultdict

    from app.models.exception import DataException
    start = _finance_start(db)
    start_m = start.strftime("%Y-%m") if start else "2026-01"
    months: dict = defaultdict(lambda: {"orders": 0, "open_issues": 0, "by_type": defaultdict(int)})
    for (od,) in db.execute(select(Order.order_date).where(Order.order_date.isnot(None))).all():
        if od:
            months[od.strftime("%Y-%m")]["orders"] += 1
    for e in db.execute(select(DataException).where(
            DataException.status == "open",
            DataException.exception_type.in_(_FIN_RECON_EXC))).scalars().all():
        m = _exc_month(db, e)
        if m:
            months[m]["open_issues"] += 1
            months[m]["by_type"][e.exception_type] += 1
    out: list[dict] = []
    for m in sorted(months):
        if m < start_m:
            continue
        d = months[m]
        out.append({
            "month": m, "orders": d["orders"], "open_issues": d["open_issues"],
            "accurate": d["orders"] > 0 and d["open_issues"] == 0,
            "by_type": dict(d["by_type"]),
        })
    return out
