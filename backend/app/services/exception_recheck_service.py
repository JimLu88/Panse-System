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
    """错配单复核: 成本已不再明显高于实付(已并单/改 actual_cost/已取消/非产品) → 销账。

    与 scanner 同一判据 `_cost_exceeds_paid_qualifies`(避免口径漂移 + 去重复逻辑)。
    修(2026-06-23): 旧实现漏 import Decimal → NameError 被 recheck 吞成 None, 检查器形同
    虚设(resolve 复核拦不住、/recheck-all 会误关真错配)。本文件多处已本地 import data_quality。"""
    o = _get_order(db, exc)
    if o is None:
        return None
    from decimal import Decimal
    from app.services.data_quality_service import _cost_exceeds_paid_qualifies
    if not _cost_exceeds_paid_qualifies(o):
        return None
    paid = Decimal(str(o.paid_amount or 0))
    cost = Decimal(str(o.actual_cost if o.actual_cost is not None else (o.theoretical_cost or 0)))
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
        return None  # 补单也有正常收款流水, 不排除 (用户 2026-06-22 纠正)
    # 已退款单(退款≥实付)货款已退, 不该要求收款凭据 (2026-06-22)
    if o.refund_amount and o.paid_amount and o.refund_amount >= o.paid_amount:
        return None
    # 归一: 支付宝 related_order_no 常带 T200P 前缀 → 用 LIKE 匹配核心订单号 (2026-06-22)
    n = db.execute(select(func.count()).select_from(AlipayFlow)
                   .where(AlipayFlow.related_order_no.like(f"%{o.order_no}%"))).scalar() or 0
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
    # 自动关联 (用户 2026-06-24): 平台订单的退款一定在支付宝流水里 → 按平台订单号找"退款"流水回填流水号。
    # related_order_no 形如 T200P<订单号>, 故用 LIKE 含订单号匹配; 只在唯一一条退款流水时自动配, 避免错配。
    from sqlalchemy import or_
    from app.models.finance import AlipayFlow
    flows = db.execute(
        select(AlipayFlow).where(or_(
            AlipayFlow.related_order_no.like(f"%{exc.source_pk}%"),
            AlipayFlow.platform_order_no.like(f"%{exc.source_pk}%"),
        ))
    ).scalars().all()
    refunds = [f for f in flows if "退款" in (f.transaction_type or "")]
    # 同一笔退款可能有多条(原始 + 分账拆分 *<单号>_<id> 合成号), 按真实流水号(去掉 * 后缀)归并;
    # 全部归到同一笔真实退款时即可自动回填那笔真实流水号 (用户 2026-06-24)。
    bases = {(f.transaction_no or "").split("*")[0] for f in refunds}
    bases.discard("")
    if len(bases) == 1:
        txn = next(iter(bases))
        for r in rows:
            if not (r.alipay_flow_no or "").strip():
                r.alipay_flow_no = txn
        db.flush()
        return None  # 已按平台订单号自动配上退款流水, 销账
    return f"售后单 {exc.source_pk} (有额外赔付) 仍缺支付宝流水号 (订单库未找到唯一退款流水, 请人工核对)"


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


def _check_reconciliation_diff(db: Session, exc: DataException) -> Optional[str]:
    """对账差异复核(ledger_check/货款/营收等所有规则): 重算该规则, 看这条 key 的差异是否已消失
    /已对平 → 可销账。与每日对账自动关闭同口径(severity 不在 ok/not_available 即仍有差)。
    修(2026-06-23): reconciliation_diff 之前无复核器 → resolve 拦不住未对平的、/recheck-all 不关
    已修好的(对账 _record_exception 只幂等建、不自动关)。规则已摘除/老异常缺 context → 不拦留人工。"""
    ctx = exc.context or {}
    rule = ctx.get("rule")
    key = ctx.get("key")
    from app.services.reconciliation_service import RULES
    if not rule or rule not in RULES or not key:
        return None
    try:
        res = RULES[rule](db, record_exceptions=False)
    except Exception:  # pragma: no cover - 重算故障不拦人工
        return None
    for d in res.diffs:
        if d.key == key and d.severity not in ("ok", "not_available"):
            return f"对账 {rule} / {key} 仍有差异 ¥{getattr(d, 'diff', '?')}, 未对平"
    return None


def _check_duplicate_alipay_cross_account(db: Session, exc: DataException) -> Optional[str]:
    """跨账户重复流水复核: 该交易号已不再跨账户, 或判定为良性(内部流转/店铺过户分账) → 销账。
    (source_pk = 交易号; 2026-06-24 加, 配合 scanner 跳过内部流转。)"""
    from app.models.finance import AlipayFlow
    from app.services.scanner_service import _is_benign_cross_account_dup
    flows = db.execute(
        select(AlipayFlow.account, AlipayFlow.amount, AlipayFlow.reconciliation_type,
               AlipayFlow.counterparty, AlipayFlow.related_order_no)
        .where(AlipayFlow.transaction_no == exc.source_pk)
    ).all()
    accts = {f.account for f in flows}
    if len(accts) <= 1:
        return None   # 已不再跨账户
    if _is_benign_cross_account_dup(flows):
        return None   # 内部流转/店铺过户分账 = 非录入错误
    return f"交易号 {exc.source_pk} 仍在 {len(accts)} 个账户出现且非内部流转"


def _check_dangling_product_code(db: Session, exc: DataException) -> Optional[str]:
    """订单引用未知产品编码: 修好 = 编码已建档 / 是定制单(数字尾号≥90 或「改」后缀) / 订单已取消或不存在。
    (用户 2026-06-24: 尾号≥90 一律定制, 不该当"产品缺档"报错; 与 scanner 同步, 让历史误报自动销账)"""
    from app.models.order import Order
    from app.models.product import Product
    from app.services.sku_utils import get_threshold, is_custom_sku_code
    o = db.query(Order).filter(Order.order_no == exc.source_pk).first()
    if o is None or (o.status or "") == "cancelled":
        return None
    code = o.product_code
    if not code:
        return None
    sku_for_check = getattr(o, "sku_code", None) or code
    if is_custom_sku_code(sku_for_check, threshold=get_threshold(db)):
        return None  # 定制单用的就是定制编码, 非"产品缺档"
    exists = db.query(Product.code).filter(Product.code == code).first() is not None
    return None if exists else f"订单 {o.order_no} 仍引用不存在的产品编码 {code}"


def _check_factory_recon_incomplete(db: Session, exc: DataException) -> Optional[str]:
    """工厂对账缺字段: 现版本只在缺 bill_amount 时报 (paid_amount/支付流水号 2026-06-17 起已不报)。
    修好 = 记录已删 / bill_amount 已补。只缺 paid_amount/流水号的旧异常 → 直接销账 (用户 2026-06-24)。"""
    from app.models.finance import FactoryReconciliation
    try:
        rid = int(exc.source_pk)
    except (TypeError, ValueError):
        return None
    r = db.query(FactoryReconciliation).filter(FactoryReconciliation.id == rid).first()
    if r is None or r.bill_amount is not None:
        return None
    return "工厂对账仍缺账单金额(bill_amount)"


def _check_factory_recon_pending_delivery(db: Session, exc: DataException) -> Optional[str]:
    """遗留异常类型: 现版本已不再产生 factory_recon_pending_delivery
    (在产/未发货待付不再单独报)。一律可销账 (用户 2026-06-24: 把旧的去掉)。"""
    return None


def _check_factory_recon_unbalanced(db: Session, exc: DataException) -> Optional[str]:
    """工厂对账不平: 修好 = 记录已删 / 已对平(status 非 underpaid|overpaid) /
    未付清但仍在账期内(账单周期结束 ≤60 天, 月结正常未付, 用户 2026-06-24)。"""
    from datetime import date
    from app.models.finance import FactoryReconciliation
    try:
        rid = int(exc.source_pk)
    except (TypeError, ValueError):
        return None
    r = db.query(FactoryReconciliation).filter(FactoryReconciliation.id == rid).first()
    if r is None or r.status not in ("underpaid", "overpaid"):
        return None
    if r.status == "underpaid":
        ref = r.period_end or r.reconciled_at
        if ref is not None and (date.today() - ref).days <= 60:
            return None  # 账期内未付, 正常月结, 销账
        return f"工厂对账 [{r.factory_name}] 账期结束已超60天仍未付清 ¥{r.diff_amount}"
    return f"工厂对账 [{r.factory_name}] 超付 ¥{-(r.diff_amount or 0)}"


def _check_order_no_unresolved(db: Session, exc: DataException) -> Optional[str]:
    """支付宝「关联订单号格式未识别」: 修好 = 该账户里"本该是淘宝订单却还原不出"的流水为 0。

    用户 #7 (2026-06-24): 个人号(主力号等)的关联订单号大量是安装费/亲情卡/推广充值/拼多多/
    快递/提现/代付/缴税/收钱码等非淘宝引用 (is_non_order_reference), 永远还原不出 19 位淘宝单号,
    不算"待补规则"。只有"形似淘宝订单却还原失败"的才算仍未解决。
    """
    from app.models.finance import AlipayFlow
    from app.services.order_no_normalizer import (
        is_non_order_reference, resolve_platform_order_no)
    acct = exc.source_pk
    accts = {a for (a,) in db.execute(select(AlipayFlow.account).distinct()).all()}
    q = select(AlipayFlow).where(AlipayFlow.related_order_no.isnot(None))
    if acct in accts:                      # source_pk=导入时 sheet_account; 命中真账户则按户复核
        q = q.where(AlipayFlow.account == acct)
    genuine = 0
    for f in db.execute(q).scalars().all():
        ron = (f.related_order_no or "").strip()
        if not ron:
            continue
        if getattr(f, "platform_order_no", None):       # 已还原, 跳过
            continue
        if resolve_platform_order_no(ron, getattr(f, "platform_order_no", None)):
            continue
        if is_non_order_reference(ron, f.counterparty, f.remark):
            continue
        genuine += 1
    if genuine:
        return (f"账户「{acct}」仍有 {genuine} 条疑似淘宝订单的关联订单号无法还原, "
                f"需在 order_no_normalizer 补还原规则")
    return None


def _check_factory_bill_on_dead_order(db: Session, exc: DataException) -> Optional[str]:
    """工厂账单挂在已取消/全额退款单: 修好 = 该单上已无工厂账单行(已重新匹配走), 或该单已不再是死单。"""
    from decimal import Decimal
    from app.models.factory_recon_item import FactoryReconItem
    from app.models.order import Order
    ono = (exc.context or {}).get("order_no") if exc.context else None
    if not ono and exc.source_pk and str(exc.source_pk).isdigit():
        o0 = db.get(Order, int(exc.source_pk))
        ono = o0.order_no if o0 else None
    if not ono:
        return None
    o = db.execute(select(Order).where(Order.order_no == ono)).scalar_one_or_none()
    if o is None:
        return None
    bill = db.execute(
        select(func.coalesce(func.sum(FactoryReconItem.settle_price), 0))
        .where(FactoryReconItem.order_no == ono)).scalar() or 0
    if Decimal(str(bill)) <= 0:
        return None   # 账单已改挂走 → 销账
    paid = Decimal(str(o.paid_amount or 0))
    refund = Decimal(str(o.refund_amount or 0))
    dead = (o.status or "") == "cancelled" or (paid > 0 and refund >= paid * Decimal("0.99"))
    if not dead:
        return None   # 该单已不再是死单 → 销账
    return f"工厂账单 ¥{bill} 仍挂在已取消/退款单 {ono} 上, 请「重新匹配」到该客户的有效订单"


def _check_cost_ratio_outlier(db: Session, exc: DataException) -> Optional[str]:
    """成本率离群: 修好 = 该单成本率已回正常带(或补了工厂实际成本)→ _cost_ratio_reason 返回 None。"""
    from app.models.order import Order
    from app.services.data_quality_service import _cost_ratio_reason
    o = None
    if exc.source_pk and str(exc.source_pk).isdigit():
        o = db.get(Order, int(exc.source_pk))
    if o is None:
        return None
    return _cost_ratio_reason(o)


_CHECKERS: dict[str, Callable[[Session, DataException], Optional[str]]] = {
    "order_no_unresolved": _check_order_no_unresolved,
    "factory_bill_on_dead_order": _check_factory_bill_on_dead_order,
    "cost_ratio_outlier": _check_cost_ratio_outlier,
    "alipay_duplicate_flow": _check_alipay_duplicate_flow,
    "duplicate_alipay_flow": _check_duplicate_alipay_cross_account,
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
    "reconciliation_diff": _check_reconciliation_diff,
    "dangling_product_code": _check_dangling_product_code,
    "factory_recon_incomplete": _check_factory_recon_incomplete,
    "factory_recon_pending_delivery": _check_factory_recon_pending_delivery,
    "factory_recon_unbalanced": _check_factory_recon_unbalanced,
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
