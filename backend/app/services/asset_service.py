"""资产汇总 (Phase 4, 业务需求 14/19).

汇总 各账户余额 / 应收 / 库存账面 / 待发货 等, 给前端饼图.

业务需求 19: 公式 A = 初始 + 保证金 + 各类余额 + 待确认收货 + 未发货 - 未支付平台费
            - 未支付工厂打样 - 未支付工厂结算 - 未支付刷单佣金 - 未支付人员费
对比 B = 订单利润 + 账户余额. 差额生成 "未核销异常池" Alert.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, AlipayFlow
from app.models.inventory import PartInventory, ProductInventory
from app.models.marketing import OutsourcingExpense
from app.models.material import Material
from app.models.order import FactoryOrder, Order
from app.models.shop_deposit import ShopDeposit


@dataclass
class AssetCategory:
    name: str
    amount: Decimal
    detail: list[dict] = field(default_factory=list)


@dataclass
class AssetSummary:
    total: Decimal
    categories: list[AssetCategory] = field(default_factory=list)
    formula_a: Decimal = Decimal("0")
    formula_b: Decimal = Decimal("0")
    diff: Decimal = Decimal("0")
    # 业务需求 19: 公式 A 逐项拆解 (前端展示每个科目, 看差额藏在哪)
    breakdown: dict = field(default_factory=dict)


def _account_balances(db: Session) -> Decimal:
    """各账户最新月末 closing_balance 之和."""
    # 找每个账户最大 (year, month) 的 closing_balance
    rows = db.execute(
        select(AccountBalance).order_by(AccountBalance.id.desc())
    ).scalars().all()
    latest_per_account: dict[str, AccountBalance] = {}
    for r in rows:
        if r.account_name not in latest_per_account:
            latest_per_account[r.account_name] = r
    return sum((Decimal(r.closing_balance or 0) for r in latest_per_account.values()),
               Decimal("0"))


def _inventory_book_value(db: Session) -> tuple[Decimal, list[dict]]:
    """配件库存账面价值 = sum(physical_qty × material.price)."""
    rows = db.execute(
        select(PartInventory, Material).join(
            Material, Material.code == PartInventory.material_code,
        )
    ).all()
    total = Decimal("0")
    detail = []
    for inv, mat in rows:
        if mat.price and inv.physical_qty:
            val = (Decimal(mat.price) * Decimal(inv.physical_qty)).quantize(Decimal("0.01"))
            total += val
            detail.append({"material_code": inv.material_code,
                           "qty": inv.physical_qty, "unit_price": float(mat.price),
                           "value": float(val)})
    return total, detail


def _inventory_split(db: Session) -> tuple[Decimal, Decimal, Decimal]:
    """配件库存账面按编码前缀拆: 木料(MW) / 配件(AC) / 其它(SP/MP…)。"""
    rows = db.execute(
        select(PartInventory, Material).join(Material, Material.code == PartInventory.material_code)
    ).all()
    wood = parts = other = Decimal("0")
    for inv, mat in rows:
        if not (mat.price and inv.physical_qty):
            continue
        val = (Decimal(mat.price) * Decimal(inv.physical_qty)).quantize(Decimal("0.01"))
        code = (inv.material_code or "").upper()
        if code.startswith("MW"):
            wood += val
        elif code.startswith("AC"):
            parts += val
        else:
            other += val
    return wood, parts, other


def _product_inventory_value(db: Session) -> tuple[Decimal, list[dict]]:
    """成品库存价值 = Σ(成品现货 × 定价表工厂成本)。

    口径拍板 (2026-06-12): 财务计算一律用定价表工厂价格, 不用 BOM 物料单价
    (BOM 配件是单件订购价, 仅供定制估算)。SKU 名能对上定价行时用 SKU 级
    factory_cost, 否则产品级均值; factory_cost 缺失用 physical_cost 兜底。
    """
    from app.models.pricing import PricingSku

    sku_cost: dict[tuple[str, str], Decimal] = {}
    prod_costs: dict[str, list[Decimal]] = {}
    for ps in db.execute(select(PricingSku)).scalars().all():
        c = ps.factory_cost if ps.factory_cost is not None else ps.physical_cost
        if c is None:
            continue
        c = Decimal(str(c))
        if ps.sku:
            sku_cost[(ps.product_code, ps.sku)] = c
        prod_costs.setdefault(ps.product_code, []).append(c)
    prod_avg = {pc: (sum(cs) / len(cs)).quantize(Decimal("0.01"))
                for pc, cs in prod_costs.items()}

    total = Decimal("0")
    detail: list[dict] = []
    for inv in db.execute(select(ProductInventory)).scalars().all():
        cost = sku_cost.get((inv.product_code, inv.sku or "")) or prod_avg.get(inv.product_code)
        if cost and inv.physical_qty:
            val = (cost * Decimal(inv.physical_qty)).quantize(Decimal("0.01"))
            total += val
            detail.append({"product_code": inv.product_code,
                           "qty": float(inv.physical_qty), "unit_cost": float(cost),
                           "value": float(val)})
    return total, detail


def _pending_shipment_value(db: Session) -> Decimal:
    """已付未发货订单的待发货资产 (= paid_amount)."""
    rows = db.execute(
        select(func.coalesce(func.sum(Order.paid_amount), 0)).where(
            Order.status == "paid",
            Order.is_historical == False,  # noqa: E712
        )
    ).scalar()
    return Decimal(rows or 0)


def _pending_confirm_value(db: Session) -> Decimal:
    """待确认收货资产 = 已发货未签收订单的 paid_amount (status='shipped')."""
    rows = db.execute(
        select(func.coalesce(func.sum(Order.paid_amount), 0)).where(
            Order.status == "shipped",
            Order.is_historical == False,  # noqa: E712
        )
    ).scalar()
    return Decimal(rows or 0)


def _shop_deposit_total(db: Session) -> Decimal:
    """平台保证金 = ShopDeposit 多店铺条目求和 (押在平台的钱也是资产)."""
    total = db.execute(
        select(func.coalesce(func.sum(ShopDeposit.amount), 0))
    ).scalar()
    return Decimal(total or 0)


def _pending_factory_payment(db: Session) -> Decimal:
    """未支付的工厂订单结算 = sum(factory_bill_amount) where payment_status='unpaid'."""
    rows = db.execute(
        select(func.coalesce(func.sum(FactoryOrder.factory_bill_amount), 0)).where(
            FactoryOrder.payment_status == "unpaid",
            FactoryOrder.voided_at.is_(None),
        )
    ).scalar()
    return Decimal(rows or 0)


def _pending_platform_fee(db: Session) -> Decimal:
    """未支付平台费 = 已付未发货/待确认订单上记录的 platform_fee 之和 (尚未从余额扣)."""
    rows = db.execute(
        select(func.coalesce(func.sum(Order.platform_fee), 0)).where(
            Order.status.in_(("paid", "shipped")),
            Order.is_historical == False,  # noqa: E712
        )
    ).scalar()
    return Decimal(rows or 0)


def _pending_personnel_cost(db: Session) -> Decimal:
    """未支付人员费 = 外包费用表里无支付宝流水号的条目 (= 未结算) 之和."""
    rows = db.execute(
        select(func.coalesce(func.sum(OutsourcingExpense.amount), 0)).where(
            OutsourcingExpense.alipay_flow_no.is_(None),
        )
    ).scalar()
    return Decimal(rows or 0)


def _total_order_profit(db: Session) -> Decimal:
    """所有真实成交订单的累计净利润 (统一会计口径, 与 accounting_summary/逐单核对一致)。

    成交口径 = settled_sale_clause (已付款·非待付款/取消/关闭·非全额退款) + 排补单;
    单单利润 = (实付−退款) − 会计总成本(物理+物流+安装+平台扣点+税+额外售后)。
    旧版硬编码状态、按 paid_amount 不扣退款、成本不含平台/税, 故偏高 (2026-06-18 统一)。
    """
    from app.services import order_financials as ofin, sales_analytics
    coef = ofin.load_coefficients(db)
    as_by_order = ofin.extra_aftersales_by_order(db)
    orders = db.execute(
        select(Order).where(
            sales_analytics.settled_sale_clause(),
            Order.is_refill == False,  # noqa: E712
        )
    ).scalars().all()
    total = Decimal("0")
    for o in orders:
        revenue = Decimal(str(o.paid_amount or 0)) - Decimal(str(o.refund_amount or 0))
        cost = ofin.accounting_cost(o, coef, aftersales=Decimal(str(as_by_order.get(o.order_no, 0))))
        total += revenue - cost
    return total


def summary(db: Session) -> AssetSummary:
    """业务需求 14 资产总额 + 饼图分类 + 19 公式对比.

    公式 A (资产负债法) = 账户余额 + 库存账面 + 待发货资产 + 待确认收货
                        - 未付工厂结算 - 未付平台费 - 未付人员费
    公式 B (订单收益法) = 订单累计利润 + 账户余额
    两者理论相等; 差额提示未核销项 (保证金 / 工厂打样 / 刷单佣金 暂未建模, 见 breakdown)。
    """
    balances = _account_balances(db)
    inv_value, inv_detail = _inventory_book_value(db)
    wood_value, parts_value, other_mat_value = _inventory_split(db)
    product_inv_value, product_inv_detail = _product_inventory_value(db)
    deposit_total = _shop_deposit_total(db)
    pending_ship = _pending_shipment_value(db)
    pending_confirm = _pending_confirm_value(db)
    pending_factory = _pending_factory_payment(db)
    pending_platform = _pending_platform_fee(db)
    pending_personnel = _pending_personnel_cost(db)
    order_profit = _total_order_profit(db)

    # 饼图分类 (正向资产项)
    categories = [
        AssetCategory(name="账户余额", amount=balances),
        AssetCategory(name="平台保证金", amount=deposit_total),
        AssetCategory(name="木料库存(MW)", amount=wood_value),
        AssetCategory(name="配件库存(AC)", amount=parts_value),
        AssetCategory(name="其它物料库存", amount=other_mat_value),
        AssetCategory(name="成品库存(BOM成本)", amount=product_inv_value, detail=product_inv_detail[:50]),
        AssetCategory(name="待发货资产", amount=pending_ship),
        AssetCategory(name="待确认收货", amount=pending_confirm),
    ]
    total = sum((c.amount for c in categories), Decimal("0"))

    formula_a = (
        balances + deposit_total + inv_value + product_inv_value
        + pending_ship + pending_confirm
        - pending_factory - pending_platform - pending_personnel
    )
    formula_b = order_profit + balances

    breakdown = {
        # 正向项
        "账户余额": float(balances),
        "平台保证金": float(deposit_total),
        "木料库存(MW)": float(wood_value),
        "配件库存(AC)": float(parts_value),
        "其它物料库存": float(other_mat_value),
        "成品库存(BOM成本)": float(product_inv_value),
        "待发货资产": float(pending_ship),
        "待确认收货": float(pending_confirm),
        # 负向项 (未支付负债)
        "未付工厂结算": float(pending_factory),
        "未付平台费": float(pending_platform),
        "未付人员费": float(pending_personnel),
        # 公式 B 侧
        "订单累计利润": float(order_profit),
        # 暂未建模科目 (待数据源接入后补; 当前按 0 计, 可能是差额来源)
        "未建模_工厂打样": 0.0,
        "未建模_刷单佣金": 0.0,
    }

    return AssetSummary(
        total=total,
        categories=categories,
        formula_a=formula_a,
        formula_b=formula_b,
        diff=formula_a - formula_b,
        breakdown=breakdown,
    )


def diff_drilldown(db: Session) -> dict:
    """Plan L6: 公式 A/B 差额下钻 — 每个分项给构成明细 TopN, 定位差额藏在哪。"""
    inv_value, inv_detail = _inventory_book_value(db)
    product_inv_value, product_inv_detail = _product_inventory_value(db)
    # 未付工厂结算: 逐单明细
    unpaid_fos = db.execute(
        select(FactoryOrder).where(
            FactoryOrder.payment_status == "unpaid",
            FactoryOrder.voided_at.is_(None),
        ).order_by(FactoryOrder.factory_bill_amount.desc().nulls_last()).limit(30)
    ).scalars().all()
    # 订单累计利润: 贡献最大/最小的单 (公式 B 侧异常常在这); 口径同 _total_order_profit
    from app.services import order_financials as ofin, sales_analytics
    coef = ofin.load_coefficients(db)
    as_by_order = ofin.extra_aftersales_by_order(db)
    orders = db.execute(
        select(Order).where(
            sales_analytics.settled_sale_clause(),
            Order.is_refill == False,  # noqa: E712
        )
    ).scalars().all()
    contrib = []
    for o in orders:
        revenue = Decimal(str(o.paid_amount or 0)) - Decimal(str(o.refund_amount or 0))
        cost = ofin.accounting_cost(o, coef, aftersales=Decimal(str(as_by_order.get(o.order_no, 0))))
        contrib.append({"order_no": o.order_no, "profit": float(revenue - cost),
                        "paid": float(o.paid_amount or 0), "cost": float(cost)})
    contrib.sort(key=lambda x: x["profit"])
    return {
        "库存账面明细": inv_detail[:50],
        "成品库存明细": product_inv_detail[:50],
        "未付工厂结算明细": [
            {"factory_order_no": f.factory_order_no, "factory_name": f.factory_name,
             "amount": float(f.factory_bill_amount or 0),
             "expected_delivery": str(f.expected_delivery or "")}
            for f in unpaid_fos
        ],
        "订单利润_最亏TOP20": contrib[:20],
        "订单利润_最赚TOP20": list(reversed(contrib[-20:])),
        "近30天未核销流水": unmatched_recent_flows(db, days=30)[:50],
    }


# ----------------------------- 未核销异常池 (业务需求 19) -------- #


def unmatched_recent_flows(db: Session, *, days: int = 7) -> list[dict]:
    """业务需求 19: 公式差额可能藏在哪 — 最近 N 天 reconciliation_status='open' 的流水."""
    cutoff = date.today() - timedelta(days=days)
    rows = db.execute(
        select(AlipayFlow).where(
            AlipayFlow.reconciliation_status == "open",
            AlipayFlow.transaction_time >= cutoff,
        ).order_by(AlipayFlow.transaction_time.desc()).limit(200)
    ).scalars().all()
    return [
        {
            "id": r.id,
            "transaction_no": r.transaction_no,
            "transaction_time": r.transaction_time.isoformat() if r.transaction_time else None,
            "amount": float(r.amount),
            "counterparty": r.counterparty,
            "transaction_type": r.transaction_type,
            "remark": r.remark,
        }
        for r in rows
    ]


def check_formula_and_alert(db: Session) -> dict:
    """定时任务调: 算公式差额, > 100 元生成 Alert."""
    from app.services import alert_service
    s = summary(db)
    abs_diff = abs(s.diff)
    if abs_diff > Decimal("100"):
        unmatched = unmatched_recent_flows(db, days=7)
        alert_service.upsert(
            db,
            kind="finance_mismatch",
            severity="warn",
            title=f"账务公式差额 {s.diff:.2f} 元",
            body=(f"A (账面) {s.formula_a:.2f} vs B (订单+余额) {s.formula_b:.2f} = {s.diff:.2f}. "
                  f"最近 7 天 {len(unmatched)} 条未核销流水可能藏着差额."),
            dedupe_key="finance_mismatch:weekly",
            related_url="/finance/reconciliation",
            context={"diff": float(s.diff), "unmatched_count": len(unmatched)},
            auto_resolve_after_minutes=60 * 24,  # 一天后过期 (下次再算一遍)
        )
    return {"diff": float(s.diff), "abs_diff": float(abs_diff),
            "alerted": abs_diff > Decimal("100")}
