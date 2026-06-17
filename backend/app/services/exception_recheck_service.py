# -*- coding: utf-8 -*-
"""异常复核 (用户拍板 2026-06-12): 点「已处理」时先检查问题是否真的修好了。

按 exception_type 注册检查器:
    返回 None  = 问题已不存在, 可以销账
    返回 字符串 = 问题仍存在的原因, 拒绝销账并把原因告诉用户
没有检查器的类型 → 不拦 (人工判断为准)。force=True 跳过复核 (强制)。
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.exception import DataException


def _check_bom_product_collision(db: Session, exc: DataException) -> Optional[str]:
    """SKU 编码挂多产品: 修好 = bom_lines 里该 SKU 只剩 1 个产品。"""
    from app.models.bom import BomLine
    sku_code = (exc.context or {}).get("sku_code") or exc.source_pk
    if not sku_code:
        return None
    rows = db.execute(
        select(BomLine.product_code).where(BomLine.sku_code == sku_code).distinct()
    ).scalars().all()
    if len(rows) > 1:
        return f"SKU {sku_code} 仍挂着 {len(rows)} 个产品 ({'/'.join(rows[:4])}), 请先删多余产品的 BOM 行或改正 SKU 编码"
    return None


def _check_import_missing(db: Session, exc: DataException) -> Optional[str]:
    """导入消失: 「已处理」= 你确认删除并已删掉该记录。还在库里就不能算处理完。"""
    key = exc.source_pk
    if not key:
        return None
    if exc.source_table == "orders":
        from app.models.order import Order
        n = db.execute(select(func.count(Order.id)).where(Order.order_no == key)).scalar() or 0
        if n:
            return f"订单 {key} 还在库里 — 确认要删请先到订单页删除; 不删 (误报/拆单) 请用「强制忽略」"
    elif exc.source_table == "products":
        from app.models.product import Product
        n = db.execute(select(func.count(Product.id)).where(Product.code == key)).scalar() or 0
        if n:
            return f"产品 {key} 还在库里 — 确认要删请先到产品总表删除; 要保留请用「强制忽略」"
    return None


def _check_material_name_conflict(db: Session, exc: DataException) -> Optional[str]:
    """同料号两边名字不同: 修好 = 物料库与 BOM 名字一致 (或一侧改为占位)。"""
    from app.models.bom import BomLine
    from app.models.material import Material
    code = exc.source_pk
    if not code:
        return None
    mat = db.execute(select(Material.name).where(Material.code == code)).scalar_one_or_none()
    bom_names = {
        (n or "").strip() for n in db.execute(
            select(BomLine.material_name).where(
                BomLine.material_code == code, BomLine.material_name.isnot(None))
        ).scalars().all()
    }
    bad = {n for n in bom_names if n and not n.startswith("占位") and n != (mat or "").strip()}
    if mat and bad:
        return f"料号 {code} 在 BOM 里仍有不同名字: {'/'.join(list(bad)[:3])} (物料库为「{mat}」)"
    return None


def _check_material_placeholder(db: Session, exc: DataException) -> Optional[str]:
    """物料占位未正名: 修好 = 物料库名不再以「占位」开头 (或物料已删)。"""
    from app.models.material import Material
    code = exc.source_pk
    if not code:
        return None
    mat = db.execute(select(Material.name).where(Material.code == code)).scalar_one_or_none()
    if mat is None:
        return None   # 物料已删 → 不存在 → 可销账
    if (mat or "").strip().startswith("占位"):
        return f"料号 {code} 物料库名仍是占位「{mat}」, 未正名"
    return None        # 已正名 → 可销账


def _get_order(db: Session, exc: DataException):
    """异常 source_pk 存的是 Order.id (字符串)。取不到/非数字 → None。"""
    from app.models.order import Order
    try:
        return db.get(Order, int(exc.source_pk))
    except (TypeError, ValueError):
        return None


def _check_order_missing_cost(db: Session, exc: DataException) -> Optional[str]:
    """订单缺成本: 修好 = 已填理论/实际成本 (或订单已删/取消/历史)。"""
    o = _get_order(db, exc)
    if o is None:
        return None
    if o.theoretical_cost is not None or o.actual_cost is not None:
        return None
    if (o.status or "") in ("cancelled", "pending_payment") or o.is_historical:
        return None  # 取消/未付款无成本核算需求(用户拍板2026-06-15)
    from app.services.data_quality_service import is_non_product_order, is_custom_order
    if is_non_product_order(o) or is_custom_order(o):
        return None  # 非实物(差价/样品)/定制(改/尾号≥90) 不属此类(定制归 custom_order_missing_cost_basis)
    return f"订单 {o.order_no} 理论/实际成本仍都为空"


def _check_order_missing_tracking(db: Session, exc: DataException) -> Optional[str]:
    """已发货缺物流号: 修好 = 已填物流号 (或不再是 shipped/signed)。"""
    o = _get_order(db, exc)
    if o is None:
        return None
    if o.tracking_no:
        return None
    if (o.status or "") not in ("shipped", "signed"):
        return None
    return f"订单 {o.order_no} 状态 {o.status} 仍无物流号"


def _check_cost_exceeds_paid(db: Session, exc: DataException) -> Optional[str]:
    """错配单复核: 成本已不再明显高于实付(已并单/改 actual_cost) → 销账。与 scanner 同口径。"""
    o = _get_order(db, exc)
    if o is None:
        return None
    paid = Decimal(str(o.paid_amount or 0))
    cost = Decimal(str(o.actual_cost if o.actual_cost is not None else (o.theoretical_cost or 0)))
    if paid <= 0 or cost <= 0:
        return None
    if cost <= paid * Decimal("1.5") or (cost - paid) < Decimal("300"):
        return None
    return f"订单 {o.order_no} 实付 ¥{paid} 仍背成本 ¥{cost}, 错配未解决"


def _check_order_missing_alipay(db: Session, exc: DataException) -> Optional[str]:
    """订单缺收款记录: 修好(销账) = 已成交单有了支付宝流水或聚合结算关联; 或本就不该要收款
    (担保交易中 paid/shipped、退款 aftersales、取消、待付款、历史)。
    根因修(2026-06-15): 淘宝企业单走聚合批量打款, 逐单货款在聚合账单(OrderSettlement), 故聚合
    结算也算已收款; 且只有已成交(signed/completed) 才该有收款流水, 在途单不算缺。与 scanner 同口径。"""
    from sqlalchemy import func
    from app.models.finance import AlipayFlow
    from app.models.settlement import OrderSettlement
    o = _get_order(db, exc)
    if o is None:
        return None
    if o.is_historical or (o.status or "") not in ("signed", "completed", "success", "finished"):
        return None
    n = db.execute(select(func.count()).select_from(AlipayFlow)
                   .where(AlipayFlow.related_order_no == o.order_no)).scalar() or 0
    if n:
        return None
    m = db.execute(select(func.count()).select_from(OrderSettlement)
                   .where(OrderSettlement.order_no == o.order_no)).scalar() or 0
    if m:
        return None
    # 淘宝逐单『打款商家金额』有值 = 淘宝已确认放款(批量结算逐单流水不可得), 视为已收款; 总额由
    # 月度货款对账兜底。仅当连打款金额都没有才是真缺收款凭据 (2026-06-15 用户拍板)。
    if o.shop_received_amount and o.shop_received_amount > 0:
        return None
    # 非产品(安装/送货/补差价)单 + 下单14天内新单 不报缺收款 (与 scanner 同口径, 2026-06-17)
    from app.services.data_quality_service import _NON_PRODUCT_KW
    _txt = f"{o.product_name or ''} {o.sku or ''} {o.sku_code or ''}"
    if any(k in _txt for k in _NON_PRODUCT_KW):
        return None
    from datetime import date as _d, timedelta as _td
    if o.order_date and o.order_date >= _d.today() - _td(days=14):
        return None
    return f"订单 {o.order_no} 已成交却无任何收款凭据(支付宝流水/聚合结算/淘宝打款金额均无)"


def _check_refill_unmatched(db: Session, exc: DataException) -> Optional[str]:
    """补单订单号找不到: 修好 = 该订单号现已在订单总表 (或补单记录已删)。"""
    from sqlalchemy import func
    from app.models.finance import RefillRecord
    from app.models.order import Order
    try:
        r = db.get(RefillRecord, int(exc.source_pk))
    except (TypeError, ValueError):
        return None
    if r is None:
        return None
    n = db.execute(select(func.count()).select_from(Order)
                   .where(Order.order_no == r.order_no)).scalar() or 0
    if n:
        return None
    return f"补单 {exc.source_pk} 订单号 {r.order_no} 仍找不到对应订单"


def _check_alipay_flow_no_missing(db: Session, exc: DataException) -> Optional[str]:
    """缺支付宝流水号(售后): 修好 = 流水号已填; 或关联订单交易关闭(cancelled)/未付款
    (pending_payment)——客户下单直接退款, 本就不会产生支付宝流水 (用户拍板 2026-06-15)。
    非 after_sales 来源(如 outsourcing_expenses 外包私账)无可靠复核键 → 保留人工处理。"""
    from app.models.marketing import AfterSales
    from app.models.order import Order
    if exc.source_table != "after_sales":
        return f"{exc.source_pk} 仍缺支付宝流水号(人工处理)"
    from datetime import date as _date
    o = db.execute(select(Order).where(Order.order_no == exc.source_pk)).scalar_one_or_none()
    # 原订单已删(多为2025清理) 或 下单在2026以前 → 不再要求流水 (用户拍板 2026-06-15: 2026以前不管)
    if o is None or (o.order_date and o.order_date < _date(2026, 1, 1)):
        return None
    if (o.status or "") in ("cancelled", "pending_payment"):
        return None  # 交易关闭/未付款 → 本就无流水
    rows = db.execute(
        select(AfterSales).where(AfterSales.platform_order_no == exc.source_pk)).scalars().all()
    if not rows:
        return None  # 售后记录已不在
    if all((r.alipay_flow_no or "").strip() for r in rows):
        return None  # 都已回填
    # 无任何额外赔付付款(纯退款在原单已完成) → 不会产生新支付宝流水, 不需流水号 (用户拍板 2026-06-15)
    _PAYOUT = ("compensation_fee", "direct_compensation", "out_platform_total", "in_platform_total",
               "second_visit_fee", "return_pack_freight", "good_review_refund",
               "factory_compensation", "logistics_compensation")
    if not any(any((getattr(r, f, None) or 0) > 0 for f in _PAYOUT) for r in rows):
        return None
    return f"售后单 {exc.source_pk} (有额外赔付) 仍缺支付宝流水号"


def _check_refill_record_missing(db: Session, exc: DataException) -> Optional[str]:
    """订单标补单但补单表无记录: 修好 = 该订单号现在补单表里有记录了
    (或订单已不在/不再标补单/已取消)。source_pk = 订单号(字符串)。

    注: 此类异常由导入时(_h_order)创建, 之前无复核器 → 即便补单记录补上也不会自动销账,
    导致大量 stale 误报(实测 31/93 其实已有补单记录)。补上复核器。"""
    from sqlalchemy import func
    from app.models.finance import RefillRecord
    from app.models.order import Order
    o = db.execute(select(Order).where(Order.order_no == exc.source_pk)).scalar_one_or_none()
    if o is None:
        return None  # 订单已不在
    if not getattr(o, "is_refill", False):
        return None  # 不再标补单
    if (o.status or "") == "cancelled":
        return None
    n = db.execute(select(func.count()).select_from(RefillRecord)
                   .where(RefillRecord.order_no == exc.source_pk)).scalar() or 0
    if n:
        return None  # 补单记录已存在 → 已修好
    return f"订单 {exc.source_pk} 标补单但补单表仍无记录"


def _check_factory_order_uncovered(db: Session, exc: DataException) -> Optional[str]:
    """已发货有成本但无工厂单: 修好 = 已有有效工厂单 (或不再发货态/无成本/历史)。"""
    # 用户拍板 2026-06-15: 此异常默认关闭(系统自动建工厂单、无手工录入), 关闭时一律判为已解决→销账。
    try:
        from app.services import settings_service as _ss
        _on = str(_ss.get(db, "factory_order_uncovered_check", env_fallback=False) or "").strip().lower() \
            in ("1", "true", "yes", "on")
    except Exception:
        _on = False
    if not _on:
        return None
    from sqlalchemy import func
    from app.models.order import FactoryOrder
    o = _get_order(db, exc)
    if o is None:
        return None
    # 补单(is_refill)是补发/重发, 不需新工厂下单 → 销账 (与 scanner 同口径, 用户拍板 2026-06-15)
    if (o.status or "") not in ("shipped", "signed") or o.is_historical or getattr(o, "is_refill", False):
        return None
    if o.theoretical_cost is None and o.actual_cost is None:
        return None
    n = db.execute(select(func.count()).select_from(FactoryOrder).where(
        FactoryOrder.platform_order_no == o.order_no, FactoryOrder.voided_at.is_(None))).scalar() or 0
    if n:
        return None
    return f"订单 {o.order_no} 仍无有效工厂下单记录"


def _check_promotion_recharge_unmatched(db: Session, exc: DataException) -> Optional[str]:
    """推广充值缺流水号: 修好 = 已填 alipay_flow_no (或不再是充值)。"""
    from app.models.marketing import PromotionFlow
    try:
        r = db.get(PromotionFlow, int(exc.source_pk))
    except (TypeError, ValueError):
        return None
    if r is None or r.flow_type != "充值" or r.alipay_flow_no:
        return None
    return f"推广充值 {exc.source_pk} 仍缺支付宝流水号"


def _check_custom_order_missing_cost_basis(db: Session, exc: DataException) -> Optional[str]:
    """定制单缺成本基准: 修好 = 已填实际成本 或 定制加价 (或不再是定制单/取消)。"""
    o = _get_order(db, exc)
    if o is None:
        return None
    # 补单/刷单不该挂"缺成本"(¥0成本是正常的)→ 后来被判为补单的自动销账 (用户拍板 2026-06-17)
    if getattr(o, "is_refill", False):
        return None
    if o.actual_cost is not None or o.custom_surcharge is not None:
        return None
    # 已有推演成本(定制单核对自动写回 theoretical_cost, 含成本=0) → 已能核算 → 销账 (用户拍板 2026-06-17)
    if o.theoretical_cost is not None:
        return None
    if (o.status or "") == "cancelled":
        return None
    if not (o.is_custom or (o.sku_code or "").endswith("改")):
        return None
    return f"定制订单 {o.order_no} 仍无实际成本/定制加价"


def _check_missing_taobao_mapping(db: Session, exc: DataException) -> Optional[str]:
    """产品缺淘宝商品ID: 修好 = 已填 taobao_id (或产品已删)。"""
    from app.models.product import Product
    p = db.execute(select(Product).where(Product.code == exc.source_pk)).scalar_one_or_none()
    if p is None:
        return None
    if (getattr(p, "listing_status", None) or "") == "下架":
        return None  # 下架产品(未上架/未生产)不报缺淘宝映射
    from app.services.data_quality_service import is_non_sellable_product
    if is_non_sellable_product(p):
        return None  # 作废/定制/安装/送货/样品 等非卖品, 本就没上架
    if getattr(p, "taobao_id", None):
        return None
    return f"产品 {exc.source_pk} 仍缺淘宝商品ID"


def _check_alipay_duplicate_flow(db: Session, exc: DataException) -> Optional[str]:
    """支付宝重复流水: 修好(销账)= 不再有"同账户+同业务流水号+同类型+同金额"的其它流水。
    根因修(2026-06-15): 业务流水号会被多笔不同交易复用, 判重必须连金额一起比, 否则把"复用同号
    的不同金额交易"误报成重复 (与导入去重键 (no,type,amount) 一致)。流水已删也销账。"""
    from sqlalchemy import func
    from app.models.finance import AlipayFlow
    try:
        f = db.get(AlipayFlow, int(exc.source_pk))
    except (TypeError, ValueError):
        return None
    if f is None:
        return None
    n = db.execute(select(func.count()).select_from(AlipayFlow).where(
        AlipayFlow.account == f.account,
        AlipayFlow.transaction_no == f.transaction_no,
        AlipayFlow.transaction_type == f.transaction_type,
        AlipayFlow.amount == f.amount,
        AlipayFlow.id != f.id,
    )).scalar() or 0
    if n:
        return f"流水 {f.id} 仍与其它 {n} 条完全重复(同号+类型+金额)"
    return None


def _check_alipay_balance_gap(db: Session, exc: DataException) -> Optional[str]:
    """支付宝余额断链: 修好(销账) = 该流水"前驱余额"(本余额-本金额)现在能对上本账户某条流水
    的余额(同秒/乱序重排后链条接上了), 或本条是窗口最早一条, 或流水已删。与 scanner 同口径
    (前驱余额法, 2026-06-15 根因修: 旧"按时间相邻比"对企业号同秒多笔大量误报, 流水其实不缺)。"""
    from decimal import Decimal
    from app.models.finance import AlipayFlow
    try:
        f = db.get(AlipayFlow, int(exc.source_pk))
    except (TypeError, ValueError):
        return None
    if f is None or f.balance is None:
        return None
    bal_set = {b for (b,) in db.execute(select(AlipayFlow.balance).where(
        AlipayFlow.account == f.account, AlipayFlow.balance.isnot(None))).all()}
    earliest = db.execute(select(AlipayFlow.id).where(
        AlipayFlow.account == f.account, AlipayFlow.balance.isnot(None)
    ).order_by(AlipayFlow.transaction_time.asc(), AlipayFlow.id.asc()).limit(1)).scalar()
    pred = f.balance - (f.amount or Decimal("0"))
    if pred in bal_set or f.id == earliest:
        return None
    # 与 scanner 同口径: ≤¥0.5 的差是支付宝手续费配对噪声, 不算断链 (2026-06-17)
    if any(abs(pred - b) <= Decimal("0.5") for b in bal_set):
        return None
    return f"流水 {f.id} 余额断链 (前驱应为 ¥{pred}, 无对应流水)"


_CHECKERS: dict[str, Callable[[Session, DataException], Optional[str]]] = {
    "alipay_duplicate_flow": _check_alipay_duplicate_flow,
    "alipay_balance_gap": _check_alipay_balance_gap,
    "bom_product_collision": _check_bom_product_collision,
    "import_missing": _check_import_missing,
    "material_name_conflict": _check_material_name_conflict,
    "material_placeholder": _check_material_placeholder,
    "order_missing_cost": _check_order_missing_cost,
    "cost_exceeds_paid": _check_cost_exceeds_paid,
    "order_missing_tracking": _check_order_missing_tracking,
    "order_missing_alipay": _check_order_missing_alipay,
    "alipay_flow_no_missing": _check_alipay_flow_no_missing,
    "refill_unmatched": _check_refill_unmatched,
    "refill_record_missing": _check_refill_record_missing,
    "factory_order_uncovered": _check_factory_order_uncovered,
    "promotion_recharge_unmatched": _check_promotion_recharge_unmatched,
    "custom_order_missing_cost_basis": _check_custom_order_missing_cost_basis,
    "missing_taobao_mapping": _check_missing_taobao_mapping,
}


def recheck(db: Session, exc: DataException) -> Optional[str]:
    """复核一条异常。返回 None=可销账; 字符串=仍存在的原因。无检查器 → None。"""
    fn = _CHECKERS.get(exc.exception_type)
    if fn is None:
        return None
    try:
        return fn(db, exc)
    except Exception:  # pragma: no cover - 复核器故障不拦人工操作
        return None


def bulk_close_resolved(db: Session, *, types: Optional[list[str]] = None) -> dict[str, int]:
    """批量销账: 只对「有检查器」的异常类型重跑复核, 把条件已不成立(已修复)的置 resolved。
    没检查器的类型一律不动 (留人工逐条判断)。返回 {类型: 关闭数}。"""
    from collections import Counter
    from datetime import datetime

    use_types = [t for t in (types or list(_CHECKERS)) if t in _CHECKERS]
    if not use_types:
        return {}
    rows = db.execute(
        select(DataException).where(
            DataException.status == "open",
            DataException.exception_type.in_(use_types),
        )
    ).scalars().all()
    closed: Counter = Counter()
    now = datetime.now().isoformat(timespec="seconds")
    for exc in rows:
        if recheck(db, exc) is None:        # 条件已不成立 = 已修复
            exc.status = "resolved"
            exc.resolved_by = "系统复核(批量)"
            exc.resolved_at = now
            closed[exc.exception_type] += 1
    db.commit()
    return dict(closed)
