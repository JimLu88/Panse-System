"""数据完整性扫描 (Phase 13) — B1–B11 全部规则.

每条规则写入 DataException 异常池 (复用 exception_service.record).
所有扫描器幂等: 同 source_table+source_pk+type 不重复堆积.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models.order import FactoryOrder, Order, PartPurchase
from app.models.finance import AlipayFlow, RefillRecord, FactoryReconciliation
from app.models.marketing import OutsourcingExpense, AfterSales, PromotionFlow, Sample, WoodLoss
from app.services import exception_service, settings_service

# 非产品订单关键词: 安装/送货/补差价/样品/专链/官方服务 这类单从不走正常收款流水, 不该报「缺收款凭据」
_NON_PRODUCT_KW = ("安装", "送货", "入户", "补差价", "差价", "样品", "样块",
                   "专链", "补拍", "邮费", "官方服务", "上门服务")

_log = logging.getLogger("panse.data_quality")


def _record(db: Session, **kwargs: Any) -> None:
    """幂等写: 若同 source_table+source_pk+type+status=open 已存在则跳过."""
    from app.models.exception import DataException
    existing = db.query(DataException).filter_by(
        source_table=kwargs.get("source_table"),
        source_pk=str(kwargs.get("source_pk")),
        exception_type=kwargs.get("exception_type"),
        status="open",
    ).first()
    if existing:
        return
    exception_service.record(db, **kwargs)


# 非实物链接(差价/邮费/补拍/样品等): 无产品成本, 不该报"缺成本"; 关键词可按需扩充。
NON_PRODUCT_COST_KEYWORDS = ("差价", "邮费", "补拍", "专链", "样品", "小样", "样块")


def is_non_product_order(o) -> bool:
    """差价/邮费/补拍/样品 等非实物订单 — 无产品成本, 不计入"缺成本"异常。"""
    name = (getattr(o, "product_name", None) or "")
    return any(k in name for k in NON_PRODUCT_COST_KEYWORDS)


# 非卖品产品(作废链接/纯定制/全屋定制/商家安装/送货sku/淘宝自动生成sku/样品链接): 本就没上架,
# 不该报"缺淘宝映射"。用产品名关键词识别(在售真品名里不会出现这些词)。
NON_SELLABLE_PRODUCT_KEYWORDS = ("作废", "定制", "安装", "送货", "自动生成", "样品", "小样")


def is_non_sellable_product(p) -> bool:
    """非卖品/占位/作废 产品 — 不报缺淘宝映射 (名字含 作废/定制/安装/送货/自动生成/样 等)。"""
    name = (getattr(p, "name", None) or "")
    return any(k in name for k in NON_SELLABLE_PRODUCT_KEYWORDS)


def is_custom_order(o) -> bool:
    """定制单: is_custom 标记 / 「改」后缀 / 数字尾号≥90(99/98…)。定制单缺成本由
    custom_order_missing_cost_basis 单独管(提示补定制加价), 不在通用缺成本里重复报。"""
    from app.services import sku_utils
    return bool(getattr(o, "is_custom", False)) or sku_utils.is_custom_sku_code(
        getattr(o, "sku_code", None), getattr(o, "product_code", None))


# ---------------------------------------------------------------------------
# B1 — 订单缺理论/实际成本
# ---------------------------------------------------------------------------

def scan_order_missing_cost(db: Session) -> int:
    count = 0
    for o in db.query(Order).filter(
        Order.status.notin_(["cancelled", "pending_payment"]),  # 取消/未付款无成本核算需求(用户拍板2026-06-15)
        Order.is_historical == False,  # noqa: E712
    ).all():
        if o.theoretical_cost is None and o.actual_cost is None:
            if is_non_product_order(o) or is_custom_order(o):
                continue  # 非实物(差价/样品)无成本; 定制单(改/尾号≥90)归 custom_order_missing_cost_basis
            _record(
                db,
                source_table="orders",
                source_pk=o.id,
                exception_type="order_missing_cost",
                severity="warning",
                description=f"订单 {o.order_no} 理论成本与实际成本均未填写, 影响毛利核算。",
                suggestion_action="请补填理论成本或实际成本。",
                context={"order_no": o.order_no, "status": o.status},
            )
            count += 1
    _log.info("scan_order_missing_cost: %d orders missing cost", count)
    return count


# ---------------------------------------------------------------------------
# B2 — 订单缺支付宝流水号匹配
# ---------------------------------------------------------------------------

def scan_order_missing_alipay(db: Session) -> int:
    # 已收款判定 (2026-06-15 根因修, 据用户提示"淘宝聚合可逐单拉"): 淘宝企业订单走『聚合结算』
    # 批量打款 —— 单笔支付宝流水是多单合并的批次, 无法逐单匹配; 逐单货款只落在『聚合账单』里
    # (OrderSettlement.order_no, 由 settlement_import 导入)。故"已收款"= 支付宝流水
    # (AlipayFlow.related_order_no) 或 聚合结算(OrderSettlement.order_no) 任一有记录。
    from app.models.settlement import OrderSettlement
    from app.services.smart_matching_service import _order_key as _okey
    # 归一 (2026-06-22): 支付宝 related_order_no 常带 T200P 前缀/空格, 原值精确比对会漏匹配
    # (实测 T200P3232689… 收款¥5512 误报"无收款") → 用 _okey 抽核心订单号, 与 revenue_alipay 口径一致。
    linked = {
        _okey(r) for (r,) in db.query(AlipayFlow.related_order_no)
        .filter(AlipayFlow.related_order_no.isnot(None)).all() if _okey(r)
    }
    linked |= {
        _okey(r) for (r,) in db.query(OrderSettlement.order_no)
        .filter(OrderSettlement.order_no.isnot(None)).all() if _okey(r)
    }
    # 只查『货款应已到账』的订单 —— 交易成功(signed)/已完成。担保交易中的 paid(买家已付款待发货)/
    # shipped(已发货待确认收货) 货款仍在淘宝担保未释放, 本就无收款流水, 不算缺失(在途, 归现金流预测);
    # aftersales(退款)/cancelled/pending_payment 同理不该要收款流水。否则把"在途"误报成"缺数据"。
    SETTLED_STATES = ("signed", "completed", "success", "finished")
    count = 0
    for o in db.query(Order).filter(
        Order.status.in_(SETTLED_STATES),
        Order.is_historical == False,  # noqa: E712
    ).all():
        if _okey(o.order_no) in linked:
            continue
        # 已退款单(退款≥实付)是退款交易, 货款已退, 不该要求收款凭据 (2026-06-22)
        if o.refund_amount and o.paid_amount and o.refund_amount >= o.paid_amount:
            continue
        # 淘宝企业单走批量结算, 逐单支付宝流水拿不到; 但淘宝订单报表已逐单给出『打款商家金额』
        # (淘宝实际打给卖家的货款)。有该金额 = 淘宝已逐单确认放款 → 视为已收款, 不逐单误报;
        # "钱是否真到账"由月度货款对账(Σ打款商家金额 vs 支付宝/聚合实际到账, 按天可下钻定位)兜底。
        # (2026-06-15 用户拍板: 交易成功+有打款金额=货款到账)
        if o.shop_received_amount and o.shop_received_amount > 0:
            continue
        # 非产品订单(安装/送货/补差价/官方服务)从不走收款流水, 不该当缺收款异常 (2026-06-17)
        _txt = f"{o.product_name or ''} {o.sku or ''} {o.sku_code or ''}"
        if any(k in _txt for k in _NON_PRODUCT_KW):
            continue
        # 宽限期: 下单 14 天内货款多在结算途中(淘宝 T+7~14), 暂不报缺收款 (避免新单噪声)
        if o.order_date and o.order_date >= date.today() - timedelta(days=14):
            continue
        # 已成交 + 无流水 + 无聚合 + 连淘宝打款金额都没有 + 非新单/非服务单 → 真·缺收款凭据
        _record(
            db,
            source_table="orders",
            source_pk=o.id,
            exception_type="order_missing_alipay",
            severity="warning",
            description=f"订单 {o.order_no} 已成交却无任何收款凭据(支付宝流水/聚合结算/淘宝打款金额均无), 需人工核实货款。",
            suggestion_action="确认该单货款是否到账; 或导入覆盖该单的支付宝流水/聚合账单后自动消除。",
            context={"order_no": o.order_no, "paid_amount": str(o.paid_amount)},
        )
        count += 1
    _log.info("scan_order_missing_alipay: %d (settled, no flow/settlement/淘宝打款金额)", count)
    return count


def scan_cost_exceeds_paid(db: Session) -> int:
    """错配单: 实付明显小于成本(背了整份SKU成本)的正式订单 → 异常, 供人工查原因。

    用户拍板 2026-06-17: 那批"实付几十却背一整套餐边柜成本"的单, 关键词抓不到的(补差价/部分付款
    /错配)列入异常逐单看。口径: 正式销售(非补单/非取消), 成本(actual或theoretical) > 实付×1.5
    且超额 ≥¥300; 非产品单(安装/送货/差价/样品 —— 应已归0)除外。
    """
    from decimal import Decimal
    SALE = ("paid", "shipped", "signed", "completed", "success", "finished")
    count = 0
    for o in db.query(Order).filter(
        Order.status.in_(SALE), Order.is_refill == False,  # noqa: E712
    ).all():
        paid = Decimal(str(o.paid_amount or 0))
        cost = Decimal(str(o.actual_cost if o.actual_cost is not None else (o.theoretical_cost or 0)))
        if paid <= 0 or cost <= 0:
            continue
        if cost <= paid * Decimal("1.5") or (cost - paid) < Decimal("300"):
            continue
        txt = f"{o.product_name or ''} {o.sku or ''} {o.sku_code or ''}"
        if any(k in txt for k in _NON_PRODUCT_KW):
            continue  # 这类应已归0; 双保险
        _record(
            db, source_table="orders", source_pk=o.id,
            exception_type="cost_exceeds_paid", severity="warning",
            description=(f"订单 {o.order_no} 实付 ¥{paid} 却背成本 ¥{cost} (差 ¥{cost - paid}), "
                        f"疑补差价/部分付款单错配整份 SKU 成本, 拖累利润, 请核实。"),
            suggestion_action="核实是否补差价/部分付款: 是则成本归原单, 本单成本应清(改 actual_cost=0 或并单)。",
            context={"order_no": o.order_no, "paid": str(paid), "cost": str(cost),
                     "product_name": o.product_name},
        )
        count += 1
    _log.info("scan_cost_exceeds_paid: %d", count)
    return count


def order_payment_diagnosis(db: Session, order_nos: list[str]) -> list[dict]:
    """诊断「订单缺支付宝收款流水」(只读): 对给定订单号, 查它现在到底有没有收款凭据
    (支付宝流水关联 / 聚合结算 / 淘宝打款金额), 以及现在是否还该被标异常。

    用户拍板 2026-06-17: 6/2-6/3 的几单当时可能流水还没到, 现在导入后应已正常 → 查清+清掉。
    """
    from sqlalchemy import select
    from app.models.settlement import OrderSettlement
    out: list[dict] = []
    for ono in order_nos:
        ono = (ono or "").strip()
        o = db.execute(select(Order).where(Order.order_no == ono)).scalar_one_or_none()
        if o is None:
            out.append({"order_no": ono, "found": False})
            continue
        af = db.execute(select(AlipayFlow.transaction_no, AlipayFlow.amount).where(
            AlipayFlow.related_order_no.like(f"%{ono}%"))).all()   # 归一 T200P 前缀
        st = db.execute(select(OrderSettlement).where(OrderSettlement.order_no == ono)).scalars().all()
        st_income = float(sum((s.income or 0) for s in st))
        shop = o.shop_received_amount
        is_refunded = bool(o.refund_amount and o.paid_amount and o.refund_amount >= o.paid_amount)
        has_evidence = bool(af) or bool(st) or (shop is not None and shop > 0)
        _txt = f"{o.product_name or ''} {o.sku or ''} {o.sku_code or ''}"
        is_non_product = any(k in _txt for k in _NON_PRODUCT_KW)
        recent = bool(o.order_date and o.order_date >= date.today() - timedelta(days=14))
        if has_evidence:
            verdict = "已有收款凭据 → 异常应清除(过期)"
        elif is_refunded:
            verdict = "已退款单(退款≥实付)→ 货款已退, 不该要求收款凭据(已改为跳过)"
        elif is_non_product:
            verdict = "非产品单(安装/送货/补差价)→ 本就无收款流水, 不该报异常(已改为跳过)"
        elif recent:
            verdict = "下单14天内新单, 货款多在结算途中 → 暂不报(已加宽限期)"
        else:
            verdict = "真·缺收款凭据(请导入覆盖该单的支付宝/聚合账单)"
        out.append({
            "order_no": ono, "found": True, "status": o.status,
            "order_date": o.order_date.isoformat() if o.order_date else None,
            "product_name": o.product_name, "paid_amount": float(o.paid_amount or 0),
            "shop_received_amount": (float(shop) if shop is not None else None),
            "alipay_flow_linked": [t for t, _a in af][:5],
            "settlement_rows": len(st), "settlement_income": st_income,
            "is_non_product": is_non_product, "is_recent": recent,
            "has_payment_evidence_now": has_evidence,
            "verdict": verdict,
        })
    return out


# ---------------------------------------------------------------------------
# B3 — 导入陈旧提醒 (> N 天无新订单)
# ---------------------------------------------------------------------------

def scan_stale_import(db: Session) -> int:
    from sqlalchemy import func
    threshold = int(settings_service.get(db, "stale_import_days") or "7")
    latest = db.query(func.max(Order.order_date)).scalar()
    if latest is None:
        return 0
    if isinstance(latest, str):
        latest = date.fromisoformat(latest)
    days_stale = (date.today() - latest).days
    if days_stale > threshold:
        _record(
            db,
            source_table="orders",
            source_pk="stale_import",
            exception_type="stale_import",
            severity="error",
            description=f"最新订单日期 {latest}, 距今 {days_stale} 天 (阈值 {threshold} 天), 可能有订单未导入。",
            suggestion_action="请在导入页面上传最新订单 Excel。",
            context={"latest_order_date": str(latest), "days_stale": days_stale},
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# B4 — 已发货订单缺物流单号
# ---------------------------------------------------------------------------

def scan_order_missing_tracking(db: Session) -> int:
    count = 0
    for o in db.query(Order).filter(
        Order.status.in_(["shipped", "signed"]),
        Order.tracking_no.is_(None),
    ).all():
        _record(
            db,
            source_table="orders",
            source_pk=o.id,
            exception_type="order_missing_tracking",
            severity="warning",
            description=f"订单 {o.order_no} 状态为 {o.status}, 但物流单号为空。",
            suggestion_action="请补填承运商和物流单号。",
            context={"order_no": o.order_no, "status": o.status},
        )
        count += 1
    _log.info("scan_order_missing_tracking: %d", count)
    return count


# ---------------------------------------------------------------------------
# B7 — 补单记录: 订单号在主订单表中找不到 → 报异常
# ---------------------------------------------------------------------------

def scan_refill_unmatched(db: Session) -> int:
    known_orders = {o.order_no for o in db.query(Order.order_no).all()}
    count = 0
    for r in db.query(RefillRecord).all():
        # 2025 及以前的补单不纳入(系统从 2026 起算, 旧单订单本就不在系统, 2026-06-17)
        if r.refill_date is not None and r.refill_date < date(2026, 1, 1):
            continue
        if r.order_no not in known_orders:
            _record(
                db,
                source_table="refill_records",
                source_pk=r.id,
                exception_type="refill_unmatched",
                severity="warning",
                description=f"补单记录 {r.id} 的订单号 '{r.order_no}' 在订单总表中找不到。",
                suggestion_action="请确认补单记录的订单号是否正确。",
                context={"refill_id": r.id, "order_no": r.order_no},
            )
            count += 1
    _log.info("scan_refill_unmatched: %d", count)
    return count


# ---------------------------------------------------------------------------
# B8 — 支付宝流水号空值 (空字符串)
# ---------------------------------------------------------------------------

def scan_alipay_missing_txn(db: Session) -> int:
    count = 0
    for row in db.query(AlipayFlow).filter(
        (AlipayFlow.transaction_no == "") | (AlipayFlow.transaction_no == "null")
    ).all():
        _record(
            db,
            source_table="alipay_flows",
            source_pk=row.id,
            exception_type="alipay_missing_txn",
            severity="warning",
            description=f"支付宝流水 (账户: {row.account}, 时间: {row.transaction_time}) 流水号为空, 无法去重/对账。",
            suggestion_action="请在支付宝后台找到该笔流水, 补填交易流水号。",
            context={"account": row.account, "amount": str(row.amount), "transaction_time": str(row.transaction_time)},
        )
        count += 1
    _log.info("scan_alipay_missing_txn: %d", count)
    return count


# ---------------------------------------------------------------------------
# B9 — 工厂对账单缺字段
# ---------------------------------------------------------------------------

def scan_factory_recon_incomplete(db: Session) -> int:
    count = 0
    for r in db.query(FactoryReconciliation).all():
        missing = []
        if r.bill_amount is None:
            missing.append("bill_amount (工厂账单金额)")
        # paid_amount/alipay_flow_no 不再单独报异常 (2026-06-17 用户拍板): 应付↔实付的对账已由
        # 「货款对账」(工厂应付 vs 支付宝 factory_payment) 整体覆盖, 这里逐行缺实付/流水号是噪声。
        if missing:
            _record(
                db,
                source_table="factory_reconciliations",
                source_pk=r.id,
                exception_type="factory_recon_incomplete",
                severity="warning",
                description=f"工厂对账 [{r.factory_name}] 缺少: {', '.join(missing)}。",
                suggestion_action="请补填工厂对账单对应字段。",
                context={"id": r.id, "factory_name": r.factory_name, "missing_fields": missing},
            )
            count += 1
    _log.info("scan_factory_recon_incomplete: %d", count)
    return count


# ---------------------------------------------------------------------------
# B10 — 人员外包费用缺流水号/支付日期
# ---------------------------------------------------------------------------

def scan_outsourcing_missing(db: Session) -> int:
    count = 0
    for r in db.query(OutsourcingExpense).all():
        missing = []
        if not r.alipay_flow_no:
            missing.append("alipay_flow_no")
        if not r.payment_date:
            missing.append("payment_date")
        if missing:
            _record(
                db,
                source_table="outsourcing_expenses",
                source_pk=r.id,
                exception_type="outsourcing_missing",
                severity="warning",
                description=f"人员外包费用 [{r.payee}] 缺少: {', '.join(missing)}。",
                suggestion_action="请补填外包费用的支付宝流水号和支付日期。",
                context={"id": r.id, "payee": r.payee, "missing_fields": missing},
            )
            count += 1
    _log.info("scan_outsourcing_missing: %d", count)
    return count


# ---------------------------------------------------------------------------
# B11 — 售后表为空
# ---------------------------------------------------------------------------

def scan_aftersales_empty(db: Session) -> int:
    total = db.query(AfterSales).count()
    if total == 0:
        _record(
            db,
            source_table="after_sales",
            source_pk="empty",
            exception_type="aftersales_empty",
            severity="info",
            description="售后表当前无任何记录, 如有历史售后单请及时录入。",
            suggestion_action="在售后/退货页面录入或导入历史售后数据。",
            context={"total": 0},
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# B12 — 支付宝余额连续性 (账户内 balance 跳变 → 流水可能缺失)
# ---------------------------------------------------------------------------

from decimal import Decimal as _Decimal
# 余额断链容差: ≤此值视为链条连续(吸收支付宝收款手续费的几分钱配对噪声, 真漏一笔至少几元)
_BALANCE_GAP_TOL = _Decimal("0.5")


def scan_alipay_balance_gap(db: Session) -> int:
    """账户余额连续性核查 —— 订单无关、与流水排序无关 (2026-06-15 根因修)。

    旧法按 (时间,id) 相邻两条比 balance[i]≈balance[i-1]+amount[i], 但企业号常有"同一秒多笔
    收款", id 顺序≠真实账务顺序 → 大量误报(实测57条几乎全是同秒乱序, 流水其实一条不缺)。
    新法: 每条流水的"前驱余额"= 本余额 - 本金额; 若该值正好是本账户里某条流水的余额(说明确有
    一条流水停在那, 链条接得上), 或本条就是窗口最早一条, 则连续。只有"前驱余额"对不上任何
    流水 = 真有一笔流水缺失。这样同秒/乱序不再误报, 只抓真缺口。
    """
    from decimal import Decimal
    count = 0
    # 用户拍板 (2026-06-11): 爱群号曾混用私人支出, 流水天然不连续 → 永久豁免不报
    EXEMPT_KW = ("爱群",)
    accounts = [a[0] for a in db.query(AlipayFlow.account).distinct().all()]
    for account in accounts:
        if account and any(k in account for k in EXEMPT_KW):
            continue
        rows = (
            db.query(AlipayFlow)
            .filter(AlipayFlow.account == account, AlipayFlow.balance.isnot(None))
            .order_by(AlipayFlow.transaction_time.asc(), AlipayFlow.id.asc())
            .all()
        )
        if len(rows) < 2:
            continue  # 余额全为 NULL 或只有1条, 无法连续性核查, 跳过 (不误报)
        bal_set = {r.balance for r in rows if r.balance is not None}
        earliest_id = min(rows, key=lambda r: (r.transaction_time, r.id)).id
        for i, r in enumerate(rows, 1):  # i = 该账户按时间的第几条
            pred = (r.balance or Decimal("0")) - (r.amount or Decimal("0"))
            if pred in bal_set or r.id == earliest_id:
                continue  # 前驱余额能对上某条流水(或本条是窗口起点) → 链条完整
            # 容差 (2026-06-17 实测企业号6条全是 ¥0.11~0.15 差): 支付宝收款每笔带 ~¥0.1 手续费,
            # 收款行与手续费行的余额配对会让链条差几分钱 → ≤¥0.5 视为连续。真漏一笔至少几元,
            # 这样只抓材料级缺口, 不再被 1毛钱手续费噪声误报成"漏导一笔流水"。
            if any(abs(pred - b) <= _BALANCE_GAP_TOL for b in bal_set):
                continue
            mon = r.transaction_time.strftime("%Y-%m-%d %H:%M") if r.transaction_time else "?"
            _record(
                db,
                source_table="alipay_flows",
                source_pk=r.id,
                exception_type="alipay_balance_gap",
                severity="warning",
                description=(
                    f"支付宝[{account}] {mon} 第{i}条 (流水号…{(r.transaction_no or '')[-8:]}) 余额断链: "
                    f"本余额 ¥{r.balance} − 本金额 ¥{r.amount} = 前驱应为 ¥{pred}, 但没有任何流水停在该余额"
                    f" → 该账户该时段疑似漏导一笔流水。"
                ),
                suggestion_action="检查该账户该时段是否有遗漏流水未导入。",
                context={"account": account,
                         "month": (r.transaction_time.strftime("%Y-%m")
                                   if r.transaction_time else None),
                         "seq": i, "expected_prev_balance": str(pred),
                         "balance": str(r.balance), "txn_no": r.transaction_no},
            )
            count += 1
    _log.info("scan_alipay_balance_gap: %d gaps (predecessor-balance method)", count)
    return count


def balance_gap_details(db: Session, *, account_kw: str = "企业号", window: int = 4) -> list[dict]:
    """诊断支付宝余额断链 (只读): 对每条断链流水, 列出本账户按时间相邻的前后几条流水
    (含 balance/amount/是否空余额), 让人看清到底是真漏了一笔, 还是前驱流水余额为空导致的假断链。

    用户拍板 2026-06-17: 企业号流水由系统自动导出, 理论上不该缺 → 先查清这 6 条是真缺还是误报。
    """
    from decimal import Decimal
    out: list[dict] = []
    accounts = [a[0] for a in db.query(AlipayFlow.account).distinct().all()
                if a[0] and account_kw in a[0]]
    for account in accounts:
        rows = (db.query(AlipayFlow)
                .filter(AlipayFlow.account == account)
                .order_by(AlipayFlow.transaction_time.asc(), AlipayFlow.id.asc()).all())
        bal_set = {r.balance for r in rows if r.balance is not None}
        balrows = [r for r in rows if r.balance is not None]
        if not balrows:
            continue
        earliest_id = min(balrows, key=lambda r: (r.transaction_time, r.id)).id
        for idx, r in enumerate(rows):
            if r.balance is None:
                continue
            pred = (r.balance or Decimal("0")) - (r.amount or Decimal("0"))
            if pred in bal_set or r.id == earliest_id:
                continue
            if any(abs(pred - b) <= _BALANCE_GAP_TOL for b in bal_set):
                continue  # 几分钱手续费噪声, 与 scanner 同口径不算断链
            lo, hi = max(0, idx - window), min(len(rows), idx + 2)
            null_before = any(rows[j].balance is None for j in range(lo, idx))
            out.append({
                "account": account,
                "gap_txn_no": r.transaction_no,
                "time": r.transaction_time.isoformat() if r.transaction_time else None,
                "balance": float(r.balance),
                "amount": float(r.amount or 0),
                "expected_prev_balance": float(pred),
                "null_balance_flow_nearby": null_before,
                "likely_cause": ("前驱流水余额为空(假断链, 非真漏)" if null_before
                                 else "前后流水均有余额 → 疑似真漏一笔"),
                "context_flows": [{
                    "txn_no": x.transaction_no,
                    "time": x.transaction_time.isoformat() if x.transaction_time else None,
                    "type": x.transaction_type,
                    "amount": float(x.amount or 0),
                    "balance": (float(x.balance) if x.balance is not None else None),
                    "null_balance": x.balance is None,
                    "is_gap_row": x.id == r.id,
                } for x in rows[lo:hi]],
            })
    return out


# ---------------------------------------------------------------------------
# B13 — 木材损耗率异常偏高
# ---------------------------------------------------------------------------

def scan_wood_loss_high(db: Session) -> int:
    """木材损耗率 > 15% 报 error, 10%~15% 报 warning, 提示异常损耗/材料浪费。"""
    from decimal import Decimal
    threshold = Decimal(settings_service.get(db, "wood_loss_warn_pct") or "15")
    count = 0
    for r in db.query(WoodLoss).filter(WoodLoss.loss_rate_pct.isnot(None)).all():
        rate = Decimal(r.loss_rate_pct or 0)
        if rate <= threshold * Decimal("0.67"):
            continue
        sev = "error" if rate > threshold else "warning"
        _record(
            db,
            source_table="wood_losses",
            source_pk=r.id,
            exception_type="wood_loss_high",
            severity=sev,
            description=f"木材损耗 [{r.wood_type or '?'} {r.spec or ''}] 损耗率 {rate}% 偏高 (阈值 {threshold}%)。",
            suggestion_action="复核下料工艺/材料质量, 必要时调整 BOM 损耗系数。",
            context={"id": r.id, "loss_rate_pct": str(rate), "wood_type": r.wood_type},
        )
        count += 1
    _log.info("scan_wood_loss_high: %d", count)
    return count


# ---------------------------------------------------------------------------
# B14 — 样品缺成本 / 报损未记成本
# ---------------------------------------------------------------------------

def scan_sample_missing_cost(db: Session) -> int:
    """样品 cost 为空 → 影响样品资产/费用核算; 报损样品更需记成本。"""
    count = 0
    for r in db.query(Sample).filter(Sample.cost.is_(None)).all():
        is_scrap = (r.status or "") in ("报损", "报废")
        _record(
            db,
            source_table="samples",
            source_pk=r.id,
            exception_type="sample_missing_cost",
            severity="warning" if is_scrap else "info",
            description=f"样品 {r.sample_no} ({r.product_name or '?'}) 未填成本"
                        + ("，且已报损/报废，需计入费用。" if is_scrap else "。"),
            suggestion_action="补填样品制作成本。",
            context={"id": r.id, "sample_no": r.sample_no, "status": r.status},
        )
        count += 1
    _log.info("scan_sample_missing_cost: %d", count)
    return count


# ---------------------------------------------------------------------------
# B15 — 已发货订单无对应工厂单 (工厂单覆盖率)
# ---------------------------------------------------------------------------

def scan_factory_order_uncovered(db: Session) -> int:
    """状态为 shipped/signed 且有 actual_cost/theoretical_cost 的订单,
    若无任何有效 (未作废) 工厂单 → 报 info 级提示。

    仅对有成本的订单报告 (成本已录入说明有采购行为); 无成本的订单可能是库存直发或
    赠品, 不写异常, 避免误报。severity=info 确保不拉低健康分。

    用户拍板 2026-06-15: 系统本就自动生成工厂单、无手工录入步骤; 不在工厂下单表里的订单
    一律视为库存直发/无需工厂单 → 此异常默认**关闭**(仅 settings factory_order_uncovered_check=1 才扫)。
    """
    try:
        from app.services import settings_service as _ss
        _on = str(_ss.get(db, "factory_order_uncovered_check", env_fallback=False) or "").strip().lower() \
            in ("1", "true", "yes", "on")
    except Exception:
        _on = False
    if not _on:
        _log.info("scan_factory_order_uncovered: 默认关闭, skip")
        return 0
    covered = {
        no for (no,) in db.query(FactoryOrder.platform_order_no)
        .filter(FactoryOrder.platform_order_no.isnot(None), FactoryOrder.voided_at.is_(None))
        .distinct().all()
    }
    count = 0
    for o in db.query(Order).filter(
        Order.status.in_(["shipped", "signed"]),
        Order.is_historical == False,  # noqa: E712
        Order.is_refill == False,      # noqa: E712  # 补单是补发/重发, 不需新工厂下单 (用户拍板 2026-06-15)
        # 只报有成本记录的订单; 无成本大概率库存直发
        (Order.theoretical_cost.isnot(None)) | (Order.actual_cost.isnot(None)),
    ).all():
        if o.order_no not in covered:
            _record(
                db,
                source_table="orders",
                source_pk=o.id,
                exception_type="factory_order_uncovered",
                severity="info",
                description=f"订单 {o.order_no} 已{o.status}, 有成本但无工厂下单记录。"
                            "如为库存直发可忽略; 如为工厂生产请补录工厂单。",
                suggestion_action="确认是否库存直发; 如非库存直发请在工厂下单页补录。",
                context={"order_no": o.order_no, "status": o.status},
            )
            count += 1
    _log.info("scan_factory_order_uncovered: %d", count)
    return count


# ---------------------------------------------------------------------------
# B16 — 推广充值记录缺支付宝流水号
# ---------------------------------------------------------------------------

def scan_promotion_recharge_unmatched(db: Session) -> int:
    """推广 '充值' 记录缺 alipay_flow_no → 先按金额+日期窗口自动配支付宝推广支出流水(自动填号),
    真配不上才报异常 (2026-06-17 用户: 支付宝企业号是系统自动拉的, 该能对上)。"""
    from decimal import Decimal as _D
    from datetime import timedelta as _td
    # 支付宝里推广类(或尚未归类)的支出流水 (amount<0) + 已被其它充值占用的流水号
    promo_flows = db.query(AlipayFlow).filter(
        (AlipayFlow.reconciliation_type == "promotion")
        | (AlipayFlow.reconciliation_type.is_(None)),
        AlipayFlow.amount < 0, AlipayFlow.transaction_time.isnot(None)).all()
    used = {n for (n,) in db.query(PromotionFlow.alipay_flow_no).filter(
        PromotionFlow.alipay_flow_no.isnot(None), PromotionFlow.alipay_flow_no != "").all()}
    count = 0
    for r in db.query(PromotionFlow).filter(
        PromotionFlow.flow_type == "充值",
        (PromotionFlow.alipay_flow_no.is_(None)) | (PromotionFlow.alipay_flow_no == ""),
    ).all():
        # 自动配: 同金额、日期 ±10 天内、未被占用的支付宝推广支出
        amt = _D(str(r.amount or 0))
        hit = None
        for pf in promo_flows:
            if pf.transaction_no in used:
                continue
            if abs(_D(str(pf.amount or 0))) == amt and r.transaction_date and \
                    abs((pf.transaction_time.date() - r.transaction_date).days) <= 10:
                hit = pf
                break
        if hit is not None:
            r.alipay_flow_no = hit.transaction_no  # 自动填号, 不报异常
            used.add(hit.transaction_no)
            continue
        _record(
            db,
            source_table="promotion_flows",
            source_pk=r.id,
            exception_type="promotion_recharge_unmatched",
            severity="warning",
            description=f"推广充值记录 {r.id} (¥{r.amount}, {r.transaction_date}) 缺支付宝流水号, 无法核对充值出账。",
            suggestion_action="补填该充值对应的支付宝流水号。",
            context={"id": r.id, "amount": str(r.amount), "date": str(r.transaction_date)},
        )
        count += 1
    _log.info("scan_promotion_recharge_unmatched: %d", count)
    return count


# ---------------------------------------------------------------------------
# B12 — 工厂对账不平 (账单 ≠ 实付)
# ---------------------------------------------------------------------------

def scan_factory_recon_unbalanced(db: Session) -> int:
    """工厂对账 账单 vs 实付 不平 → 异常 (未付清 / 超付)。

    区别于 factory_recon_incomplete (缺字段): 这条是金额对不上的实质差异,
    让"4月差¥13389未付清""某期超付"这类口子在异常中心冒出来, 而不是只算个 diff 藏着。
    """
    count = 0
    for r in db.query(FactoryReconciliation).filter(
        FactoryReconciliation.status.in_(["underpaid", "overpaid"]),
    ).all():
        diff = r.diff_amount or 0
        if r.status == "underpaid":
            sev, label, act = "error", "未付清", f"账单 ¥{r.bill_amount} > 实付 ¥{r.paid_amount}, 尚欠 ¥{diff}。请确认是否漏付或分期。"
        else:
            sev, label, act = "warning", "超付", f"实付 ¥{r.paid_amount} > 账单 ¥{r.bill_amount}, 多付 ¥{-diff}。请确认是否预付或重复支付。"
        _record(
            db,
            source_table="factory_reconciliations",
            source_pk=r.id,
            exception_type="factory_recon_unbalanced",
            severity=sev,
            description=f"工厂对账 [{r.factory_name}] {r.period_start}~{r.period_end} 对账不平({label}): {act}",
            suggestion_action="核对工厂账单与支付宝付款流水, 补付/退回差额或登记说明。",
            context={"id": r.id, "factory_name": r.factory_name, "status": r.status,
                     "bill_amount": str(r.bill_amount), "paid_amount": str(r.paid_amount),
                     "diff_amount": str(r.diff_amount)},
        )
        count += 1
    _log.info("scan_factory_recon_unbalanced: %d", count)
    return count


# ---------------------------------------------------------------------------
# B13 — 支付宝支出被自动归类为采购(存疑), 待人工确认
# ---------------------------------------------------------------------------

def scan_unclassified_purchase(db: Session) -> int:
    """无法归类的支出流水被自动建成的采购记录 → 异常, 提示人工确认归类。

    堵住最大的静默漏洞: 系统把对不上的支出"猜"成采购后, 不再无声无息,
    而是逐条进异常中心, 用户能看到"系统替你归了哪些类、需要复核"。
    """
    from app.services.alipay_flow_router_service import UNCLASSIFIED_PURCHASE_TYPE
    count = 0
    for r in db.query(PartPurchase).filter(
        PartPurchase.purchase_type == UNCLASSIFIED_PURCHASE_TYPE,
    ).all():
        _record(
            db,
            source_table="part_purchases",
            source_pk=r.id,
            exception_type="unclassified_purchase",
            severity="warning",
            description=(f"采购记录 {r.purchase_no} (¥{r.amount}, {r.supplier or '未知对手方'}) "
                         f"由支付宝流水自动归类, 实际用途存疑, 需人工确认是否为采购。"),
            suggestion_action="核对该笔支出真实用途 (采购/日常经营/外包/其它), 修正归类或补全配件信息。",
            context={"id": r.id, "purchase_no": r.purchase_no, "amount": str(r.amount),
                     "supplier": r.supplier, "alipay_flow_no": r.alipay_flow_no},
        )
        count += 1
    _log.info("scan_unclassified_purchase: %d", count)
    return count


# 明显不是配件采购的"财务噪音"关键词 (支付宝流水被误建成采购记录)
_MISCLASS_KEYWORDS = (
    "理财", "申购", "赎回", "利息", "收益", "转入", "转出", "转账", "还款", "借款",
    "提现", "充值", "红包", "服务费", "保证金", "消费者",
)


def scan_misclassified_purchase(db: Session) -> int:
    """配件采购里混进的财务噪音(理财申购/单次转入/服务费/保证金…) → 异常, 提示重新归类。

    这些是支付宝流水被误当成"配件采购"建进来的: 既不是配件, 也会污染采购统计。
    归类建议: 理财/转入/转账 → 其它/忽略; 消费者体验提升计划服务费 → 平台费/经营支出;
    消费者保证金充值 → 平台保证金。
    """
    count = 0
    for r in db.query(PartPurchase).all():
        name = f"{r.material_name or ''}{r.supplier or ''}"
        hit = next((kw for kw in _MISCLASS_KEYWORDS if kw in name), None)
        if not hit:
            continue
        nm = (r.material_name or r.supplier or "")[:40]   # material_name 可能很长, 截断
        _record(
            db,
            source_table="part_purchases",
            source_pk=r.id,
            exception_type="misclassified_purchase",
            severity="warning",
            description=(f"采购记录 {r.purchase_no}「{nm}」(¥{r.amount}) 含「{hit}」, "
                         f"疑似财务流水(理财/转账/服务费/保证金)被误归成配件采购。"
                         f"建议: 理财/转入→其它; 服务费→平台费/经营; 保证金→平台保证金。"),
            suggestion_action="疑似财务流水误入配件采购，请改归类或删除。",   # 列宽仅 64
            context={"id": r.id, "purchase_no": r.purchase_no, "name": nm,
                     "supplier": r.supplier, "amount": str(r.amount), "matched": hit},
        )
        count += 1
    _log.info("scan_misclassified_purchase: %d", count)
    return count


def scan_alipay_duplicate_flow(db: Session) -> int:
    """支付宝重复流水检测 (智能判重)。

    背景: 一笔淘宝收款, 支付宝会产生两条共用同一「交易流水号」的流水 ——
      - 在线支付: 客户实付货款 (正, 本店收入)
      - 分账    : 淘宝支付手续费 (负, 约千分之六)
    这种「同号不同交易类型」是正常配对, 不能算重复。
    真正的重复是「同账户 + 同交易流水号 + 同交易类型」出现多条 (常见于导出/导入重跑、
    手工补录), 会把收入或手续费重复计一遍, 必须人工核对。

    判重规则: 只对 (account, transaction_no, transaction_type) 完全相同的多条流水报异常;
    同号不同类型 (分账 / 在线支付) 一律放行。
    """
    from collections import defaultdict

    groups: dict[tuple, list] = defaultdict(list)
    for row in db.query(AlipayFlow).filter(
        AlipayFlow.transaction_no.isnot(None),
        AlipayFlow.transaction_no != "",
    ).all():
        # 业务流水号(transaction_no)会被多笔不同交易复用(分账/其它/手续费), 判重必须连金额一起比 ——
        # 否则把"复用同一业务流水号的不同金额交易"误判成重复 (2026-06-15 根因修; 实测53组误报,
        # 如 交易分账#…599608 含 ¥22.00/¥-0.13/¥21.87 是三笔不同交易)。与导入去重键 (no,type,amount) 一致。
        key = (row.account, row.transaction_no, row.transaction_type, str(row.amount))
        groups[key].append(row)

    count = 0
    for (account, tx_no, tx_type, _amt), rows in groups.items():
        if len(rows) < 2:
            continue
        rows_sorted = sorted(rows, key=lambda r: r.id)
        amounts = [str(r.amount) for r in rows_sorted]
        ids = [r.id for r in rows_sorted]
        # 同号 + 同类型 + 同金额 → 几乎确定是真重复(error); 金额不同 → 疑似(warning)。
        identical = len(set(amounts)) == 1
        sev = "error" if identical else "warning"
        label = "完全重复(同号+同类型+同金额)" if identical else "疑似重复(同号+同类型,金额不同)"
        # 异常挂在除第一条以外的每条「多余」流水上, 方便逐条核销/删除。
        for r in rows_sorted[1:]:
            _record(
                db,
                source_table="alipay_flows",
                source_pk=r.id,
                exception_type="alipay_duplicate_flow",
                severity=sev,
                description=(
                    f"支付宝重复流水 [{account}] 流水号 {tx_no} 交易类型「{tx_type}」"
                    f"出现 {len(rows)} 条 ({label}): 金额 {amounts}, id={ids}。"
                    f"注: 同号的『分账(手续费)+在线支付(收款)』为正常配对, 不在此列。"
                ),
                suggestion_action=(
                    "核对是否为导入重跑/手工补录造成的重复; 若确为重复, 删除多余流水, "
                    "仅保留一条, 避免收入或手续费被重复计入。"
                ),
                context={"account": account, "transaction_no": tx_no,
                         "transaction_type": tx_type, "amounts": amounts, "ids": ids,
                         "identical": identical},
            )
            count += 1
    _log.info("scan_alipay_duplicate_flow: %d", count)
    return count


# ---------------------------------------------------------------------------
# B17 — 定制订单缺成本依据 (方案B): 无工厂实际成本 且 无定制加价 → 挂异常
# ---------------------------------------------------------------------------

def scan_custom_order_missing_cost_basis(db: Session) -> int:
    """方案B: is_custom 单既无工厂实际成本(actual_cost)、又无定制加价(custom_surcharge),
    系统无法核算成本 → 挂异常, 提示逐条补「定制需求(定制加价)」。

    已填工厂实际成本的定制单不报(用户明确说这些已无所谓);
    已填定制加价的也不报(系统能按 基础BOM+加价 自动算预计成本)。
    含历史单(老定制单正是要逐条补的对象), 仅排除补单。
    """
    from sqlalchemy import or_
    count = 0
    for o in db.query(Order).filter(
        Order.is_refill == False,       # noqa: E712  # 补单/刷单不挂缺成本(¥0成本正常)
        Order.status.notin_(["cancelled"]),
    ).all():
        # 强制确保不是刷单/非产品再录异常 (用户拍板 2026-06-17): 非实物(差价/邮费/样品/专链)直接跳过
        if is_non_product_order(o):
            continue
        if not is_custom_order(o):
            continue  # 仅定制单: is_custom / 「改」后缀 / 数字尾号≥90(99/98…), DB 无关
        if o.actual_cost is not None:
            continue  # 已有工厂实际成本 → 不动
        if o.custom_surcharge is not None:
            continue  # 已有定制加价 → 可算预计成本, 不报
        if o.theoretical_cost is not None:
            continue  # 已有推演成本(定制单核对自动写回, 含成本=0) → 不再报缺依据 (用户拍板 2026-06-17)
        _record(
            db,
            source_table="orders",
            source_pk=o.id,
            exception_type="custom_order_missing_cost_basis",
            severity="warning",
            description=f"定制订单 {o.order_no} 既无工厂实际成本, 又无定制加价, 无法核算成本。",
            suggestion_action="填写该单「定制加价」(定制需求报价), 系统将按 基础BOM成本 + 定制加价 自动算预计成本。",
            context={"order_no": o.order_no, "sku_code": o.sku_code, "status": o.status},
        )
        count += 1
    _log.info("scan_custom_order_missing_cost_basis: %d", count)
    return count


# ---------------------------------------------------------------------------
# 全量扫描入口
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# B-extra — 产品 / BOM / 物料 一致性冲突 (配件清单串料 / 占位名 的根源)
# ---------------------------------------------------------------------------
_PLACEHOLDER_NAMES = {"待补", "—", "-", "?", "？", "待定", "未知"}


def _is_placeholder_name(name) -> bool:
    s = (name or "").strip()
    return (not s) or s.startswith("占位") or s in _PLACEHOLDER_NAMES


def scan_bom_product_collision(db: Session) -> int:
    """一个 SKU 编码在 BOM 里挂了多个产品 → 按此 SKU 生成的配件清单会串料。"""
    from sqlalchemy import func, select
    from app.models.bom import BomLine
    count = 0
    rows = db.execute(
        select(BomLine.sku_code, func.count(func.distinct(BomLine.product_code)))
        .where(BomLine.sku_code.isnot(None))
        .group_by(BomLine.sku_code)
        .having(func.count(func.distinct(BomLine.product_code)) > 1)
    ).all()
    for sku_code, npc in rows:
        prods = db.execute(
            select(BomLine.product_code, BomLine.product_name)
            .where(BomLine.sku_code == sku_code).distinct()
        ).all()
        plist = "; ".join(f"{pc}({pn or '-'})" for pc, pn in prods)
        _record(
            db,
            source_table="bom_lines",
            source_pk=sku_code,
            exception_type="bom_product_collision",
            severity="error",
            description=f"SKU 编码 {sku_code} 在 BOM 里挂了 {npc} 个产品: {plist}。会导致按此 SKU 生成的配件清单串料。",
            suggestion_action="删多余产品或改正其SKU编码",
            context={"sku_code": sku_code,
                     "products": [{"code": pc, "name": pn} for pc, pn in prods]},
        )
        count += 1
    _log.info("scan_bom_product_collision: %d colliding sku_codes", count)
    return count


def scan_material_name_conflict(db: Session) -> int:
    """同一料号在 物料库 与 BOM 里名字不一样(都非占位) → 同编码指了不同物料。"""
    from sqlalchemy import select
    from app.models.bom import BomLine
    from app.models.material import Material
    rows = db.execute(
        select(BomLine.material_code, BomLine.material_name, Material.name)
        .join(Material, BomLine.material_code == Material.code)
        .where(BomLine.material_name.isnot(None))
        .distinct()
    ).all()
    count = 0
    seen: set[str] = set()
    for code, bom_name, mat_name in rows:
        if code in seen or _is_placeholder_name(bom_name) or _is_placeholder_name(mat_name):
            continue
        if (bom_name or "").strip() == (mat_name or "").strip():
            continue
        seen.add(code)
        _record(
            db,
            source_table="materials",
            source_pk=code,
            exception_type="material_name_conflict",
            severity="error",
            description=f"料号 {code} 名称冲突: 物料库='{mat_name}', BOM='{bom_name}' —— 同一编码指了不同物料。",
            suggestion_action="核对该料号并改正名称",
            context={"material_code": code, "material_name": mat_name, "bom_name": bom_name},
        )
        count += 1
    _log.info("scan_material_name_conflict: %d conflicts", count)
    return count


def scan_material_placeholder(db: Session) -> int:
    """物料库里名字还是"占位"、且已被 BOM 引用的料号 → 该补真实名。"""
    from sqlalchemy import select
    from app.models.bom import BomLine
    from app.models.material import Material
    count = 0
    for code, name in db.execute(
        select(Material.code, Material.name).where(Material.name.like("占位%"))
    ).all():
        used = db.execute(
            select(BomLine.id).where(BomLine.material_code == code).limit(1)
        ).first()
        if not used:
            continue
        bom_name = db.execute(
            select(BomLine.material_name).where(
                BomLine.material_code == code, BomLine.material_name.isnot(None)
            ).limit(1)
        ).scalar()
        sugg = (f"建议填: {bom_name}" if bom_name and not _is_placeholder_name(bom_name)
                else "请补填该物料真实名称")
        _record(
            db,
            source_table="materials",
            source_pk=code,
            exception_type="material_placeholder",
            severity="warning",
            description=f"料号 {code} 物料库名称还是占位『{name}』, 已被 BOM 引用但未填真实名。",
            suggestion_action=sugg[:64],
            context={"material_code": code, "placeholder_name": name, "bom_name": bom_name},
        )
        count += 1
    _log.info("scan_material_placeholder: %d placeholders in use", count)
    return count


def scan_product_missing_taobao_ids(db: Session) -> int:
    """产品缺 淘宝商品ID / SKU ID → 异常 (用户要求: 对应关系直接维护在产品表上)。"""
    from app.models.product import Product
    count = 0
    for p in db.query(Product).all():
        if (getattr(p, "listing_status", None) or "") == "下架":
            continue  # 下架=未上架/未生产, 不报缺淘宝映射
        if is_non_sellable_product(p):
            continue  # 作废/定制/安装/送货/样品 等非卖品, 本就没上架
        missing = []
        if not (p.taobao_id or "").strip():
            missing.append("淘宝商品ID")
        if not (p.taobao_sku_id or "").strip():
            missing.append("淘宝SKU ID")
        if not missing:
            continue
        _record(
            db,
            source_table="products",
            source_pk=p.code,
            exception_type="missing_taobao_mapping",
            severity="warning",
            description=f"产品 {p.code} {p.name} 缺 {'/'.join(missing)}, 订单将无法按对应表自动回填编码。",
            suggestion_action="到产品页编辑补填; 或导入淘宝宝贝导出表自动回填。",
            context={"product_code": p.code, "missing": missing},
        )
        count += 1
    _log.info("scan_product_missing_taobao_ids: %d", count)
    return count


def scan_factory_bill_on_dead_order(db: Session) -> int:
    """工厂账单挂在「已取消/全额退款」的订单上 → 异常: 工厂已下单、成本真实发生, 但该销售单作废/退款
    已被成交口径排除出财务, 成本会"悄悄丢失"或需重新归属到客户的重下单(像那次的床¥3400)。供人工改归属。
    口径: factory_recon_items.order_no 对应 Order 是 cancelled, 或全额退款(refund≥实付×0.99 且实付>0)。"""
    from decimal import Decimal
    from sqlalchemy import text
    bills = dict(db.execute(text(
        "SELECT order_no, COALESCE(SUM(settle_price),0) FROM factory_recon_items "
        "WHERE order_no IS NOT NULL GROUP BY order_no")).all())
    if not bills:
        return 0
    count = 0
    for o in db.query(Order).filter(Order.order_no.in_(list(bills.keys()))).all():
        bill = Decimal(str(bills.get(o.order_no) or 0))
        if bill <= 0:
            continue
        paid = Decimal(str(o.paid_amount or 0))
        refund = Decimal(str(o.refund_amount or 0))
        is_cancelled = (o.status or "") == "cancelled"
        is_full_refund = paid > 0 and refund >= paid * Decimal("0.99")
        if not (is_cancelled or is_full_refund):
            continue
        reason = "已取消" if is_cancelled else "全额退款"
        _record(
            db, source_table="orders", source_pk=o.id,
            exception_type="factory_bill_on_dead_order", severity="warning",
            description=(f"工厂账单 ¥{bill} 挂在「{reason}」的订单 {o.order_no} 上 —— 工厂已下单、成本真实发生, "
                        f"但该销售单作废/退款已被排除出财务, 成本会丢失或需重新归属到客户的重下单。"),
            suggestion_action=("核实工厂这单货是否给了客户的重下单: 是 → 把工厂账单 order_no 改到重下单"
                               "(或在重下单填 actual_cost); 否(工厂未真做/已退) → 可忽略。"),
            context={"order_no": o.order_no, "factory_bill": str(bill), "status": o.status},
        )
        count += 1
    _log.info("scan_factory_bill_on_dead_order: %d", count)
    return count


def run_all(db: Session) -> dict[str, int]:
    results: dict[str, int] = {}
    scanners = [
        ("order_missing_cost", scan_order_missing_cost),
        ("cost_exceeds_paid", scan_cost_exceeds_paid),
        ("order_missing_alipay", scan_order_missing_alipay),
        ("stale_import", scan_stale_import),
        ("order_missing_tracking", scan_order_missing_tracking),
        ("refill_unmatched", scan_refill_unmatched),
        ("alipay_missing_txn", scan_alipay_missing_txn),
        ("alipay_duplicate_flow", scan_alipay_duplicate_flow),
        ("factory_recon_incomplete", scan_factory_recon_incomplete),
        ("factory_recon_unbalanced", scan_factory_recon_unbalanced),
        ("factory_bill_on_dead_order", scan_factory_bill_on_dead_order),
        ("unclassified_purchase", scan_unclassified_purchase),
        ("misclassified_purchase", scan_misclassified_purchase),
        ("outsourcing_missing", scan_outsourcing_missing),
        ("aftersales_empty", scan_aftersales_empty),
        ("alipay_balance_gap", scan_alipay_balance_gap),
        ("wood_loss_high", scan_wood_loss_high),
        ("sample_missing_cost", scan_sample_missing_cost),
        ("factory_order_uncovered", scan_factory_order_uncovered),
        ("promotion_recharge_unmatched", scan_promotion_recharge_unmatched),
        ("custom_order_missing_cost_basis", scan_custom_order_missing_cost_basis),
        ("bom_product_collision", scan_bom_product_collision),
        ("material_name_conflict", scan_material_name_conflict),
        ("material_placeholder", scan_material_placeholder),
        ("product_missing_taobao_ids", scan_product_missing_taobao_ids),
    ]
    for name, fn in scanners:
        try:
            results[name] = fn(db)
            db.commit()
        except Exception as e:  # pragma: no cover
            db.rollback()
            _log.error("scanner %s failed: %s", name, e)
            results[name] = -1
    return results
