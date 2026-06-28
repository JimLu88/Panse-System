"""支付宝流水 → 各业务表 自动归类回填 (Phase 3).

在 smart_matching_service 给流水打完 reconciliation_type 后, 把流水落到对应业务表:

  create_aftersales_from_flows  18-售后表   : 非订单售后支出 → 自动建售后记录 (必须最先跑)
  match_promotion_flows   15-推广记录   : 按金额+日期回填 alipay_flow_no
  match_daily_operations  16-日常经营   : 按金额+日期(+备注)回填 alipay_flow_no
  match_outsourcing       17-人员外包   : 按金额+日期回填 (侧重 爱群号 账户)
  create_purchases        7-配件采购    : 无法归类的支出流水 → 新建采购记录 (年份单号)
  flip_factory_payment    6-工厂下单    : 工厂订单有流水号 → 付款状态翻 已付款 + 付款日期

所有操作幂等: 只填空 alipay_flow_no / 只补缺, 已配对的不动。run_all 一键全跑。
"""
from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.marketing import AfterSales, DailyOperation, OutsourcingExpense, PromotionFlow
from app.models.order import FactoryOrder, PartPurchase
from app.services import internal_accounts

_logger = logging.getLogger("panse.alipay_router")

_MATCH_WINDOW_DAYS = 10   # 金额相同时, 日期相差不超过 N 天才算配上

# 无法归类的支出流水自动建成的采购记录, 用此 purchase_type 标记为「存疑」,
# 由 data_quality_service.scan_unclassified_purchase 捞成异常, 在异常中心提示人工确认,
# 避免「系统替你猜成采购、你却不知道」的静默归类。
UNCLASSIFIED_PURCHASE_TYPE = "存疑(支付宝流水自动归类)"

def _q2(v) -> Decimal:
    return Decimal(str(abs(v))).quantize(Decimal("0.01"))


def _index_flows_by_amount(flows: list[AlipayFlow]) -> dict[Decimal, list[AlipayFlow]]:
    idx: dict[Decimal, list[AlipayFlow]] = {}
    for f in flows:
        if f.amount is None:
            continue
        idx.setdefault(_q2(f.amount), []).append(f)
    return idx


def _match_flow(amount, when: Optional[date], idx, used: set[str]) -> Optional[AlipayFlow]:
    """按 |金额| 相等 + 日期最近 (窗口内) 挑一条未用过的流水。"""
    if amount is None:
        return None
    cands = idx.get(_q2(amount), [])
    best: Optional[AlipayFlow] = None
    best_gap = None
    for f in cands:
        if f.transaction_no in used:
            continue
        gap = 0
        if when and f.transaction_time:
            gap = abs((f.transaction_time.date() - when).days)
            if gap > _MATCH_WINDOW_DAYS:
                continue
        if best is None or (best_gap is not None and gap < best_gap):
            best, best_gap = f, gap
    return best


_AFTERSALES_RECON_TYPES = frozenset({
    "aftersales", "refund", "compensation", "return", "售后", "退款", "赔付",
})

_MATERIAL_GUESS_THRESHOLD = 0.55   # difflib 相似度阈值


@dataclass
class RouteResult:
    aftersales_created: int = 0
    promotion_filled: int = 0
    daily_filled: int = 0
    outsourcing_filled: int = 0
    purchases_created: int = 0
    factory_flipped: int = 0
    notes: list[str] = field(default_factory=list)


def _all_flows(db: Session, *, expense_only: bool = False,
               account: Optional[str] = None) -> list[AlipayFlow]:
    stmt = select(AlipayFlow)
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    rows = db.execute(stmt).scalars().all()
    if expense_only:
        rows = [r for r in rows if (r.amount or 0) < 0]
    return rows


def create_aftersales_from_flows(db: Session) -> int:
    """18-售后表: 有关联订单号的售后/退款支出流水 → 自动建售后记录.

    范围: amount<0 且 related_order_no 非空, 且流水 reconciliation_type 指向售后/退款类型。
    幂等: 按 alipay_flow_no 去重 (已在售后表里的不重建)。
    必须在 create_purchases_from_unclassified 之前运行, 避免被采购抢走。
    """
    existing_flow_nos = {
        no for no in db.execute(
            select(AfterSales.alipay_flow_no).where(AfterSales.alipay_flow_no.isnot(None))
        ).scalars().all()
        if no
    }
    flows = db.execute(
        select(AlipayFlow).where(
            AlipayFlow.amount < 0,
            AlipayFlow.related_order_no.isnot(None),
        )
    ).scalars().all()
    n = 0
    for f in flows:
        if f.transaction_no in existing_flow_nos:
            continue
        rt = (f.reconciliation_type or "").lower()
        remark_lower = (f.remark or "").lower()
        is_aftersales = (
            any(k in rt for k in _AFTERSALES_RECON_TYPES)
            or any(k in remark_lower for k in ("售后", "退款", "赔付", "补偿", "退货"))
        )
        if not is_aftersales:
            continue
        when = f.transaction_time.date() if f.transaction_time else date.today()
        db.add(AfterSales(
            platform_order_no=(f.related_order_no or "").strip(),
            reason=(f.remark or f.transaction_type or "支付宝流水自动归类"),
            direct_compensation=_q2(f.amount),
            processed_at=when,
            alipay_flow_no=f.transaction_no,
            status="auto",
            remark=f"自动从支付宝流水 {f.transaction_no} 生成",
        ))
        existing_flow_nos.add(f.transaction_no)
        n += 1
    db.flush()
    return n


def match_promotion_flows(db: Session) -> int:
    """15-推广记录: 给缺流水号的推广记录按金额+日期配支付宝流水。"""
    flows = _all_flows(db)
    idx = _index_flows_by_amount(flows)
    used: set[str] = set()
    rows = db.execute(
        select(PromotionFlow).where(PromotionFlow.alipay_flow_no.is_(None))
    ).scalars().all()
    n = 0
    for r in rows:
        f = _match_flow(r.amount, r.transaction_date, idx, used)
        if f:
            r.alipay_flow_no = f.transaction_no
            used.add(f.transaction_no)
            n += 1
    db.flush()
    return n


def match_daily_operations(db: Session) -> int:
    """16-日常经营: 按金额+日期配流水; 同金额多笔时优先备注/对象有交集的。"""
    flows = _all_flows(db)
    idx = _index_flows_by_amount(flows)
    used: set[str] = set()
    rows = db.execute(
        select(DailyOperation).where(DailyOperation.alipay_flow_no.is_(None))
    ).scalars().all()
    n = 0
    for r in rows:
        f = _match_flow(r.amount, r.record_date, idx, used)
        if f:
            r.alipay_flow_no = f.transaction_no
            used.add(f.transaction_no)
            n += 1
    db.flush()
    return n


def match_outsourcing(db: Session) -> int:
    """17-人员外包费用: 按金额+日期配流水 (爱群号等账户支出)。"""
    flows = _all_flows(db)
    idx = _index_flows_by_amount(flows)
    used: set[str] = set()
    rows = db.execute(
        select(OutsourcingExpense).where(OutsourcingExpense.alipay_flow_no.is_(None))
    ).scalars().all()
    n = 0
    for r in rows:
        f = _match_flow(r.amount, r.payment_date, idx, used)
        if f:
            r.alipay_flow_no = f.transaction_no
            used.add(f.transaction_no)
            n += 1
    db.flush()
    return n


def _next_purchase_no_yearly(db: Session, year: int) -> str:
    """采购单号: 年份+5位序号, 如 202600001 (业务指定格式)。"""
    prefix = str(year)
    rows = db.execute(
        select(PartPurchase.purchase_no).where(PartPurchase.purchase_no.like(f"{prefix}%"))
    ).scalars().all()
    max_seq = 0
    for no in rows:
        tail = (no or "")[len(prefix):]
        if tail.isdigit() and len(tail) == 5:
            max_seq = max(max_seq, int(tail))
    return f"{prefix}{max_seq + 1:05d}"


def _guess_material(db: Session, name: str) -> tuple[Optional[str], Optional[str]]:
    """用 difflib 对 Material.name 模糊匹配, 返回 (material_code, hint_suffix).

    hint_suffix 形如 "(疑似 72%)" 用于拼接到 material_name。
    相似度低于阈值时返回 (None, None)。
    """
    from app.models.material import Material
    materials = db.execute(select(Material.code, Material.name)).all()
    best_code: Optional[str] = None
    best_ratio = 0.0
    for code, mname in materials:
        if not mname:
            continue
        ratio = difflib.SequenceMatcher(None, name.lower(), mname.lower()).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_code = code
    if best_ratio >= _MATERIAL_GUESS_THRESHOLD and best_code:
        pct = int(best_ratio * 100)
        return best_code, f"(疑似 {pct}%)"
    return None, None


# 非采购支出关键字: 平台代扣/代付/服务费、理财/余额宝转入、退款/转账等 —— 不当成"配件采购"。
# 写进系统: 以后导入支付宝流水, 命中这些的不会再生成采购记录(自动筛选)。
# "消费券代付资金扣回"属于订单级扣款(已在 order_settlements 按订单核算), 不是采购。
_NON_PURCHASE_KW = (
    "代扣", "代付", "资金扣回", "消费券", "服务费", "手续费",
    "理财", "申购", "赎回", "余额宝", "退款", "还款",
    "转账", "转入", "转出", "单次转", "提现", "花呗", "借呗", "工资", "保证金",
    "淘天", "淘宝", "天猫",
)


def _is_non_purchase(f) -> bool:
    text = f"{f.transaction_type or ''}{f.remark or ''}{f.counterparty or ''}"
    if any(k in text for k in _NON_PURCHASE_KW):
        return True
    # 内部互转 (我方账户/人员之间挪钱) 不是采购; 员工代购(带真实货品备注)除外
    return internal_accounts.is_internal_transfer(
        f.counterparty, f.transaction_type, f.remark
    )


def purge_non_purchase_records(db: Session) -> int:
    """清理已误生成的非采购记录 (代付/理财/内部互转), 每次流水归类时自动跑。

    只删自动生成的行 (有 alipay_flow_no), 手工录入的不碰。
    员工代购 (内部人员 + 真实货品备注) 保留并补 purchase_type=员工代购。
    """
    rows = db.execute(
        select(PartPurchase).where(PartPurchase.alipay_flow_no.isnot(None))
    ).scalars().all()
    n = 0
    unlinked_flow_nos: list[str] = []
    for p in rows:
        name = p.material_name or ""
        if any(k in name for k in _NON_PURCHASE_KW):
            unlinked_flow_nos.append(p.alipay_flow_no)
            db.delete(p)
            n += 1
            continue
        # 已导入账户主人 (爱群/魏佳音/畔色…): 任何打款都是内部互转 → 删 (防双计)
        if internal_accounts.is_imported_account_owner(p.supplier):
            unlinked_flow_nos.append(p.alipay_flow_no)
            db.delete(p)
            n += 1
            continue
        if internal_accounts.is_internal_counterparty(p.supplier):
            if internal_accounts.is_transfer_like(name) or not name.strip():
                unlinked_flow_nos.append(p.alipay_flow_no)
                db.delete(p)
                n += 1
            elif p.purchase_type != internal_accounts.EMPLOYEE_PROXY_PURCHASE_TYPE:
                p.purchase_type = internal_accounts.EMPLOYEE_PROXY_PURCHASE_TYPE
    db.flush()
    # Plan L8: 删除的采购行解绑流水 — 无其他业务行引用的流水核销状态回 open
    if unlinked_flow_nos:
        from app.services import match_unlink_service
        for no in unlinked_flow_nos:
            match_unlink_service.unlink_purchase(db, no)
    return n


def create_purchases_from_unclassified(db: Session) -> int:
    """7-配件采购记录: 把无法归类的支出流水整合成采购记录。

    范围: amount<0 且 reconciliation_type 为空/other, 且该流水号尚未被任何
    采购/推广/外包/日常记录引用。供应商取对手方, 备注取流水备注, 视为已付款。
    """
    purge_non_purchase_records(db)   # 先清掉历史误归类 (代付/理财/内部互转)
    referenced = set()
    for model in (PartPurchase, PromotionFlow, OutsourcingExpense, DailyOperation, AfterSales):
        for no in db.execute(select(model.alipay_flow_no)).scalars().all():
            if no:
                referenced.add(no)

    flows = db.execute(
        select(AlipayFlow).where(
            AlipayFlow.amount < 0,
            AlipayFlow.reconciliation_type.is_(None),
        )
    ).scalars().all()
    n = 0
    for f in flows:
        if f.transaction_no in referenced:
            continue
        if _is_non_purchase(f):
            continue   # 平台代扣/服务费/理财/余额宝/退款/转账等 → 不是配件采购, 不生成记录
        when = f.transaction_time.date() if f.transaction_time else date.today()
        amount = _q2(f.amount)
        raw_name = f.remark or f.transaction_type or "未分类支出"
        mat_code, hint = _guess_material(db, raw_name)
        mat_name = raw_name + (hint or "")
        # 员工代购: 对手方内部人员 + 真实货品备注 → 保留为采购但专门标记
        ptype = (internal_accounts.EMPLOYEE_PROXY_PURCHASE_TYPE
                 if internal_accounts.is_internal_counterparty(f.counterparty)
                 else UNCLASSIFIED_PURCHASE_TYPE)
        pp = PartPurchase(
            purchase_no=_next_purchase_no_yearly(db, when.year),
            supplier=f.counterparty,
            purchase_date=when,
            material_code=mat_code,
            material_name=mat_name,
            qty=Decimal("1"),
            unit_price=amount,
            amount=amount,
            total_amount=amount,
            # 归账用淘宝平台订单号(对得上 Order.order_no), 而非支付宝商户单号(related_order_no);
            # 平台订单号可多单(\n 分隔) → 交给 parts_recon.aggregate_related_purchases 拆单按 BOM 占比分摊。
            related_order_no=(f.platform_order_no or None),
            purchase_type=ptype,
            payment_method="支付宝",
            payment_status="paid",
            payment_date=when,
            alipay_flow_no=f.transaction_no,
        )
        db.add(pp)
        db.flush()  # so next yearly seq sees this row
        referenced.add(f.transaction_no)
        n += 1
    return n


def flip_factory_payment(db: Session) -> int:
    """6-工厂下单: 有支付宝流水号的工厂订单 → 付款状态翻已付款 + 补付款日期。"""
    rows = db.execute(
        select(FactoryOrder).where(
            FactoryOrder.alipay_flow_no.isnot(None),
            FactoryOrder.payment_status != "paid",
        )
    ).scalars().all()
    n = 0
    for fo in rows:
        fo.payment_status = "paid"
        if fo.payment_date is None:
            flow = db.execute(
                select(AlipayFlow).where(AlipayFlow.transaction_no == fo.alipay_flow_no)
            ).scalar_one_or_none()
            if flow and flow.transaction_time:
                fo.payment_date = flow.transaction_time.date()
        n += 1
    db.flush()
    return n


def run_all(db: Session, *, create_purchases: bool = True) -> RouteResult:
    """一键全跑 (建议先跑 smart_matching_service.run 打好 reconciliation_type).

    顺序: 售后先抢 → 推广/日常/外包匹配 → 采购兜底 → 工厂翻已付 → 回写售后成本。
    create_purchases=False: 只做匹配/回填(填空 alipay_flow_no 等), 不把无法归类的支出兜底建成
    采购记录 —— 供 run_ingest 导入后"联动消异常"调用, 避免每次导入都新建一堆待确认采购。
    """
    from app.services import order_sync_service
    res = RouteResult()
    res.aftersales_created = create_aftersales_from_flows(db)
    res.promotion_filled = match_promotion_flows(db)
    res.daily_filled = match_daily_operations(db)
    res.outsourcing_filled = match_outsourcing(db)
    if create_purchases:
        res.purchases_created = create_purchases_from_unclassified(db)
    res.factory_flipped = flip_factory_payment(db)
    # 售后新建后立即回写订单赔付成本
    if res.aftersales_created > 0:
        order_sync_service.backfill_compensation_from_aftersales(db)
    _logger.info(
        "支付宝流水归类: 售后建%d 推广%d 日常%d 外包%d 采购新建%d 工厂翻已付%d",
        res.aftersales_created, res.promotion_filled, res.daily_filled,
        res.outsourcing_filled, res.purchases_created, res.factory_flipped,
    )
    return res
