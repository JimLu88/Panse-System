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

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
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
from app.models.settlement import OrderSettlement
from app.services import exception_service

RuleName = Literal[
    "factory_payment",
    "install_fee",
    "promotion",
    "refill_compensation",
    "refill_transfer",
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
    # 博冠等"货款走个人账户(爱群号/主力号)、单独按月对账"的工厂, 从通用工厂货款对账排除:
    # 它们的实付在货款户里(被 #1+#2 account 角色排出 factory_payment 流水), 通用对账永远看不到→假报"实付0"。
    # 走专用月度对账(#8)。配置 system_settings['factories_separate_recon'] = ["玉山县博冠家具有限公司"] (用户 2026-06-29)。
    import json as _json_sep
    try:
        from app.services import settings_service as _ss_sep
        _sep_raw = _ss_sep.get(db, "factories_separate_recon", env_fallback=False)
        _separate_factories = {_canon_factory(str(n).strip(), aliases)
                               for n in (_json_sep.loads(_sep_raw) if _sep_raw else []) if str(n).strip()}
    except Exception:  # pragma: no cover - 配置坏不拦对账
        _separate_factories = set()
    billed_by_factory: dict[str, Decimal] = {}
    for name, billed in db.execute(fo_stmt).all():
        k = _canon_factory(name, aliases)
        billed_by_factory[k] = billed_by_factory.get(k, Decimal("0")) + Decimal(billed or 0)

    # 已登记的物料/配件供应商(木隅大板/岩板/玻璃/五金…)走【送货单对账】, 不是 FactoryOrder 成品工厂。
    # 它们的付款也标 factory_payment(供 supplier_payment_matcher 配送货单), 但【无工厂下单】→ 若进
    # 本对账会假报"应付0 实付X"。故: 对手方命中已登记供应商关键字、且该对手方无任何工厂单的, 在此排除
    # (用户 2026-06-29: 木隅工厂其实是配件采购被误当工厂对账)。
    from app.models.supplier import Supplier as _Supplier
    _sup_kws: list[str] = []
    for _sn, _kws in db.execute(
        select(_Supplier.name, _Supplier.alipay_counterparty_keywords).where(_Supplier.is_active.is_(True))
    ).all():
        _sup_kws.extend(k.strip() for k in (list(_kws or []) + [_sn]) if k and str(k).strip())

    def _is_supplier_cp(cp: Optional[str]) -> bool:
        return bool(cp) and any(kw in cp for kw in _sup_kws)

    # #1 账户角色: 货款户(爱群号/主力号)/内部户(佳宝号)= 非营收账户, 其支出走博冠按月货款专用对账(#8)
    # /送货单对账, 不是通用工厂货款。它们的脏流水(符号丢/双导)被误标 factory_payment 也在此【按账户】排除
    # → 根治"伟男(主力号)/**男(爱群号) 漏进工厂对账报假差 + 僵尸复发"(用户 2026-06-29: 博冠走李爱群+主账号)。
    from app.services import account_registry_service as _acct_reg
    _non_rev_accts = _acct_reg.non_revenue_accounts(db)

    # 流水里查 reconciliation_type = factory_payment 的支出 (amount < 0)
    # 评审财务#1: 付款侧也要按账期过滤 (与 promotion/install/logistics/revenue 一致);
    # 原来只过滤了应付侧(order_date), 付款侧取全量 → 传 period 时"当期应付 vs 历史全量实付"虚假大额差。
    flow_stmt = select(
        AlipayFlow.counterparty,
        func.coalesce(func.sum(-AlipayFlow.amount), 0).label("paid"),
    ).where(AlipayFlow.reconciliation_type == "factory_payment")
    if _non_rev_accts:
        flow_stmt = flow_stmt.where(AlipayFlow.account.notin_(_non_rev_accts))
    if period_start:
        flow_stmt = flow_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        flow_stmt = flow_stmt.where(AlipayFlow.transaction_time <= period_end)
    flow_stmt = flow_stmt.group_by(AlipayFlow.counterparty)
    paid_by_factory: dict[str, Decimal] = {}
    factory_has_nonsupplier: set[str] = set()   # 该 canon key 是否有"非供应商"对手方贡献(有则保留对账)
    for name, paid in db.execute(flow_stmt).all():
        k = _canon_factory(name, aliases) if name else "(未匹配)"
        paid_by_factory[k] = paid_by_factory.get(k, Decimal("0")) + Decimal(paid or 0)
        if not _is_supplier_cp(name):
            factory_has_nonsupplier.add(k)

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
    if _non_rev_accts:
        flow_detail = flow_detail.where(AlipayFlow.account.notin_(_non_rev_accts))
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
        if factory in _separate_factories:
            continue   # 博冠等走专用月度对账(#8), 不在通用工厂货款对账报(实付在货款户, 这里永远0)
        billed = billed_by_factory.get(factory, Decimal("0"))
        paid = paid_by_factory.get(factory, Decimal("0"))
        # 无工厂下单(应付0)、付款全来自已登记供应商 → 是配件/物料采购, 走送货单对账, 不在工厂货款里报假差
        if billed == 0 and factory not in factory_has_nonsupplier:
            continue
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
    """推广充值闭环 (15) — 万相台充值是否都有支付宝充值流水号佐证。

    改口径 (2026-06-22): 万相台充值即支付宝出账, CSV 每笔充值都带充值流水号; 真实扣款发生在
    绑定的广告支付宝/银行卡(未导入本主账户), 旧口径找 reconciliation_type='promotion' 的流水
    永远≈0 → 误报「支付宝打款¥0」。改为: 推广充值(全部充值记录) ↔ 有充值流水号佐证的充值,
    缺号(B16 数据质量另扫)才算差。period_start/period_end 限定账期。
    """
    from sqlalchemy import extract, or_

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

    # 改口径: 充值按备注识别(在线充值/自动充值), 排除「现金消耗」(消耗, 备注空+号='现金消耗')与退回。
    def _recharge_by_month(funded_only: bool) -> dict[tuple[int, int], Decimal]:
        st = select(
            extract("year", PromotionFlow.transaction_date).label("y"),
            extract("month", PromotionFlow.transaction_date).label("m"),
            func.coalesce(func.sum(PromotionFlow.amount), 0).label("amt"),
        ).where(or_(PromotionFlow.remark.like("%在线充值%"),
                    PromotionFlow.remark.like("%自动充值%")))
        if funded_only:
            st = st.where(PromotionFlow.alipay_flow_no.isnot(None),
                          PromotionFlow.alipay_flow_no != "",
                          PromotionFlow.alipay_flow_no != "现金消耗")
        if period_start:
            st = st.where(PromotionFlow.transaction_date >= period_start)
        if period_end:
            st = st.where(PromotionFlow.transaction_date <= period_end)
        out: dict[tuple[int, int], Decimal] = {}
        for y, m, amt in db.execute(st.group_by("y", "m")).all():
            if y is not None and m is not None:
                out[(int(y), int(m))] = Decimal(amt or 0)
        return out

    by_month_recharge = _recharge_by_month(funded_only=False)   # 全部万相台充值
    by_month_spend = _pf_by_month("支出")                        # 消耗(供余额勾稽)
    use_recharge = True
    by_month_pf = by_month_recharge
    pf_label = "推广充值"
    by_month_af = _recharge_by_month(funded_only=True)          # 有充值流水号佐证的充值

    # 每月充值流水号 (供核对)
    rc_detail = select(PromotionFlow.transaction_date, PromotionFlow.amount,
                       PromotionFlow.alipay_flow_no).where(
        or_(PromotionFlow.remark.like("%在线充值%"),
            PromotionFlow.remark.like("%自动充值%")))
    if period_start:
        rc_detail = rc_detail.where(PromotionFlow.transaction_date >= period_start)
    if period_end:
        rc_detail = rc_detail.where(PromotionFlow.transaction_date <= period_end)
    flow_by_month: dict[tuple[int, int], list[str]] = {}
    for td, amt, fno in db.execute(rc_detail).all():
        if td is None:
            continue
        flow_by_month.setdefault((td.year, td.month), []).append(
            f"充值 ¥{Decimal(amt or 0)} 流水号{fno or '(缺)'} ({td.strftime('%Y-%m-%d')})")

    diffs: list[ReconciliationDiff] = []
    for key in sorted(set(by_month_pf) | set(by_month_af)):
        y, m = key
        expected = by_month_pf.get(key, Decimal("0"))
        actual = by_month_af.get(key, Decimal("0"))
        diff = actual - expected
        sev = _classify(diff, base=expected)
        period_key = f"{y}-{m:02d}"
        fls = flow_by_month.get(key, [])
        msg = f"{period_key}: {pf_label} ¥{expected}, 有支付宝充值号佐证 ¥{actual}, 差 ¥{diff} ({len(fls)}笔充值)"
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
    if by_month_recharge or by_month_spend:
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


# -------- Rule 4 重做 (2026-06-19): 刷单对账 (补单按日汇总 ↔ 转徐晶晶 b流水/Y 两笔) --------
# 旧「补单赔实付」run_refill_compensation 假设补单是真卖货、有产品本金、实付≈本金 —— 对刷单(假单)
# 完全是错的, 只会产生假异常, 已从 RULES 摘除(保留函数)。补单 = 刷单: 钱按日批量转给中间人徐晶晶,
# 支付宝流水 remark = '{月.日}-b流水'(当日订单额汇总) / '{月.日}-Y'(当日佣金汇总), 两笔分开转。
# 本规则核对: 账上该转(补单按 refill_date 汇总) ↔ 实际转(支付宝转徐晶晶), 订单额/佣金各一条。

_REFILL_PAYEE = "%晶晶%"   # 中间人对手方 LIKE (用户拍板 2026-06-19: 每次都是徐晶晶; 用 LIKE 兼容 sqlite 测试)


def run_refill_transfer(
    db: Session, *,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    record_exceptions: bool = True,
) -> ReconciliationResult:
    """刷单对账: 当日补单 Σ订单额/Σ佣金 ↔ 支付宝转徐晶晶的 b流水/Y 两笔 (按业务日逐日核)。"""
    import re
    from collections import defaultdict

    # 1. 账上该转: 补单(刷单)按 refill_date 汇总 — 订单额 / 佣金
    ref_amt: dict[date, Decimal] = defaultdict(Decimal)
    ref_comm: dict[date, Decimal] = defaultdict(Decimal)
    for d, amt, comm in db.execute(
        select(RefillRecord.refill_date, RefillRecord.order_amount, RefillRecord.commission)
        .where(RefillRecord.refill_date.isnot(None))
    ).all():
        ref_amt[d] += Decimal(amt or 0)
        ref_comm[d] += Decimal(comm or 0)

    # 2. 实际转: 支付宝转徐晶晶(amount<0), remark '{月.日}-{Y佣金 / b流水订单额}' 解析到业务日
    tr_amt: dict[date, Decimal] = defaultdict(Decimal)
    tr_comm: dict[date, Decimal] = defaultdict(Decimal)
    for amt, rk, tt in db.execute(
        select(AlipayFlow.amount, AlipayFlow.remark, AlipayFlow.transaction_time)
        .where(AlipayFlow.counterparty.like(_REFILL_PAYEE), AlipayFlow.amount < 0)
    ).all():
        m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\s*-?\s*(\S+)", rk or "")
        if not m:
            continue
        mo, da, typ = int(m.group(1)), int(m.group(2)), m.group(3)
        yr = tt.year if tt else date.today().year
        if tt and mo > tt.month + 1:    # remark 月份超前于转账月份 → 跨年(上一年)
            yr -= 1
        try:
            bd = date(yr, mo, da)
        except ValueError:
            continue
        val = abs(Decimal(amt or 0))
        if typ.startswith("Y"):
            tr_comm[bd] += val
        elif "b" in typ or "流水" in typ:
            tr_amt[bd] += val

    # 3. 逐业务日对比: 订单额一条 + 佣金一条
    diffs: list[ReconciliationDiff] = []
    for d in sorted(set(ref_amt) | set(ref_comm) | set(tr_amt) | set(tr_comm)):
        if period_start and d < period_start:
            continue
        if period_end and d > period_end:
            continue
        for label, ref, tr in (
            ("订单额", ref_amt.get(d, Decimal(0)), tr_amt.get(d, Decimal(0))),
            ("佣金", ref_comm.get(d, Decimal(0)), tr_comm.get(d, Decimal(0))),
        ):
            key = f"{d}-{label}"
            # 账上有、还没转 → 待转/流水未导, 报"暂无法对比"而非差错 (避免近期未转批量误报)
            if ref > 0 and tr == 0:
                diffs.append(ReconciliationDiff(
                    key=key, expected=ref, actual=None, diff=None, severity="not_available",
                    message=f"刷单 {d} {label}: 账上Σ¥{ref}, 尚无转徐晶晶记录(待转或支付宝流水未导)"))
                continue
            diff = tr - ref
            sev = _classify(diff, base=ref, abs_floor=Decimal("1"), pct=Decimal("0.01"))
            msg = f"刷单 {d} {label}: 账上Σ¥{ref} ↔ 实际转徐晶晶¥{tr}, 差¥{diff}"
            diffs.append(ReconciliationDiff(
                key=key, expected=ref, actual=tr, diff=diff, severity=sev, message=msg))
            if sev not in ("ok", "not_available") and record_exceptions:
                _record_exception(db, rule="refill_transfer", key=key, diff_amount=diff, message=msg)
    if record_exceptions:
        db.flush()
    return _result("refill_transfer", period_start, period_end, diffs, db)


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
        # 应付 = 逐单行(line)相加; 月结汇总行(summary)是"文件名声明总额"仅供互核, 不参与求和
        # (否则 line+summary 双算 → 应付翻倍, 实测 1月 14540 被算成 29080)。某月若只有汇总
        # 行、无逐单明细 → 用汇总兜底(setdefault)。
        _w = []
        if period_start:
            _w.append(LogisticsBill.bill_date >= period_start)
        if period_end:
            _w.append(LogisticsBill.bill_date <= period_end)
        line_rows = db.execute(select(LogisticsBill.bill_date, LogisticsBill.freight_amount)
                               .where(LogisticsBill.row_type == "line", *_w)).all()
        sum_rows = db.execute(select(LogisticsBill.bill_date, LogisticsBill.freight_amount)
                              .where(LogisticsBill.row_type == "summary", *_w)).all()
        billed = _sum_by_month(line_rows)
        for _m, _s in _sum_by_month(sum_rows).items():
            billed.setdefault(_m, _s)   # 仅当该月无逐单时, 用月结汇总兜底
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
        # 未结款 (用户拍板 2026-06-24): 账单已出但还没付款(无支付宝实付记录) → 不算异常,
        # 等结款后有支付宝实付流水再比对。仅 应付>0 且 实付=0 视为待结款; 实付>0 仍正常对账。
        if exp > 0 and act == 0:
            diffs.append(ReconciliationDiff(
                key=key, expected=exp, actual=act, diff=diff, severity="ok",
                message=f"{key}: 应付物流费 ¥{exp} ({source}), 尚未结款(无支付宝实付记录), 待结款后比对",
            ))
            continue
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

# 淘金币豁免阈值: 支付宝该单收入 > 实付 且 正差 ≤ 实付×此比例 → 判为平台淘金币补贴(不报)。
# 淘金币实测占实付 1-9%; 重复流水差≈100% 远超此阈值, 不会被误豁免 (用户拍板 2026-06-21)。
_TAOJINBI_MAX_RATIO = Decimal("0.20")

# 营收对账收入侧只认"真淘宝收款账户"(企业号 / 个体户私账)。
# 爱群号 / 佳宝号 / 主力号 = 货款/采购/对外付款 + 老板个人消费账户:
#   ① 订单号是 1570… 合成号 / 商户号 → 配不到任何真订单 (实测这三户逐单匹配收入=0);
#   ② 金额丢了收/支符号 (全存正数) → 对外付款被当成客户收入;
#   ③ 还有部分双重导入 (爱群号同一笔货款在 17011/88330 两套合成交易号各录一遍、佳宝号 17 组真同号重复)。
# 三者叠加 → 只会污染"配不到订单的收入"月度兜底 (2026-06-23 实测: (无日期)兜底 ¥92.7万 + 2026-01..04 正差
# ¥55.7万 全部来自这三户)。这三户已在总账勾稽 (_LEDGER_FLOW_EXEMPT) 按"流水不完整/不连续"豁免, 营收对账同理排除。
# 注: 排除只针对营收逐单/兜底对账, 不丢任何真实匹配收入; 这三户的现金流/资产仍按金额计 (原始数据本身
# 待支付宝原始账单清洗后再修符号去重, 用户暂无 CSV)。
_NON_REVENUE_ACCOUNTS = ("爱群号", "佳宝号", "主力号")

# 配不到订单的支付宝收入里, 这些"伪订单号"是平台结算/消费券(T200P)/退款(F-refundplatform)/资金(F-capital)/
# 商户结算(HJCAEB==)/某渠道(C-aerith), 非淘宝订单收入 → 不该进营收对账月度兜底 (用户 2026-06-29 全排;
# 实测无纯数字单号 orphan, 真缺单的订单号是纯数字、仍会被正常报出)。
_NON_TAOBAO_SETTLE_PREFIXES = ("T200P", "F-", "C-", "HJCAEB")


def _is_non_taobao_settlement(ron) -> bool:
    if not ron:
        return False
    s = str(ron).strip()
    return ("==" in s) or s.startswith(_NON_TAOBAO_SETTLE_PREFIXES)


# 担保交易结算窗口: 淘宝买家付款先进担保, 确认收货后才放款到店铺支付宝, 比下单晚 1-4 周。
# 故"下单距今 < N 天"的订单, 其支付宝回款本就还没到 —— 月度兜底里不能算它"未配到流水"
# (否则当月/上月永远冒巨额假正差, 实测 2026-06 下单 ¥44万 几乎全在担保中未放款)。
# 永久逻辑: 每次跑都按 date.today() 滚动窗口, 新月份自动不再误报。
_SETTLEMENT_WINDOW_DAYS = 45


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
        select(Order.order_no, Order.order_date, Order.paid_amount,
               Order.buyer_payable_amount, Order.refund_amount,
               Order.buyer_freight, Order.status).where(
            Order.status.notin_(["cancelled", "pending_payment"]),
            Order.order_date.isnot(None),
            *( [Order.order_date >= period_start] if period_start else [] ),
            *( [Order.order_date <= period_end] if period_end else [] ),
        )
    ).all()
    order_paid: dict[str, Decimal] = {}
    order_month: dict[str, str] = {}
    order_date_by_key: dict[str, Optional[date]] = {}   # 结算窗口判断用 (下单距今<45天放款未到)
    order_status_by_key: dict[str, Optional[str]] = {}  # 未签收(paid/shipped)=担保未放款, 不算"未配"
    for no, d, paid, payable, refund, freight, ostatus in o_rows:
        k = _okey(no)
        if not k:
            continue
        # 营收对账口径: 比对基准 = max(买家应付, 实付) + 买家应付邮费 − 退款。
        # 支付宝该单收入正是"买家应付"(平台把买家用的平台券/红包补给店铺, 店铺到账=应付)。
        #   平台券: 应付>实付(实付=扣券净额) → 用应付对平, 不误报正差(平台券是平台出资);
        #   退差价: 应付=实付、退款>0 → 应付−退款=支付宝净额 → 对平;
        #   应付漏抓: 多产品/单品只抓部分子订单 → 应付<实付 而实付=支付宝该单收入 → 用实付兜底
        #     (2026-06-24: 应付漏抓残留单不再误报正差; 实付是对的)。应付缺失同样退回实付。
        #   买家应付邮费 (2026-06-24): 买家额外付的运费=代收, 不进货款/实付列, 但支付宝该单收入含它
        #     → 加进基准, 否则被误报"正差"(分不清是运费还是退款)。运费缺失(None)按 0, 行为不变。
        # 注: 营收/利润口径另用实付(扣券), 不含运费(代收代付对利润中性), 不受此对账口径影响。
        _paid_d = Decimal(paid or 0)
        base = max(Decimal(payable), _paid_d) if payable is not None else _paid_d
        base = base + Decimal(freight or 0)
        order_paid[k] = order_paid.get(k, Decimal("0")) + base - Decimal(refund or 0)
        order_month[k] = _month_key(d) or "(无日期)"
        order_date_by_key[k] = d
        order_status_by_key[k] = ostatus

    from sqlalchemy import or_ as _or
    af_stmt = select(AlipayFlow.transaction_time, AlipayFlow.amount, AlipayFlow.related_order_no,
                     AlipayFlow.transaction_no, AlipayFlow.transaction_type).where(
        AlipayFlow.amount > 0,
        AlipayFlow.related_order_no.isnot(None),
        AlipayFlow.related_order_no != "",                   # 空订单号配不到单, 只会进兜底污染
        AlipayFlow.account.notin_(_NON_REVENUE_ACCOUNTS),    # 货款/采购/个人户(合成号+丢符号+双导)不算营收
        # 用户拍板 (2026-06-11 建议3): 退款回流不算该单收入, 否则退款多的单差额虚高
        _or(AlipayFlow.reconciliation_type.is_(None),
            AlipayFlow.reconciliation_type != "refund_in"),
    )
    if period_start:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time >= period_start)
    if period_end:
        af_stmt = af_stmt.where(AlipayFlow.transaction_time <= period_end)
    flow_income: dict[str, Decimal] = {}        # 订单键 → 收入合计
    flow_nos_by_order: dict[str, list[str]] = {}      # 订单键 → 支付宝流水号(供核对)
    flow_txns_by_order: dict[str, list] = {}          # 订单键 → 交易流水号(查同号重复入库用)
    orphan_income_by_month: dict[str, Decimal] = {}   # 配不到订单的收入按月兜底
    orphan_flow_nos_by_month: dict[str, list[str]] = {}
    # 同一交易号下可能有 ①交易付款(真实收款) ②分账/交易分账(同一笔钱的结算镜像)。收入 = 该号下
    # 真实付款之和 —— 分账不重复计(否则虚高 2 倍, 2026-06-22);而定金+尾款即便共用同一交易号也都是
    # 真实付款, 各自相加(2026-06-29 修 5117408 定金+尾款共号被『取最大』误并少算 ¥2706)。
    # 真实付款按金额去重(同号同额多条=重复入库, 只算一次), 不同额(定金≠尾款)都保留。
    # 若某交易号下只有分账没有付款 → 回退取最大(保旧口径不丢收入)。
    _txn_pay: dict[str, dict] = {}                     # 订单键 → {交易号: {真实付款金额集合}}
    _txn_all: dict[str, dict] = {}                     # 订单键 → {交易号: 该号见过的最大额(回退用)}
    for t, amt, ron, tn, tt in db.execute(af_stmt).all():
        k = _okey(ron)
        amt_d = Decimal(amt or 0)
        if k and k in order_paid:
            _all = _txn_all.setdefault(k, {})
            if tn not in _all or amt_d > _all[tn]:
                _all[tn] = amt_d
            if "分账" not in (tt or ""):                # 非分账 = 真实付款(交易付款/在线支付…)
                _txn_pay.setdefault(k, {}).setdefault(tn, set()).add(amt_d)
            flow_nos_by_order.setdefault(k, []).append(f"支付宝流水 {tn} ¥{amt_d}")
            flow_txns_by_order.setdefault(k, []).append((tn, amt_d))  # 存(交易号,金额)对: 真重复=同号同额
        else:
            if _is_non_taobao_settlement(ron):
                continue   # 伪订单号=平台结算/消费券/退款/商户结算, 非淘宝订单收入 → 不计兜底 (用户 2026-06-29 全排)
            mk = _month_key(t.date() if hasattr(t, "date") else t) or "(无日期)"
            orphan_income_by_month[mk] = orphan_income_by_month.get(mk, Decimal("0")) + amt_d
            orphan_flow_nos_by_month.setdefault(mk, []).append(f"支付宝流水 {tn} ¥{amt_d} (订单{ron})")
    for _k, _all in _txn_all.items():
        _pay = _txn_pay.get(_k, {})
        total = Decimal("0")
        for tn, max_amt in _all.items():
            amts = _pay.get(tn)
            total += sum(amts, Decimal("0")) if amts else max_amt   # 有付款→付款去重相加; 仅分账→取最大
        flow_income[_k] = total

    # 聚合结算账户(微信/聚合)收款走 order_settlements 的『交易收款』(billDetail 导入), 不进 alipay_flows
    # → 营收对账过去只看 alipay_flows, 聚合付款订单假报"未配到流水" (2026-06-29)。把它并入该单收入。
    # 安全: 已验 0 单两表重复 —— 『交易收款』仅聚合源(agent/wechat), 与 source=alipay 的『交易付款』口径不撞。
    _st = select(OrderSettlement.order_no, func.sum(OrderSettlement.income)).where(
        OrderSettlement.entry_type == "交易收款", OrderSettlement.income > 0,
    )
    if period_start:
        _st = _st.where(OrderSettlement.settle_time >= period_start)
    if period_end:
        _st = _st.where(OrderSettlement.settle_time <= period_end)
    for _ron, _inc in db.execute(_st.group_by(OrderSettlement.order_no)).all():
        _k = _okey(_ron)
        if _k and _k in order_paid:
            flow_income[_k] = flow_income.get(_k, Decimal("0")) + Decimal(_inc or 0)
            flow_nos_by_order.setdefault(_k, []).append(f"聚合结算 交易收款 ¥{Decimal(_inc or 0)}")

    if not order_paid and not flow_income and not orphan_income_by_month:
        return _result("revenue_alipay", period_start, period_end, [ReconciliationDiff(
            key="all", expected=None, actual=None, diff=None, severity="not_available",
            message="无订单营收 / 支付宝订单收入可对账 (空数据)",
        )], db)

    diffs: list[ReconciliationDiff] = []
    matched_ok = 0
    unmatched_paid_by_month: dict[str, Decimal] = {}   # 没配到任何流水的订单 → 月兜底
    settling_recent = Decimal("0")                     # 下单<45天、担保放款未到 → 不计兜底(避免假正差)
    settling_cutoff = date.today() - timedelta(days=_SETTLEMENT_WINDOW_DAYS)
    for k, paid in order_paid.items():
        got = flow_income.get(k)
        if got is None:
            if paid > 0:
                _od = order_date_by_key.get(k)
                if (_od is not None and _od >= settling_cutoff) or order_status_by_key.get(k) in ("paid", "shipped"):
                    # 担保未放款不算"未配到流水"(否则当月永远假报):
                    #   ① 下单<45天 结算窗口内; ② 未签收(paid/shipped) — 淘宝确认收货才放款, 家具交付周期长常>45天。
                    settling_recent += paid
                    continue
                mk = order_month.get(k, "(无日期)")
                unmatched_paid_by_month[mk] = unmatched_paid_by_month.get(mk, Decimal("0")) + paid
            continue
        diff = got - paid
        sev = _classify(diff, base=paid, abs_floor=Decimal("50"))
        if sev == "ok":
            matched_ok += 1
            continue
        # 淘金币豁免 (用户拍板 2026-06-21): 支付宝该单收入 > 实付 的"小额正差" = 平台淘金币补贴
        # —— 客户用淘金币抵扣→实付是抵扣后净额, 平台把淘金币打给店铺→支付宝收入 = 实付 + 淘金币。
        # 仅 单条客户付款(无同号重复入库) 且 正差 ≤ 实付×20% 才豁免; "重复流水"(差≈100%、同号付款
        # 重复入库, 去重bug)仍要报, "真短收"(支付宝<实付, 差为负)也仍要报。
        # 真重复入库 = 同交易号"且同金额"出现多次(去重bug); 担保交易正常的"收入+分账"是同号"不同额", 不算
        # (2026-06-23 修: 旧版只看交易号 → 误把正常收入+分账判成重复, 错误关掉了平台券/淘金币正差豁免)。
        _txns = flow_txns_by_order.get(k, [])   # 元素 = (交易号, 金额) 对
        _dup_flow = len(_txns) != len(set(_txns))
        if diff > 0 and not _dup_flow and paid > 0 and diff <= paid * _TAOJINBI_MAX_RATIO:
            matched_ok += 1
            continue
        msg = f"订单 {k}: 订单实付 ¥{paid}, 支付宝该单收入 ¥{got}, 差 ¥{diff}"
        if _dup_flow:
            msg += " (疑同号付款流水重复入库, 非淘金币)"
        diffs.append(ReconciliationDiff(key=k, expected=paid, actual=got, diff=diff,
                                        severity=sev, message=msg,
                                        related_records=[f"订单号 {k}"] + flow_nos_by_order.get(k, [])[:20]))
        if record_exceptions:
            _record_exception(db, rule="revenue_alipay", key=k, diff_amount=diff, message=msg)

    diffs.insert(0, ReconciliationDiff(
        key="逐单配对", expected=None, actual=None, diff=None, severity="ok",
        message=f"逐单配对一致 {matched_ok} 单 (差额≤容差不逐条列出)",
    ))
    if settling_recent > 0:
        diffs.append(ReconciliationDiff(
            key="结算窗口内待放款", expected=settling_recent, actual=None, diff=None, severity="ok",
            message=f"下单<{_SETTLEMENT_WINDOW_DAYS}天、担保交易放款未到的订单实付 ¥{settling_recent} "
                    f"已排除出月度兜底(回款到账后自然配上, 非异常)。",
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
                                        diff=diff, severity=sev, message=msg,
                                        related_records=orphan_flow_nos_by_month.get(mk, [])[:30]))
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
    op_records_by_month: dict[str, list[str]] = {}   # 月 → 该月明细(源记录+流水号), 供核对
    diffs: list[ReconciliationDiff] = []
    for source, d, amt, flow_no, key in records:
        if amt <= 0:
            continue
        month = _month_key(d) or "(无日期)"
        expected[month] = expected.get(month, Decimal("0")) + amt
        op_records_by_month.setdefault(month, []).append(f"[{source}]{key} ¥{amt} 支付宝流水{flow_no}")
        if flow_no in flow_map:
            actual[month] = actual.get(month, Decimal("0")) + flow_map[flow_no]
        else:
            # 有流水号但在支付宝表里找不到: 可能号录错 或 流水未导入
            msg = f"[{source}] {key}: 流水号 {flow_no} 无对应支付宝记录, 请确认号码或补导入流水"
            diffs.append(ReconciliationDiff(
                key=key, expected=amt, actual=None, diff=None, severity="warning", message=msg,
                related_records=[f"[{source}]{key} ¥{amt}", f"支付宝流水号 {flow_no} (查无)"],
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
            related_records=op_records_by_month.get(month, [])[:30],
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
    pp_records_by_month: dict[str, list[str]] = {}   # 月 → 采购单+流水号明细, 供核对
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
            pp_records_by_month.setdefault(month, []).append(
                f"采购单 {p.purchase_no} ¥{amt} 支付宝流水{p.alipay_flow_no}")
        elif "付" in (p.payment_status or "") and not p.alipay_flow_no:
            # 已标记付款但未填流水号 → 独立 warning, 不进月度汇总 (避免双重计数)
            msg = f"采购单 {p.purchase_no}: 已标记付款 ¥{amt} 但未填支付宝流水号"
            diffs.append(ReconciliationDiff(
                key=p.purchase_no, expected=amt, actual=None, diff=None, severity="warning", message=msg,
                related_records=[f"采购单 {p.purchase_no} ¥{amt}", "支付宝流水号: 未填"],
            ))
            if record_exceptions:
                _record_exception(db, rule="purchase_payment", key=p.purchase_no, diff_amount=amt, message=msg)
        elif p.alipay_flow_no and p.alipay_flow_no not in flow_map:
            # 填了流水号但流水找不到 → 独立 warning, 不进月度汇总
            msg = f"采购单 {p.purchase_no}: 流水号 {p.alipay_flow_no} 无对应支付宝记录"
            diffs.append(ReconciliationDiff(
                key=p.purchase_no, expected=amt, actual=None, diff=None, severity="warning", message=msg,
                related_records=[f"采购单 {p.purchase_no} ¥{amt}", f"支付宝流水号 {p.alipay_flow_no} (查无)"],
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
            related_records=pp_records_by_month.get(month, [])[:30],
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


# 补单佣金按当日总和转给徐晶晶(用户拍板 2026-06-23): 支付宝备注 "M.D-Y" = 当日补单佣金实付,
# "M.D-b流水" = 当日货款本金(非佣金, 跳过)。代付台账没登记佣金时, 用徐晶晶支付宝 Y 转账当实付凭据。
# 合并备注(2026-06-23): 多日佣金并一笔转时备注作 "M.D.D-Y"/"M.D-M.D-Y"(如 3.21.22-Y = 3.21+3.22),
# 旧正则只认单日 → 这类整笔漏算、那几天误报"没付"。放宽: 抓首个日期定月份/业务日, 中间允许多日与
# 连字符, 末尾 -Y。b流水(订单额)备注不含 -Y, 自然排除, 不会误并进佣金。
_XJJ_Y_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})[\d.\-]*-Y")


def _xjj_commission_paid_by_month(db: Session, ps: Optional[date], pe: Optional[date]) -> dict[str, Decimal]:
    """徐晶晶支付宝里 备注 'M.D-Y' 的转账 = 当日补单佣金实付, 按补单月(remark 日期)汇总。"""
    out: dict[str, Decimal] = {}
    rows = db.execute(
        select(AlipayFlow.amount, AlipayFlow.remark, AlipayFlow.transaction_time).where(
            AlipayFlow.counterparty.like("%晶晶%"), AlipayFlow.remark.isnot(None))
    ).all()
    for amt, rmk, txn in rows:
        m = _XJJ_Y_RE.match(rmk or "")
        if not m:
            continue
        mon, day = int(m.group(1)), int(m.group(2))
        ty = txn.year if txn else date.today().year
        year = ty - 1 if (txn and mon == 12 and txn.month == 1) else ty   # 跨年(补单12月/付款1月)回退一年
        try:
            d = date(year, mon, day)
        except ValueError:
            continue
        if (ps and d < ps) or (pe and d > pe):
            continue
        key = _month_key(d) or "(无日期)"
        out[key] = out.get(key, Decimal("0")) + abs(Decimal(str(amt or 0)))
    return out


def _run_prepay(db: Session, *, rule: RuleName, category: str,
                billed_by_month: dict[str, Decimal],
                ps: Optional[date], pe: Optional[date], record_exceptions: bool,
                noun: str = "费用", source_hint: str = "业务表",
                annual: bool = False, suppress_zero_paid: bool = False,
                extra_paid_by_month: Optional[dict[str, Decimal]] = None,
                paid_hint: str = "代付台账") -> ReconciliationResult:
    """通用: 应摊(出项, billed_by_month) ↔ 实付(进项: 代付台账 + extra_paid_by_month)。差额=实付-应摊。

    noun/source_hint 用于把差异说明写成人话 (用户反馈"应摊¥300"看不懂)。
    annual=True: 按年聚合比对(年结口径, 不逐月报)。
    suppress_zero_paid=True: 实付还没登记(实付0)时不报差(年结未结); 实付登记后再比对 (用户 2026-06-22)。
    extra_paid_by_month: 代付台账之外的实付凭据 (补单佣金=徐晶晶支付宝Y转账, 用户拍板 2026-06-23)。
    """
    paid = _prepay_income_by_month(db, category, ps, pe)
    if extra_paid_by_month:
        for _k, _v in extra_paid_by_month.items():
            paid[_k] = paid.get(_k, Decimal("0")) + _v
    period_word = "月"
    if annual:
        def _by_year(d: dict[str, Decimal]) -> dict[str, Decimal]:
            out: dict[str, Decimal] = {}
            for k, v in d.items():
                y = (k or "")[:4] or "(无年)"
                out[y] = out.get(y, Decimal("0")) + v
            return out
        billed_by_month = _by_year(billed_by_month)
        paid = _by_year(paid)
        period_word = "年"
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
        if suppress_zero_paid and act == 0:
            sev = "ok"   # 年结口径: 代付台账未实付=未到年结, 不报差 (用户 2026-06-22)
        msg = (f"{key} {period_word}{noun}: {source_hint}里登记该{period_word}共 ¥{exp} (=应摊), "
               f"{paid_hint}里实际付出 ¥{act}, 两边差 ¥{diff}。")
        if act == 0 and exp > 0:
            msg += (" (年结口径: 未到年结/未实付, 暂不报差; 年结实付登记后再比对)"
                    if suppress_zero_paid else
                    f" 实付为 0 通常是该月 {paid_hint} 还没导入, 或这笔钱没走这个渠道。")
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
    """补单佣金: 订单应摊 RefillRecord.commission ↔ 实付 (代付台账 + 徐晶晶支付宝Y转账, 按月)。

    用户拍板 2026-06-23: 补单佣金按当日总和转给徐晶晶, 凭据在支付宝(备注 M.D-Y), 代付台账常为空;
    故把徐晶晶支付宝 Y 转账并入实付源 → 已付的月份(如4月¥2475)自动对平, 漏记的月份显真实差额。
    """
    stmt = select(RefillRecord.refill_date, RefillRecord.commission).where(RefillRecord.commission.isnot(None))
    if period_start:
        stmt = stmt.where(RefillRecord.refill_date >= period_start)
    if period_end:
        stmt = stmt.where(RefillRecord.refill_date <= period_end)
    billed = _sum_by_month(db.execute(stmt).all())
    xjj = _xjj_commission_paid_by_month(db, period_start, period_end)   # 徐晶晶支付宝Y转账 = 实付凭据
    return _run_prepay(db, rule="refill_commission_payout", category="refill_commission",
                       billed_by_month=billed, ps=period_start, pe=period_end, record_exceptions=record_exceptions,
                       noun="补单佣金", source_hint="补单记录(佣金字段)",
                       extra_paid_by_month=xjj, paid_hint="代付台账+徐晶晶支付宝Y转账")


def run_refill_express_payout(db: Session, *, period_start=None, period_end=None,
                              record_exceptions: bool = True) -> ReconciliationResult:
    """补单快递: 订单应摊 RefillRecord.refill_freight ↔ 代付台账 refill_express 实付。
    年结口径 (用户 2026-06-22): 补单运费按年结算, 不逐月报; 代付台账未实付(年结未结)时不报差,
    年结实付登记后按年比对, 对不上才报。"""
    stmt = select(RefillRecord.refill_date, RefillRecord.refill_freight).where(RefillRecord.refill_freight.isnot(None))
    if period_start:
        stmt = stmt.where(RefillRecord.refill_date >= period_start)
    if period_end:
        stmt = stmt.where(RefillRecord.refill_date <= period_end)
    billed = _sum_by_month(db.execute(stmt).all())
    return _run_prepay(db, rule="refill_express_payout", category="refill_express",
                       billed_by_month=billed, ps=period_start, pe=period_end, record_exceptions=record_exceptions,
                       noun="补单快递费", source_hint="补单记录(补单运费字段)",
                       annual=True, suppress_zero_paid=True)


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

    # 退款流出口径: 只认 'refund_out'(退给买家的钱, amt<0)。曾试过放宽认全 refund 家族, 但订单侧
    # refund_date 大面积缺失(应退挤进"无日期"~50万)、支付宝退款按月与订单对不上 → 会误报 9 个月严重差,
    # 故收回。个别错分类的退款(如流水19365 被别名误改)由 route 退款护栏 + 单笔归 refund_out 解决。
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
# 佳宝号 (2026-06-23): 只导了「转账红包」子集(非整账户), 流水不完整, 流水勾稽必假报 → 同样豁免, 只查账面自洽。
_LEDGER_FLOW_EXEMPT = ("爱群号", "佳宝号")


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
    # 上一行(同账户按年月)索引: 推广户流水滚动核对要拿上次快照当基准 (2026-07-10)
    _sorted_rows = sorted(rows, key=lambda x: (x.account_name, x.period_year, x.period_month))
    _prev_map: dict[int, AccountBalance] = {}
    _last_by_acct: dict[str, AccountBalance] = {}
    for _r in _sorted_rows:
        if _r.account_name in _last_by_acct:
            _prev_map[id(_r)] = _last_by_acct[_r.account_name]
        _last_by_acct[_r.account_name] = _r
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
        # ── 推广户治本 (2026-07-10): 月度行的收入/支出从来没人维护(恒0) → 老的"期初+0-0=期末"账面自洽
        # 每月必炸、异常僵尸复发(实测 41698 挂了一周)。推广的真实收支在 promotion_flows(万相台CSV每日
        # 自动导, 充值/扣款齐全) → 改为【流水滚动核对】: 上次快照(as_of) + 窗口充值 - 窗口扣款 ≈ 本次快照。
        # 快照截图 18:00、当天扣款CSV次日才到 → 边界日天然错位, 容差 = max(¥100, 2×窗口日均扣款)。
        # 缺 as_of(旧手填行)定不了窗口 → not_available 跳过, 不硬判。
        if "推广" in (r.account_name or ""):
            from app.models.marketing import PromotionFlow
            _prev = _prev_map.get(id(r))
            _p_asof = getattr(_prev, "as_of_date", None) if _prev is not None else None
            _c_asof = getattr(r, "as_of_date", None)
            _closing = Decimal(r.closing_balance or 0)
            if _prev is None or _p_asof is None or _c_asof is None or _p_asof >= _c_asof:
                diffs.append(ReconciliationDiff(
                    key=key, expected=None, actual=_closing, diff=None,
                    severity="not_available",
                    message=f"{key}: 推广户走流水滚动核对, 但缺上/本次快照统计日期(as_of), 无法定窗口 — 跳过",
                ))
                continue
            _pf = db.execute(select(PromotionFlow).where(
                PromotionFlow.transaction_date > _p_asof,
                PromotionFlow.transaction_date <= _c_asof,
            )).scalars().all()
            _recharge = sum((Decimal(str(p.amount or 0)) for p in _pf if p.flow_type == "充值"), Decimal("0"))
            _spend = sum((Decimal(str(p.amount or 0)) for p in _pf if p.flow_type == "支出"), Decimal("0"))
            _base_bal = Decimal(_prev.closing_balance or 0)
            _expected = _base_bal + _recharge - _spend
            _fdiff = _closing - _expected
            _days = max((_c_asof - _p_asof).days, 1)
            _tol = max(Decimal("100"), _spend / _days * 2)
            if abs(_fdiff) <= _tol:
                diffs.append(ReconciliationDiff(
                    key=key, expected=_expected, actual=_closing, diff=_fdiff, severity="ok",
                    message=(f"{key}: 流水滚动对平 — 上次快照({_p_asof}) ¥{_base_bal} + 充值 ¥{_recharge}"
                             f" - 扣款 ¥{_spend} ≈ 本次快照({_c_asof}) ¥{_closing} (差 ¥{_fdiff}, 边界日容差内)"),
                ))
            else:
                _msg = (f"{key}: 推广流水滚不平 — 上次快照({_p_asof}) ¥{_base_bal} + 充值 ¥{_recharge}"
                        f" - 扣款 ¥{_spend} = ¥{_expected}, 但本次快照({_c_asof})记 ¥{_closing}, 差 ¥{_fdiff}"
                        f" (超容差 ¥{_tol:.0f}: 充值没导/扣款缺天/余额读错)")
                diffs.append(ReconciliationDiff(
                    key=key, expected=_expected, actual=_closing, diff=_fdiff,
                    severity=_classify(_fdiff, base=_spend + _recharge), message=_msg))
                if record_exceptions:
                    _record_exception(db, rule="ledger_check", key=key, diff_amount=_fdiff, message=_msg)
            continue
        # ── 聚合户治本 (2026-07-10, 同推广户思路): 月度行收支恒0 → 老"账面自洽"每月必炸。聚合结算账户
        # (千牛资金页, 只装微信钱)的真实明细在 order_settlements 的微信侧(source=wechat/agent, billDetail
        # 每日自动拉+导, 实测好用) → 快照滚动核对: 上次快照 + 窗口微信净额 ≈ 本次快照(实测 06-29→07-09
        # 净额 2634.00 与余额变动分毫不差)。⚠ 不能算 source=alipay 的行 —— 那是企业号支付宝分账, 钱在
        # 支付宝企业账户, 不在聚合户(混入会虚高 +3.4万)。快照 18:00 截图、边界日晚间结算会错位 →
        # 容差 = max(¥100, 两个边界日净额绝对值)。缺 as_of 定不了窗口 → not_available 跳过。
        if "聚合" in (r.account_name or ""):
            from app.models.settlement import OrderSettlement
            _prev = _prev_map.get(id(r))
            _p_asof = getattr(_prev, "as_of_date", None) if _prev is not None else None
            _c_asof = getattr(r, "as_of_date", None)
            _closing = Decimal(r.closing_balance or 0)
            if _prev is None or _p_asof is None or _c_asof is None or _p_asof >= _c_asof:
                diffs.append(ReconciliationDiff(
                    key=key, expected=None, actual=_closing, diff=None,
                    severity="not_available",
                    message=f"{key}: 聚合户走微信明细滚动核对, 但缺上/本次快照统计日期(as_of), 无法定窗口 — 跳过",
                ))
                continue
            _st = db.execute(select(OrderSettlement).where(
                OrderSettlement.source.in_(("wechat", "agent")))).scalars().all()
            def _net_of(rows_):
                return sum((Decimal(str(s.income or 0)) - Decimal(str(s.expense or 0)) for s in rows_),
                           Decimal("0"))
            _win = [s for s in _st if s.settle_time and _p_asof < s.settle_time.date() <= _c_asof]
            _net = _net_of(_win)
            _base_bal = Decimal(_prev.closing_balance or 0)
            _expected = _base_bal + _net
            _fdiff = _closing - _expected
            _edge = abs(_net_of([s for s in _st if s.settle_time
                                 and s.settle_time.date() in (_p_asof, _c_asof)]))
            _tol = max(Decimal("100"), _edge)
            if abs(_fdiff) <= _tol:
                diffs.append(ReconciliationDiff(
                    key=key, expected=_expected, actual=_closing, diff=_fdiff, severity="ok",
                    message=(f"{key}: 微信明细滚动对平 — 上次快照({_p_asof}) ¥{_base_bal} + 窗口净额 ¥{_net}"
                             f" ≈ 本次快照({_c_asof}) ¥{_closing} (差 ¥{_fdiff}, 边界日容差内)"),
                ))
            else:
                _msg = (f"{key}: 聚合微信明细滚不平 — 上次快照({_p_asof}) ¥{_base_bal} + 窗口净额 ¥{_net}"
                        f" = ¥{_expected}, 但本次快照({_c_asof})记 ¥{_closing}, 差 ¥{_fdiff}"
                        f" (超容差 ¥{_tol:.0f}: billDetail缺段/余额读错/有提现未入明细)")
                diffs.append(ReconciliationDiff(
                    key=key, expected=_expected, actual=_closing, diff=_fdiff,
                    severity=_classify(_fdiff, base=abs(_net) + _base_bal / 100), message=_msg))
                if record_exceptions:
                    _record_exception(db, rule="ledger_check", key=key, diff_amount=_fdiff, message=_msg)
            continue
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
    # install_fee 已彻底关闭 (用户拍板 2026-06-17): 充值制不需万师傅月结对账, 且月结账单本就不导。
    # 保留 run_install_fee 函数与 RuleName 字面量, 仅从 RULES 摘除 → 面板不再出现这张卡。
    "promotion": run_promotion,
    # 「补单赔实付」已废 (用户拍板 2026-06-19): 补单=刷单, 该规则假设有产品本金对刷单是错的, 只产生假异常。
    # 保留 run_refill_compensation 函数与 Literal, 仅从 RULES 摘除 → 改用「刷单对账」run_refill_transfer。
    "refill_transfer": run_refill_transfer,
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


def _autoclose_resolved_diffs(db: Session, results: dict) -> int:
    """对账重算后, 把"差异已消失/已对平"的 open reconciliation_diff 自动销账。

    治本「关了又冒 / 僵尸告警」(2026-06-23): _record_exception 只幂等创建、不会在差异修好后自动关
    (期初/收支/余额录对后旧告警永远挂着)。这里用本轮已算好的 diffs 反向核对: open 异常的
    (rule:key) 不在本轮"仍超阈值"集合里 = 已对平 → 自动 resolved。零额外查询(复用 results)。
    只在全量重算时调用(见 run_all), 避免窄账期重算误关其它月份的异常。被人工 ignored 的不动。
    """
    from datetime import datetime, timezone

    from app.models.exception import DataException
    still_off = {
        f"{rule}:{d.key}"
        for rule, res in results.items()
        for d in res.diffs
        if d.severity not in ("ok", "not_available")
    }
    now = datetime.now(timezone.utc).isoformat()
    closed = 0
    for ex in db.query(DataException).filter(
        DataException.source_table == "reconciliation",
        DataException.exception_type == "reconciliation_diff",
        DataException.status == "open",
    ).all():
        if (ex.source_pk or "") not in still_off:
            ex.status = "resolved"
            ex.resolved_by = ex.resolved_by or "auto"
            ex.resolved_at = ex.resolved_at or now
            ex.description = (ex.description or "") + \
                " | 自动关闭(2026-06-23): 对账重算后该差异已消失/已对平。"
            closed += 1
    if closed:
        db.flush()
    return closed


def run_all(db: Session, **kwargs) -> dict[RuleName, ReconciliationResult]:
    load_thresholds(db)   # 阈值可配 (对账建议 9)
    # 全量重算 = 调用方未限定账期 → 才能安全自动关已对平的旧异常 (窄账期重算只覆盖部分月, 不可关其它月)
    full_rerun = not (kwargs.get("period_start") or kwargs.get("period_end"))
    # 财务起始线: 未显式传账期时默认从 2026-01-01 起 (2025 不导入, 用户拍板)
    if not kwargs.get("period_start"):
        kwargs["period_start"] = _finance_start(db)
    results = {name: fn(db, **kwargs) for name, fn in RULES.items()}
    if kwargs.get("record_exceptions", True):
        # 治本: 全量重算后把差异已消失的 open reconciliation_diff 自动销账 (修好自愈, 不再僵尸/关了又冒)
        if full_rerun:
            _autoclose_resolved_diffs(db, results)
        # 异常池同步时间 (对账建议 9/审计): 记录最近一次"写异常"的对账时间
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
