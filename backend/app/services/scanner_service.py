"""异常扫描引擎 (plan §6.2 定时全量扫描)。

每个扫描器是一个函数，返回 list[ScanFinding]。统一通过 run_scanner / run_all 跑。
扫描器内部对每条 finding 调 exception_service.record() 写入 data_exceptions，
**带去重**：相同 (source_table, source_pk, exception_type, status=open) 不重复落表。

可单独 run_dry()，仅返回发现不写库；方便前端预览。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.exception import DataException
from app.models.finance import AlipayFlow, RefillRecord
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.order import Order, PartPurchase
from app.models.product import Product
from app.services import exception_service
from app.services.sku_utils import get_threshold, is_custom_sku_code


@dataclass
class ScanFinding:
    source_table: str
    source_pk: str
    exception_type: str
    severity: str
    description: str
    suggestion_action: str
    context: dict


@dataclass
class ScannerResult:
    scanner: str
    findings: list[ScanFinding]
    written: int
    skipped_duplicate: int


def _exists_open_exception(db: Session, *, source_table: str, source_pk: str, exception_type: str) -> bool:
    q = select(DataException.id).where(
        and_(
            DataException.source_table == source_table,
            DataException.source_pk == source_pk,
            DataException.exception_type == exception_type,
            DataException.status == "open",
        )
    )
    return db.execute(q).first() is not None


# -------- Scanner 1: 外键引用断裂 — 订单引用未知 product_code --------

def scan_dangling_order_product(db: Session) -> list[ScanFinding]:
    out: list[ScanFinding] = []
    existing_codes = {c for (c,) in db.execute(select(Product.code)).all()}
    threshold = get_threshold(db)
    rows = db.execute(select(Order).where(Order.product_code.is_not(None))).scalars().all()
    for o in rows:
        if (o.status or "") == "cancelled":
            continue  # 交易关闭单无收入/不生产, 产品编码坏不坏无所谓, 不报 (用户 2026-06-22)
        if o.product_code and o.product_code not in existing_codes:
            # 定制单(数字尾号≥90 / 「改」后缀)用的就是不在产品总表里的定制编码, 非"产品缺档", 跳过 (用户 2026-06-24)
            sku_for_check = getattr(o, "sku_code", None) or o.product_code
            if is_custom_sku_code(sku_for_check, threshold=threshold):
                continue
            out.append(ScanFinding(
                source_table="orders",
                source_pk=o.order_no,
                exception_type="dangling_product_code",
                severity="error",
                description=f"订单 {o.order_no} 引用了不存在的产品编码 {o.product_code}",
                suggestion_action="create_product_or_fix_code",
                context={"order_no": o.order_no, "product_code": o.product_code, "product_name": o.product_name},
            ))
    return out


# -------- Scanner 2: 库存负数 — 物理 - 锁定 < 0 --------

def scan_negative_inventory(db: Session) -> list[ScanFinding]:
    out: list[ScanFinding] = []
    for inv in db.execute(select(PartInventory)).scalars():
        avail = int(inv.physical_qty or 0) - int(inv.locked_qty or 0)
        if avail < 0:
            out.append(ScanFinding(
                source_table="part_inventory",
                source_pk=f"PART#{inv.id}",
                exception_type="negative_inventory",
                severity="error",
                description=f"配件库存 #{inv.id} ({inv.material_code}) 可用={avail} (物理 {inv.physical_qty} - 锁定 {inv.locked_qty})",
                suggestion_action="reconcile_inbound_outbound_order",
                context={"warehouse": inv.warehouse, "material_code": inv.material_code, "physical": inv.physical_qty, "locked": inv.locked_qty},
            ))
    for inv in db.execute(select(ProductInventory)).scalars():
        avail = int(inv.physical_qty or 0) - int(inv.locked_qty or 0)
        if avail < 0:
            out.append(ScanFinding(
                source_table="product_inventory",
                source_pk=f"PROD#{inv.id}",
                exception_type="negative_inventory",
                severity="error",
                description=f"成品库存 #{inv.id} ({inv.product_code}) 可用={avail}",
                suggestion_action="reconcile_inbound_outbound_order",
                context={"warehouse": inv.warehouse, "product_code": inv.product_code, "physical": inv.physical_qty, "locked": inv.locked_qty},
            ))
    return out


# -------- Scanner 3: 数值范围异常 — 物料价格 <= 0 --------

def scan_numeric_range(db: Session) -> list[ScanFinding]:
    out: list[ScanFinding] = []
    rows = db.execute(
        select(Material).where(Material.price.is_not(None)).where(Material.price <= 0)
    ).scalars().all()
    for m in rows:
        out.append(ScanFinding(
            source_table="materials",
            source_pk=m.code,
            exception_type="non_positive_price",
            severity="warning",
            description=f"物料 {m.code} 单价 = {m.price}，应大于 0",
            suggestion_action="correct_price",
            context={"code": m.code, "name": m.name, "price": str(m.price)},
        ))
    return out


# -------- Scanner 4: 日期逻辑异常 — 发货早于下单 --------

def scan_date_logic(db: Session) -> list[ScanFinding]:
    out: list[ScanFinding] = []
    rows = db.execute(
        select(Order).where(Order.ship_date.is_not(None)).where(Order.order_date.is_not(None))
    ).scalars().all()
    for o in rows:
        if o.ship_date and o.order_date and o.ship_date < o.order_date:
            out.append(ScanFinding(
                source_table="orders",
                source_pk=o.order_no,
                exception_type="ship_before_order",
                severity="warning",
                description=f"订单 {o.order_no}: 发货日期 {o.ship_date} 早于下单日期 {o.order_date}",
                suggestion_action="confirm_correct_dates",
                context={"order_no": o.order_no, "order_date": str(o.order_date), "ship_date": str(o.ship_date)},
            ))
    return out


# -------- Scanner 5: 必填字段缺失 — 定制物料没价格 --------

def scan_missing_custom_price(db: Session) -> list[ScanFinding]:
    out: list[ScanFinding] = []
    rows = db.execute(
        select(Material).where(Material.is_custom == True).where(Material.price.is_(None))  # noqa: E712
    ).scalars().all()
    for m in rows:
        out.append(ScanFinding(
            source_table="materials",
            source_pk=m.code,
            exception_type="custom_material_missing_price",
            severity="warning",
            description=f"定制物料 {m.code} ({m.name}) 没填价格，无法计入对账与报价",
            suggestion_action="fill_material_fields",
            context={"code": m.code, "name": m.name, "unit": m.unit},
        ))
    return out


# -------- Scanner 6: 重复 alipay 流水 (account 内 transaction_no 已唯一约束，但跨账户可能重复) --------

def _is_benign_cross_account_dup(flows) -> bool:
    """跨账户同交易号是否为"良性"(内部流转/店铺过户分账, 非录入错误), flows=该交易号全部流水。

    ① 内部转账: 金额一出一入(混正负), 或标 internal_transfer, 或对方是我方账户主人
       (魏佳英/Klossy·Lee 等账户之间挪钱, 同一笔在两账户各记收/支一面);
    ② 店铺过户分账/同单: 各账户共享同一订单号(老店铺收款 + 过户后新店铺分账, 或同单退款)。
    实测 120 笔跨账户"重复" = 30 内部转账 + 90 同订单, 真录入重复 0 笔。
    """
    from app.services import internal_accounts
    from app.services.smart_matching_service import _order_key
    amts = [float(f.amount or 0) for f in flows]
    if any(a > 0 for a in amts) and any(a < 0 for a in amts):
        return True
    if any((getattr(f, "reconciliation_type", None) == "internal_transfer") for f in flows):
        return True
    if any(internal_accounts.is_internal_counterparty(getattr(f, "counterparty", None)) for f in flows):
        return True
    okeys = {_order_key(getattr(f, "related_order_no", None)) for f in flows if getattr(f, "related_order_no", None)}
    okeys.discard(None)
    if len(okeys) == 1:   # 各账户同一订单 → 店铺过户分账/同单
        return True
    return False


def scan_duplicate_alipay_cross_account(db: Session) -> list[ScanFinding]:
    """同一 transaction_no 出现在「多个不同账户」→ 可能是数据录入错误。

    注意: 同一账户内同一流水号出现多条是正常的 —— 一笔淘宝收款会产生
    『在线支付(收款) + 分账(手续费)』两条共用同一流水号的配对流水。
    因此这里必须按「不同账户数」判重 (count(distinct account) > 1),
    而不是按行数, 否则会把正常配对流水误报成重复。
    同账户内的真重复由 data_quality_service.scan_alipay_duplicate_flow 负责。
    2026-06-24: 跨账户同号若是内部流转/店铺过户分账(_is_benign_cross_account_dup)→ 不报。
    """
    out: list[ScanFinding] = []
    from sqlalchemy import func
    dup_q = (
        select(
            AlipayFlow.transaction_no,
            func.count(func.distinct(AlipayFlow.account)).label("n_acct"),
        )
        .group_by(AlipayFlow.transaction_no)
        .having(func.count(func.distinct(AlipayFlow.account)) > 1)
    )
    for tx_no, n_acct in db.execute(dup_q).all():
        flows = db.execute(
            select(AlipayFlow.account, AlipayFlow.amount, AlipayFlow.reconciliation_type,
                   AlipayFlow.counterparty, AlipayFlow.related_order_no)
            .where(AlipayFlow.transaction_no == tx_no)
        ).all()
        if _is_benign_cross_account_dup(flows):
            continue   # 内部流转/店铺过户分账 → 不报
        accounts = sorted({f.account for f in flows})
        out.append(ScanFinding(
            source_table="alipay_flows",
            source_pk=tx_no,
            exception_type="duplicate_alipay_flow",
            severity="warning",
            description=f"支付宝流水号 {tx_no} 在 {n_acct} 个不同账户出现: {accounts}",
            suggestion_action="merge_or_delete_duplicates",
            context={"transaction_no": tx_no, "accounts": accounts, "account_count": n_acct},
        ))
    return out


# -------- Scanner: 木作工厂月结账单未付清 (用户 2026-07-01) --------

def scan_factory_bill_unpaid(db: Session) -> list[ScanFinding]:
    """木作供应商(博冠)各结算月「已开账单未付清」→ 逐月提醒。

    付清后在「工厂月结销账」点该月「已付清」(或支付宝备注「X月已付清」自动销账),
    本异常由 _check_factory_bill_unpaid 自动复核销账。source_pk = 'supplier|YYYY-MM'。
    """
    from app.services import factory_settlement_service as fss
    out: list[ScanFinding] = []
    bd = fss.month_breakdown(db)
    for m in bd["months"]:
        if Decimal(str(m["unpaid"])) <= 0:
            continue
        out.append(ScanFinding(
            source_table="factory_settlement",
            source_pk=f'{bd["supplier"]}|{m["month"]}',
            exception_type="factory_bill_unpaid",
            severity="info",
            description=(f'{bd["supplier"]} {m["month"]} 工厂账单未付清: 未付 ¥{m["unpaid"]}'
                        f'（{m["order_count"]}单, 状态 {m["status"]}）'),
            suggestion_action="付清后在「工厂月结销账」点该月「已付清」, 或支付宝备注「X月已付清」自动销账",
            context={"supplier": bd["supplier"], "month": m["month"], "unpaid": str(m["unpaid"]),
                     "order_count": m["order_count"], "status": m["status"]},
        ))
    return out


# -------- 注册表 + 跑器 --------

# -------- Scanner 8: 配件采购↔物料库 价差预警 (用户 2026-07-08 安全版: 匹配+预警, 绝不自动改标准价) --------
_PURCHASE_VARIANCE_PCT = 20.0   # 采购单价 vs 物料库标准价 偏离阈值(%)


def _match_purchase_material(p: PartPurchase, by_code: dict, by_name: dict):
    m = by_code.get((p.material_code or "").strip()) if p.material_code else None
    # 名字一致性护栏 (2026-07-10): 支付宝流水自动建的采购单, 编码是 difflib 模糊猜的
    # (alipay_flow_router._guess_material), 实测"卓晔五金定金/挚乐轨道灯带/样品玻璃"全被猜成
    # AC-1006 榉木床头柜金属侧板 → 价差体检拿错挂的标准价比出 +1889% 假偏离。编码命中但采购单
    # 自己写的名字与物料名毫不相干 → 当匹配不上(不比价/不许把错料标准价写回)。
    if m is not None and p.material_name and m.name:
        _pn = p.material_name.strip().lower()
        _mn = m.name.strip().lower()
        if _mn not in _pn and _pn not in _mn:
            m = None
    if m is None and p.material_name:
        m = by_name.get(p.material_name.strip())
    return m


def scan_purchase_price_outliers(db: Session) -> list[ScanFinding]:
    """配件采购单价 vs 物料库标准价 偏离≥阈值 → 预警(近180天有单价的采购)。绝不自动改标准价。"""
    from datetime import timedelta
    mats = db.execute(select(Material)).scalars().all()
    by_code = {m.code: m for m in mats if m.code}
    by_name: dict = {}
    for m in mats:
        if m.name:
            by_name.setdefault(m.name.strip(), m)
    since = date.today() - timedelta(days=180)
    out: list[ScanFinding] = []
    for p in db.execute(select(PartPurchase).where(PartPurchase.unit_price.is_not(None))).scalars():
        if (p.purchase_date and p.purchase_date < since) or not p.unit_price or p.unit_price <= 0:
            continue
        m = _match_purchase_material(p, by_code, by_name)
        if m is None or m.price is None or m.price <= 0:
            continue   # 匹配不上/无标准价 → 不预警(只核有标准价的)
        if m.unit and any(u in str(m.unit) for u in ("平", "米")):
            continue   # 按面积/长度计价(岩板/玻璃/线): 采购按块/根不可直接比单价 → 跳过防误报(待接入面积后精算)
        std, buy = Decimal(m.price), Decimal(p.unit_price)
        pct = float((buy - std) / std * 100)
        if abs(pct) >= _PURCHASE_VARIANCE_PCT:
            out.append(ScanFinding(
                source_table="part_purchases",
                source_pk=p.purchase_no,
                exception_type="purchase_price_variance",
                severity="warning",
                description=(f"采购 {p.purchase_no} {m.name}({m.code}) 采购价¥{buy} vs 物料库标准¥{std} "
                            f"偏离{pct:+.0f}% — 请核对(改标准价需人工确认)"),
                suggestion_action="review_material_price",
                context={"purchase_no": p.purchase_no, "material_code": m.code,
                         "buy_price": float(buy), "std_price": float(std), "variance_pct": round(pct, 1)},
            ))
    return out


def apply_purchase_price(db: Session, purchase_no: str, *, by: Optional[str] = None) -> dict:
    """人工确认: 把某采购单价写成该料物料库标准价 (仅显式调用, 绝不自动)。返回新旧价。"""
    p = db.execute(select(PartPurchase).where(PartPurchase.purchase_no == purchase_no)).scalar_one_or_none()
    if p is None or not p.unit_price:
        return {"ok": False, "error": "采购单不存在或无单价"}
    mats = db.execute(select(Material)).scalars().all()
    by_code = {m.code: m for m in mats if m.code}
    by_name = {m.name.strip(): m for m in mats if m.name}
    m = _match_purchase_material(p, by_code, by_name)
    if m is None:
        return {"ok": False, "error": "匹配不到物料库物料"}
    old = m.price
    m.price = Decimal(p.unit_price)
    m.remark = (f"{(m.remark or '').strip()} | 采购{purchase_no}确认改价{('·' + by) if by else ''}").strip(" |")
    db.commit()
    return {"ok": True, "material_code": m.code, "material_name": m.name,
            "old_price": float(old or 0), "new_price": float(m.price)}


SCANNERS: dict[str, Callable[[Session], list[ScanFinding]]] = {
    "dangling_order_product": scan_dangling_order_product,
    "negative_inventory": scan_negative_inventory,
    "numeric_range": scan_numeric_range,
    "date_logic": scan_date_logic,
    "missing_custom_price": scan_missing_custom_price,
    "duplicate_alipay_cross_account": scan_duplicate_alipay_cross_account,
    "factory_bill_unpaid": scan_factory_bill_unpaid,
    "purchase_price_variance": scan_purchase_price_outliers,
}


def run_scanner(db: Session, name: str, *, dry_run: bool = False) -> ScannerResult:
    fn = SCANNERS.get(name)
    if fn is None:
        raise ValueError(f"unknown scanner {name!r}; known: {list(SCANNERS)}")
    findings = fn(db)
    written = 0
    skipped = 0
    if not dry_run:
        for f in findings:
            if _exists_open_exception(
                db, source_table=f.source_table, source_pk=f.source_pk, exception_type=f.exception_type
            ):
                skipped += 1
                continue
            exception_service.record(
                db,
                source_table=f.source_table,
                source_pk=f.source_pk,
                exception_type=f.exception_type,
                severity=f.severity,
                description=f.description,
                suggestion_action=f.suggestion_action,
                context=f.context,
            )
            written += 1
        db.flush()
    return ScannerResult(scanner=name, findings=findings, written=written, skipped_duplicate=skipped)


def run_all(db: Session, *, dry_run: bool = False) -> dict[str, ScannerResult]:
    return {name: run_scanner(db, name, dry_run=dry_run) for name in SCANNERS}
