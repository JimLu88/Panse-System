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
from app.models.order import Order
from app.models.product import Product
from app.services import exception_service


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
    rows = db.execute(select(Order).where(Order.product_code.is_not(None))).scalars().all()
    for o in rows:
        if o.product_code and o.product_code not in existing_codes:
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

def scan_duplicate_alipay_cross_account(db: Session) -> list[ScanFinding]:
    """同一 transaction_no 在多个账户出现 → 多半是数据录入错误。"""
    out: list[ScanFinding] = []
    from sqlalchemy import func
    dup_q = (
        select(AlipayFlow.transaction_no, func.count(AlipayFlow.id).label("n"))
        .group_by(AlipayFlow.transaction_no)
        .having(func.count(AlipayFlow.id) > 1)
    )
    for tx_no, n in db.execute(dup_q).all():
        accounts = [a for (a,) in db.execute(
            select(AlipayFlow.account).where(AlipayFlow.transaction_no == tx_no)
        ).all()]
        out.append(ScanFinding(
            source_table="alipay_flows",
            source_pk=tx_no,
            exception_type="duplicate_alipay_flow",
            severity="warning",
            description=f"支付宝流水号 {tx_no} 在 {n} 个账户出现: {accounts}",
            suggestion_action="merge_or_delete_duplicates",
            context={"transaction_no": tx_no, "accounts": accounts, "count": n},
        ))
    return out


# -------- 注册表 + 跑器 --------

SCANNERS: dict[str, Callable[[Session], list[ScanFinding]]] = {
    "dangling_order_product": scan_dangling_order_product,
    "negative_inventory": scan_negative_inventory,
    "numeric_range": scan_numeric_range,
    "date_logic": scan_date_logic,
    "missing_custom_price": scan_missing_custom_price,
    "duplicate_alipay_cross_account": scan_duplicate_alipay_cross_account,
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
