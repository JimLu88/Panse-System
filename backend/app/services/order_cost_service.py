"""订单理论成本反推 (按 BOM × 物料单价).

- 理论成本(单件) = Σ(该 SKU 的 BOM 每条 qty_per_product × 单价).
- 木作物料 (WD- 前缀) 不在 materials 表, 单价按 SKU 从定价表 PricingSku.wood_cost 取
  (木作成本是按 SKU 定制变化的, 整套木作部分共用这一个值).
- 实际成本不反推, 仅由人工/导入录入.
- 差异 = 实际成本 − 理论成本 (任一缺失则 None), 由 Order.cost_diff 属性给出.
- compute() 返回逐条物料 breakdown, 供前端把反推过程可视化.

另: backfill_theoretical_from_pricing() 直接用定价表 accounting_cost (会计总成本)
回填 order.theoretical_cost — 适合冷启动 / 缺 BOM 时, 把定价表当单一真值来源。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.exception import DataException
from app.models.material import Material
from app.models.order import Order
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services import sku_utils

_CENTS = Decimal("0.01")

# 木作物料编码前缀: 这类物料不在 materials 单价表, 价格按 SKU 从 PricingSku.wood_cost 取。
WOOD_PREFIX = "WD"

# 零成本订单关键词: 商家安装 / 送货入户 / 补差价 / 样品样块 / 专链补拍 等非产品单, 无整份SKU生产成本,
# 自动归0不报异常 (2026-06-17 扩充: 实测 157 单"实付几十却背一整套餐边柜成本¥9000"全是这类, 拉负总利润)。
# 注: "样品"/"样块" 不再归零 —— 木块小样是真实售卖的有成本商品(售价¥16-44, 物理成本约¥13),
# 按实际成本入账(用户拍板 2026-06-21)。淘宝标题"...样品样块"同含两词, 必须两词都不归零,
# 否则 any() 仍会被"样品"命中。default_warehouse_for 用自有字面量判仓库, 不受此影响。
# 淘宝官方服务/服务类商品(按标题/SKU 判): 无生产成本 → 理论成本归 0。
_OFFICIAL_SERVICE_KW = ("安装", "官方服务", "上门服务", "送货", "邮费", "专链", "补拍")
# 差价/补差价: 按「备注」判, 不读标题/SKU (标题易被误标; 真实判据是人工备注 —— 用户 2026-06-24)。
# 不在此直接归 0, 交差价/小额规则: 实付<200 归0; ≥200 按实付×85% + 挂异常待人工核。
_DIFF_KEYWORDS = ("补差价", "差价")
ZERO_COST_KEYWORDS = _OFFICIAL_SERVICE_KW + _DIFF_KEYWORDS   # 兼容旧引用


def default_warehouse_for(product_name: Optional[str], sku: Optional[str],
                          is_refill: bool) -> str:
    """发货仓库默认判定: 样块 / 补单订单统一杭州, 其余默认江西仓库。"""
    text = f"{product_name or ''} {sku or ''}"
    if is_refill or "样块" in text or "样品" in text:
        return "杭州"
    return "江西仓库"


def zero_cost_reason(order: Order) -> Optional[str]:
    """判断订单是否应把理论成本直接归 0, 返回原因 (否则 None)。

    - 补单/刷单: 不产生真实生产成本 (成本在补单记录单独核算), 理论成本=0。
    - 商家安装 / 淘宝官方服务 SKU: 淘宝官方服务, 无实际成本, 全部=0。
    """
    if order.is_refill:
        return "补单(刷单)无真实生产成本, 成本在补单记录核算"
    text = f"{order.sku or ''} {order.sku_code or ''} {order.product_name or ''}"
    if any(k in text for k in _OFFICIAL_SERVICE_KW):
        return "商家安装/淘宝官方服务 SKU, 无实际成本"
    # 差价/补差价不再按标题归0 —— 交差价/小额规则(读备注 + 金额阈值), 见 _apply_fragment_rule。
    return None


# 不在产销售的订单关键词/口径: 退款/退货/关闭 这类订单不是真实在产销售,
# 不该按成本率估算成本、更不该报「缺成本」异常 (用户拍板 2026-06-17)。
_REFUND_KEYWORDS = ("退款", "退货", "关闭")
# 只对 2026-01-01 起的订单做成本估算/异常 (更早的旧单不纳入, 用户拍板 2026-06-17)。
_COST_ESTIMATE_CUTOFF = date(2026, 1, 1)
# 全额退款判定阈值: 退款金额 ≥ 实付 × 此比例 视为整单退掉。
_FULL_REFUND_RATIO = Decimal("0.99")   # 与 sales_analytics.settled_sale_clause 的 0.99 统一: 90~99%退款带不再"算收入不算成本"


def _skip_cost_estimate(order: Order) -> Optional[str]:
    """判断订单是否应跳过「成本率估算 + 缺成本异常」, 满足任一即跳过, 返回原因 (否则 None)。

    用户拍板 2026-06-17: 退款单 / 关闭单 / 2026 年以前的旧单不是真实在产销售,
    不该估算成本也不该进异常台账。这些订单只用真实 BOM/定价反推 (查不到就留空)。
    """
    od = order.order_date
    if od is not None and od < _COST_ESTIMATE_CUTOFF:
        return f"订单日期 {od} 早于 {_COST_ESTIMATE_CUTOFF} (旧单, 不纳入成本估算)"
    st = order.status or ""
    # 只对已成交(paid/shipped/signed)的单估算成本; 待付款/退款/取消/关闭 都不是真实在产销售
    if (st in ("cancelled", "closed", "pending_payment", "aftersales")
            or "关闭" in st or "取消" in st or "待付款" in st):
        return f"订单状态 {st} (未成交/退款/取消/关闭, 不纳入成本估算)"
    # 用户 2026-06-24: 只跳「全额退款」。部分退款(如一单多子订单退一个、还剩一个)绝不跳过,
    # 否则剩余产品的成本会丢失。原先"退款状态含退款/退货即跳"的逻辑会误伤部分退款单 —— 已去掉,
    # 改为只看下方全额退款金额判定(退款≥实付×99%)。
    paid = order.paid_amount
    refund = order.refund_amount
    if paid is not None and refund is not None and Decimal(str(paid)) > 0:
        if Decimal(str(refund)) >= Decimal(str(paid)) * _FULL_REFUND_RATIO:
            return f"全额退款 (退款¥{refund} ≥ 实付¥{paid}×{_FULL_REFUND_RATIO})"
    return None


@dataclass
class CostLine:
    material_code: str
    material_name: Optional[str]
    qty_per_product: Decimal
    unit_price: Optional[Decimal]
    line_cost: Optional[Decimal]      # qty_per_product × unit_price
    missing_price: bool


@dataclass
class CostBreakdown:
    order_no: str
    sku_code: Optional[str]
    qty: int
    unit_cost: Decimal                # 单件理论成本 = Σ line_cost
    total_cost: Decimal               # unit_cost × qty
    lines: list[CostLine] = field(default_factory=list)
    resolved: bool = False            # 是否成功匹配到 BOM
    missing_price_count: int = 0
    cost_incomplete: bool = False     # 有物料/木作单价未知(NULL) → 该成本不完整, 非权威值
    note: Optional[str] = None


def _resolve_sku_code(db: Session, order: Order) -> Optional[str]:
    """订单上 sku_code 优先 (去掉定制「改」后缀取基础码查 BOM); 否则用 SKU 名反查.

    反查多义护栏 (2026-07-13): 「尺寸微定制/定制专拍/材质定制咨询」这类通用描述在多产品下
    各有一行(实测 16 行同名), 按描述反查天然多义 — 挑任意一行 = 错 BOM 错成本; 原
    scalar_one_or_none 直接 MultipleResultsFound, 把夜间订单维护/成本反推整轮任务炸停。
    多义 → 诚实返回 None, 该单走既有缺成本兜底(类目成本率)+ 缺成本异常管道。"""
    if order.sku_code:
        return sku_utils.strip_custom_suffix(order.sku_code)
    if order.sku:
        rows = db.execute(
            select(PricingSku).where(PricingSku.sku == order.sku)
        ).scalars().all()
        if len(rows) == 1:
            return rows[0].sku_code
    return None


def _asof_pricing(db: Session, sku_code, product_code, on_date, field: str, live):
    """★方案B(用户拍板 2026-07-12): 订单时点取定价字段 —— order_date 命中调价历史版本且该字段非空
    → 版本值(老单老价); 否则 live 现值(无版本/新单 = 改造前行为)。与 _pricing_cost_for 的 physical_at 同口径。"""
    if on_date is None:
        return live
    from app.services import pricing_version_service
    row = pricing_version_service.values_at(
        db, sku_code=sku_code, product_code=product_code, on_date=on_date)
    if row is not None:
        v = getattr(row, field, None)
        if v is not None:
            return Decimal(str(v))
    return live


def _wood_unit_price(db: Session, sku_code: Optional[str], on_date=None) -> Optional[Decimal]:
    """木作物料单价: 按 SKU 从定价表 PricingSku.wood_cost 取 (整套木作共用此值); 带订单日则老单老价."""
    if not sku_code:
        return None
    wc = db.execute(
        select(PricingSku.wood_cost).where(PricingSku.sku_code == sku_code)
    ).scalar_one_or_none()
    live = Decimal(str(wc)) if wc is not None else None
    return _asof_pricing(db, sku_code, None, on_date, "wood_cost", live)


def _custom_estimate_cost(db: Session, order: Order) -> Optional[Decimal]:
    """定制单缺成本时的占位估算: 取"同款(product_code)正常单(非定制/非补单)、理论成本已知"
    的平均单件理论成本。无同款正常单则 None。用户拍板 2026-06-15: 账面先大致准, 异常仍报。"""
    pc = (order.product_code or "").strip()
    if not pc:
        return None
    vals = db.execute(
        select(Order.theoretical_cost).where(
            Order.product_code == pc,
            Order.is_custom == False,        # noqa: E712
            Order.is_refill == False,        # noqa: E712
            Order.theoretical_cost.isnot(None),
            Order.theoretical_cost > 0,
        )
    ).scalars().all()
    if not vals:
        return None
    avg = sum((Decimal(str(v)) for v in vals), Decimal("0")) / Decimal(len(vals))
    return avg.quantize(_CENTS)


# ── 定制单套常规同尺寸款 (第二阶段, 用户拍板 2026-07-02): 尺寸对标 + 全套护栏 ─────────────
# 仅限岩板餐桌系列 (用户 2026-07-02: 其他产品不套); 白名单可在 system_settings 配 regular_size_product_codes。
_REGULAR_SIZE_CODES_KEY = "regular_size_product_codes"
# 用户 2026-07-02: 优先仅 PPS24210070901(榉木岩板餐桌)上线; 其他岩板餐桌系列(PPS24210050510/
# PFG26210060102 等)验证无误后再在 system_settings 的 regular_size_product_codes 里逗号追加扩展。
_REGULAR_SIZE_CODES_DEFAULT = "PPS24210070901"


def _regular_size_whitelist(db: Session) -> set:
    """套常规款生效的 product_code 白名单 (默认岩板餐桌 3 个系列; 后台可改)。"""
    from app.services import settings_service
    raw = settings_service.get(db, _REGULAR_SIZE_CODES_KEY, env_fallback=False) or _REGULAR_SIZE_CODES_DEFAULT
    return {c.strip() for c in raw.split(",") if c.strip()}


_LEN_RES = [
    re.compile(r"(\d\.\d{1,2})\s*[×xX*]"),          # 小数米×: 1.35*0.6 / 1.6*0.75 → 长边 1.35米
    re.compile(r"(\d(?:\.\d{1,2})?)\s*米"),         # 1.8米 / 1米
    re.compile(r"(\d{3,4})\s*[×xX*]\s*\d{2,4}"),   # 整数×: 1800×750 / 140×80 → 取长边
    re.compile(r"(\d{3,4})\s*mm"),                  # 1800mm
    re.compile(r"(\d{3})\s*cm"),                    # 180cm
]
_MULTI_KW = ("本单含", "含2个商品", "含两个商品", "含3个商品", "含多个商品")  # 多商品单 → 不套
_DONGSHI_KW = "洞石"                                # 洞石岩板 → 常规款(白岩板)基础上 +加价
_DONGSHI_SURCHARGE = Decimal("300")                 # 洞石岩板配件加价 (用户拍板 2026-07-02)
_SIZE_FRAGMENT_RATIO = Decimal("0.70")              # 片段护栏: 实付 < 匹配常规款physical×70% → 判片段, 不套


def _parse_length_cm(txt: Optional[str]) -> Optional[int]:
    """长度归一到厘米整数: 1800(mm)/1.8米/180cm/1800×750 → 180。取长边(第一个数); 取不到 → None。"""
    if not txt:
        return None
    for p in _LEN_RES:
        m = p.search(txt)
        if not m:
            continue
        v = float(m.group(1))
        if v >= 1000:            # mm
            return int(round(v / 10))
        if v < 10:               # 米
            return int(round(v * 100))
        if 100 <= v < 1000:      # cm
            return int(round(v))
    return None


def _regular_physical_map(db: Session, product_code: Optional[str]) -> dict:
    """该产品【常规款】(非定制SKU, physical_cost 非空) 的 {长度cm: 最小 physical_cost}。"""
    d: dict = {}
    if not product_code:
        return d
    for ps in db.execute(select(PricingSku).where(PricingSku.product_code == product_code)).scalars():
        if sku_utils.is_custom_sku_code(ps.sku_code) or not ps.physical_cost or ps.physical_cost <= 0:
            continue   # 跳过定制款 / physical≤0(如中古岩板餐桌未维护成本)
        L = _parse_length_cm(ps.sku)
        if L is None:
            continue
        v = Decimal(str(ps.physical_cost))
        if L not in d or v < d[L]:
            d[L] = v
    return d


def regular_size_cost(db: Session, order: Order) -> tuple[Optional[Decimal], str]:
    """定制单套常规同尺寸款成本 (带全部护栏)。返回 (cost, kind):
      cost=Decimal → 套用成功; cost=None → 不套(回落), kind 给原因(供缺尺寸异常/诊断)。

    优先级/护栏 (用户拍板 2026-07-02):
      not_custom  非定制单 → 不处理
      multi       多商品单("本单含N个商品") → 不套(走多商品逻辑)
      no_regular  该产品无常规款系列 → 不套
      missing_size 完整单(实付达标)但解析不出尺寸 → 不套, 标「缺尺寸」提示人工补
      fragment    片段/差价: 无尺寸小额, 或 实付 < 匹配常规physical×70% → 不套(维持封顶)
      oversize    尺寸 > 最大常规款 → 不套(回落)
      regular / regular_upsize (+_dongshi)  标准尺寸=直接套; 非标=向上取≥的最小常规款; 洞石+300
    """
    if not (order.is_custom or sku_utils.is_custom_sku_code(order.sku_code, order.product_code)):
        return None, "not_custom"
    if order.product_code not in _regular_size_whitelist(db):
        return None, "not_applicable"   # 仅限岩板餐桌白名单; 其他产品不套 (用户 2026-07-02)
    remark = order.remark or ""
    if any(k in remark for k in _MULTI_KW):
        return None, "multi"
    regs = _regular_physical_map(db, order.product_code)
    if not regs:
        return None, "no_regular"
    paid = Decimal(str(order.paid_amount or 0))
    length = _parse_length_cm(f"{order.sku or ''} {remark}")
    if length is None:
        # 无尺寸: 完整单(实付≥最小常规×70%)→缺尺寸提示; 否则小额→片段
        return None, ("missing_size" if paid >= min(regs.values()) * _SIZE_FRAGMENT_RATIO else "fragment")
    if length in regs:
        base, kind = regs[length], "regular"
    else:
        ups = sorted((l, p) for l, p in regs.items() if l >= length)
        if not ups:
            return None, "oversize"
        base, kind = ups[0][1], "regular_upsize"
    if _DONGSHI_KW in remark:
        base = base + _DONGSHI_SURCHARGE
        kind += "_dongshi"
    if paid < base * _SIZE_FRAGMENT_RATIO:   # 片段护栏: 实付远小于套用成本 → 判片段
        return None, "fragment"
    return base.quantize(_CENTS), kind


def compute(db: Session, order: Order) -> CostBreakdown:
    """反推一条订单的理论成本, 返回明细 (不写库).

    口径 (用户拍板 2026-06-12): **已知 SKU 直接用定价表工厂成本反推**
    (factory_cost 含全套配件的批量采购价) + 运费/安装/税/扣点 = 会计总成本;
    BOM 物料反推只用于 **定制单** (BOM 配件是单件订购价, 对成品 SKU 不准)。

    BOM 路径细节: 普通物料按 materials.price; 木作物料 (WD- 前缀) 按定价表
    PricingSku.wood_cost, 整套只计第一条 WD 行, 避免重复累加。
    """
    sku_code = _resolve_sku_code(db, order)
    _is_custom_order = order.is_custom or sku_utils.is_custom_sku_code(order.sku_code, order.product_code)

    # ---- 已知 SKU (非定制): 定价表直推, 不走 BOM ----
    if sku_code and not _is_custom_order:
        ps = db.execute(
            select(PricingSku).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
        if ps is not None and (ps.factory_cost is not None or ps.accounting_cost is not None):
            lines: list[CostLine] = []
            for code, name, val in (
                ("工厂成本", "出厂成本(定价表, 含全套配件)", ps.factory_cost),
                ("运费", "物流运输费(定价表)", ps.logistics_cost),
                ("安装", "安装费(定价表)", ps.install_cost),
            ):
                if val is None or Decimal(str(val)) == 0:
                    continue
                amt = Decimal(str(val)).quantize(_CENTS)
                lines.append(CostLine(
                    material_code=code, material_name=name,
                    qty_per_product=Decimal("1"), unit_price=amt,
                    line_cost=amt, missing_price=False,
                ))
            comp_sum = sum(
                (ln.line_cost for ln in lines if ln.line_cost is not None), Decimal("0")
            ).quantize(_CENTS)
            # 理论成本 = 物理总成本(商品+物流+安装), 不含税/平台扣点: 会计汇总(accounting_summary)
            # 已按实付另算这两项, 计进理论成本会被重复扣减 (用户拍板 2026-06-18 实测修正)。
            unit_cost = (Decimal(str(ps.physical_cost)).quantize(_CENTS)
                         if ps.physical_cost is not None else comp_sum)
            qty = int(order.qty or 1)
            note = "口径: 定价表物理总成本(商品+物流+安装); 税/扣点由会计汇总按实付另算, 不计入理论成本"
            if ps.physical_cost is not None and comp_sum != unit_cost:
                note += f" | 组件合计 ¥{comp_sum} 与物理总成本差 ¥{(unit_cost - comp_sum).quantize(_CENTS)}"
            return CostBreakdown(
                order_no=order.order_no, sku_code=sku_code, qty=qty,
                unit_cost=unit_cost,
                total_cost=(unit_cost * qty).quantize(_CENTS),
                lines=lines, resolved=True,
                missing_price_count=0, cost_incomplete=False, note=note,
            )

    # ---- 定制单 / 定价表无成本: BOM 物料反推 ----
    lines: list[CostLine] = []
    if sku_code:
        wood_price = _wood_unit_price(db, sku_code, on_date=getattr(order, "order_date", None))
        wood_counted = False  # 整套木作成本只计一次
        rows = db.execute(
            select(BomLine, Material.name.label("mat_name"), Material.price.label("price"))
            .join(Material, BomLine.material_code == Material.code, isouter=True)
            .where(BomLine.sku_code == sku_code)
        ).all()
        # 物料价按生效日版本化 (用户 2026-07-03): 成本取订单 order_date 当时生效价 →
        # 改价前订单用旧价、改价后用新价; 无历史则回退当前 Material.price (行为不变)。
        from datetime import date as _date_cls
        from app.services import material_price_service as _mps
        _on_date = order.order_date or _date_cls.today()
        for bom, mat_name, price in rows:
            qty_per = Decimal(str(bom.qty_per_product or 1))
            is_wood = (bom.material_code or "").upper().startswith(WOOD_PREFIX)
            if is_wood:
                # 木作: 单价取定价表 wood_cost, 整套只计第一条, 其余 WD 行计 0
                if wood_counted:
                    p = Decimal("0")
                else:
                    p = wood_price
                    wood_counted = wood_price is not None
                # 木作成本是整套固定值, 不乘 qty_per (qty_per 仅描述拆件份数)
                line_cost = p.quantize(_CENTS) if p is not None else None
                missing = wood_price is None
            else:
                _eff = _mps.material_price_at(db, bom.material_code, _on_date)
                p = Decimal(str(_eff)) if _eff is not None else None
                line_cost = (qty_per * p).quantize(_CENTS) if p is not None else None
                missing = p is None
            lines.append(CostLine(
                material_code=bom.material_code,
                material_name=mat_name,
                qty_per_product=qty_per,
                unit_price=p,
                line_cost=line_cost,
                missing_price=missing,
            ))

    # 费用组件 (用户 2026-06-12: 理论成本须含 运费/安装/税费/平台扣点 — 这些是实际发生的):
    # 来自定价表同 SKU 行。platform_fee_rate 列实存的是扣点金额 (账面核算:
    # accounting_cost = factory_cost + logistics + install + tax + platform_fee, 已实测吻合)。
    pricing_acc: Optional[Decimal] = None
    if lines and sku_code:
        ps = db.execute(
            select(PricingSku).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
        if ps is not None:
            if ps.accounting_cost is not None:
                pricing_acc = Decimal(str(ps.accounting_cost))
            # 理论成本只含 商品+物流+安装; 税/平台扣点由会计汇总按实付另算, 不在此累加 (避免重复)。
            for code, name, val in (
                ("运费", "物流运输费(定价表)", ps.logistics_cost),
                ("安装", "安装费(定价表)", ps.install_cost),
            ):
                if val is None or Decimal(str(val)) == 0:
                    continue
                amt = Decimal(str(val)).quantize(_CENTS)
                lines.append(CostLine(
                    material_code=code, material_name=name,
                    qty_per_product=Decimal("1"), unit_price=amt,
                    line_cost=amt, missing_price=False,
                ))

    unit_cost = sum(
        (ln.line_cost for ln in lines if ln.line_cost is not None), Decimal("0")
    ).quantize(_CENTS)
    qty = int(order.qty or 1)
    total_cost = (unit_cost * qty).quantize(_CENTS)
    missing = sum(1 for ln in lines if ln.missing_price)

    wood_missing = any(
        ln.missing_price and (ln.material_code or "").upper().startswith(WOOD_PREFIX)
        for ln in lines
    )
    if not lines:
        note = "未匹配到 BOM (订单缺 sku_code 或该 SKU 无 BOM), 无法反推理论成本"
    elif missing and wood_missing:
        note = f"{missing} 项单价未知(待核算), 含木作成本 — 请在定价表补 wood_cost 后重算"
    elif missing:
        note = f"{missing} 项物料单价未知(待核算) — 请在物料表补单价后重算"
    else:
        note = None
    # 与定价表会计总成本对账: 差异通常 = BOM 反推出厂成本 与 定价表出厂成本的差
    if pricing_acc is not None and lines:
        diff = (unit_cost - pricing_acc).quantize(_CENTS)
        cmp_note = (f"定价表会计总成本 ¥{pricing_acc}"
                    + (f" (反推差 {'+' if diff > 0 else ''}{diff})" if diff != 0 else " (反推一致)"))
        note = (note + " | " if note else "") + cmp_note

    # 方案B: is_custom 单(或 SKU编码带「改」后缀)在基础BOM成本上加一笔「定制加价」(可手改, 来自定制报价单或手填)
    if _is_custom_order and order.custom_surcharge is not None:
        sur = Decimal(str(order.custom_surcharge)).quantize(_CENTS)
        if sur != 0:
            had_base = bool(lines)
            lines.append(CostLine(
                material_code="定制加价",
                material_name="定制加价(方案B)",
                qty_per_product=Decimal("1"),
                unit_price=sur,
                line_cost=sur,
                missing_price=False,
            ))
            unit_cost = (unit_cost + sur).quantize(_CENTS)
            total_cost = (unit_cost * qty).quantize(_CENTS)
            note = (note + " | " if note else "") + f"含定制加价 ¥{sur}"
            if not had_base:
                note += "(⚠️ 基础BOM未匹配, 仅含加价)"

    # 定制单仍无成本依据(无BOM产品成本/无定制加价/无实际成本) → 用"同款正常单平均理论成本"占位估算,
    # 让账面毛利大致可用; 异常照报(scan 看 actual_cost/custom_surcharge, 不看此估算)。用户拍板 2026-06-15。
    _FEE_CODES = {"运费", "安装", "税费", "扣点", "定制加价"}
    _prod_cost = sum((ln.line_cost or Decimal("0")) for ln in lines
                     if ln.material_code not in _FEE_CODES)
    if (_is_custom_order and order.custom_surcharge is None and order.actual_cost is None
            and _prod_cost <= 0):
        _est = _custom_estimate_cost(db, order)
        if _est is not None and _est > 0:
            lines.append(CostLine(
                material_code="估算成本", material_name="同款正常单均价(占位估算)",
                qty_per_product=Decimal("1"), unit_price=_est, line_cost=_est,
                missing_price=False,
            ))
            unit_cost = (unit_cost + _est).quantize(_CENTS)
            total_cost = (unit_cost * qty).quantize(_CENTS)
            note = ((note + " | ") if note else "") + \
                f"⚠定制缺成本 → 按同款正常单均价估算 ¥{_est} (账面占位, 待补工厂实际成本)"

    return CostBreakdown(
        order_no=order.order_no,
        sku_code=sku_code,
        qty=qty,
        unit_cost=unit_cost,
        total_cost=total_cost,
        lines=lines,
        resolved=bool(lines),
        missing_price_count=missing,
        cost_incomplete=missing > 0,
        note=note,
    )


def cost_completeness_scan(db: Session) -> dict:
    """成本完整性体检: 找定价表里关键成本列为空(未知/待补)的 SKU。

    区别 0 (确实免费) 与 NULL (未知): NULL 表示成本不完整, 任何由它派生的
    标价/理论成本都不是权威值, 这里把这些 SKU 列出来供前端标「成本不完整 ⚠️」,
    而不是让系统拿一个偏低的数字当真。
    """
    skus = db.execute(select(PricingSku)).scalars().all()
    incomplete: list[dict] = []
    for s in skus:
        missing = [name for name, val in (
            ("木作成本", s.wood_cost),
            ("总出厂成本", s.factory_cost),
            ("会计总成本", s.accounting_cost),
        ) if val is None]
        if missing:
            incomplete.append({"sku_code": s.sku_code, "missing": missing})
    return {
        "total_skus": len(skus),
        "incomplete_count": len(incomplete),
        "incomplete": incomplete[:300],
    }


# ----------------------------- 类目/全店成本率 (用户拍板 2026-06-17) ------------ #

_SALE_STATUSES = ("paid", "shipped", "signed", "production", "aftersales")


def category_cost_ratios(db: Session, *, min_orders: int = 5) -> dict:
    """动态算「成本÷营收」比例: 每个产品类目一个 (成本=actual或theoretical, 营收=实付),
    类目订单数 < min_orders → 该类目退用全店平均。返回 {"_store": 比例, 类目: 比例, ...},
    比例 clamp 到 [0.2, 0.95]。用户拍板 2026-06-17: 写进系统动态算, 类目不足退全店。

    给「查不到 SKU 成本的订单」按 实付 × 此比例 兜底成本 (账面不虚高)。
    """
    eff_cost = func.coalesce(Order.actual_cost, Order.theoretical_cost)
    rows = db.execute(
        select(Product.category, Order.paid_amount, eff_cost)
        .join(Product, Product.code == Order.product_code, isouter=True)
        .where(
            Order.is_refill == False,             # noqa: E712
            Order.status.in_(_SALE_STATUSES),
            Order.paid_amount.isnot(None), Order.paid_amount > 0,
            eff_cost.isnot(None), eff_cost > 0,
        )
    ).all()
    cat_rev: dict = defaultdict(lambda: Decimal("0"))
    cat_cost: dict = defaultdict(lambda: Decimal("0"))
    cat_n: dict = defaultdict(int)
    tot_rev = tot_cost = Decimal("0")
    for cat, paid, cost in rows:
        paid = Decimal(str(paid)); cost = Decimal(str(cost))
        if paid <= 0:
            continue
        # 剔除离群单: 补差价/部分付款/错配单 (实付远低于成本, 成本率>1.2 或 <0.1) 会把比例带歪, 不计入
        r = float(cost / paid)
        if r < 0.1 or r > 1.2:
            continue
        c = cat or "_uncat"
        cat_rev[c] += paid; cat_cost[c] += cost; cat_n[c] += 1
        tot_rev += paid; tot_cost += cost

    def _clamp(r: float) -> float:
        return round(min(max(r, 0.2), 0.95), 4)

    store = _clamp(float(tot_cost / tot_rev)) if tot_rev > 0 else 0.6
    out: dict = {"_store": store}
    for c, n in cat_n.items():
        if c == "_uncat":
            continue
        if n >= min_orders and cat_rev[c] > 0:
            out[c] = _clamp(float(cat_cost[c] / cat_rev[c]))
    return out


def _sales_ratio_cost(db: Session, order: Order, ratios: dict) -> Optional[Decimal]:
    """实付 × 类目成本率 (类目缺/不足 → 全店平均)。查不到产品类目也用全店平均。"""
    paid = Decimal(str(order.paid_amount or 0))
    if paid <= 0:
        return None
    cat = None
    if order.product_code:
        cat = db.execute(
            select(Product.category).where(Product.code == order.product_code)
        ).scalar_one_or_none()
    ratio = ratios.get(cat) if cat else None
    if ratio is None:
        ratio = ratios.get("_store")
    if not ratio:
        return None
    return (paid * Decimal(str(ratio))).quantize(_CENTS)


# 差价/定金/补拍片段判定 (用户拍板 2026-06-18): 实付远低于整件产品成本 → 不是整件成交,
# 而是用产品链接付的小额(补差价/定金/补拍邮费)。这类按 实付×85% 兜底成本(留15%毛利),
# 不挂整柜成本 (否则 ¥200 订单背 ¥8721 整柜成本, 月度成本被拉爆)。
_FRAGMENT_PAID_RATIO = Decimal("0.5")    # 实付 < 产品成本×此比例 → 判为片段
_FRAGMENT_COST_RATE = Decimal("0.85")    # 片段成本 = 实付 × 此率
_DIFF_ZERO_BELOW = Decimal("200")        # 差价/小额单: 实付 < 此额 → 成本归0 (用户 2026-06-24)


def _is_diff_remark(order: Order) -> bool:
    """备注(商家备注+ERP备注+买家留言)含 差价/补差价 → 差价单 (不读标题/SKU, 用户 2026-06-24)。"""
    txt = " ".join(str(getattr(order, f, None) or "")
                   for f in ("seller_memo", "remark", "buyer_message"))
    return any(k in txt for k in _DIFF_KEYWORDS)


def _apply_fragment_rule(order: Order, bd: CostBreakdown) -> Decimal:
    """差价/小额单兜底 (用户 2026-06-24 重定): 非定制单, 若是差价单(备注含差价/补差价)或
    片段(实付<整件成本×50%) → 实付<200 成本归0; 实付≥200 成本=实付×85%(留毛利, 后续挂异常待人工核)。

    只对非定制单生效(定制单走定制报价/独立核算); bd 已带整件产品成本(bd.unit_cost)。原地改 bd。
    """
    cost = bd.unit_cost
    if order.is_custom:
        return cost
    paid = Decimal(str(order.paid_amount or 0))
    is_fragment = bool(cost and cost > 0 and paid > 0 and paid < cost * _FRAGMENT_PAID_RATIO)
    if not (_is_diff_remark(order) or is_fragment):
        return cost
    if paid < _DIFF_ZERO_BELOW:                         # 实付<200 → 归0
        bd.unit_cost = Decimal("0")
        bd.total_cost = Decimal("0")
        bd.cost_incomplete = True
        bd.note = ((bd.note + " | ") if bd.note else "") + \
            f"差价/小额单(实付¥{paid}<200) → 成本归0"
        return Decimal("0")
    capped = (paid * _FRAGMENT_COST_RATE).quantize(_CENTS)   # 实付≥200 → 实付×85%
    bd.unit_cost = capped
    bd.total_cost = (capped * bd.qty).quantize(_CENTS)
    bd.cost_incomplete = True
    bd.note = ((bd.note + " | ") if bd.note else "") + \
        f"差价/小额单(实付¥{paid}≥200) → 实付×85%兜底 ¥{capped} (待人工核价)"
    return capped


def _effective_qty(order: Order, unit_cost: Decimal) -> int:
    """真实计价件数(修「买N件只算1件成本」bug)。

    默认 1; 仅当 qty>1 且**非定制** 且**件均实付 ≥ 单件成本**(确认是真多件,
    而非「拍N件凑价」的定金/差价/链接单 —— 那些 qty 是脏数据)时返回 qty。
    用户拍板 2026-06-20: qty bug 全量修复须排除定制单。
    注: 仅用于 BOM/定价表「单件」成本路径; 实付×成本率兜底已是订单总额, 不乘。"""
    qty = int(order.qty or 1)
    if qty <= 1 or order.is_custom or unit_cost is None or unit_cost <= 0:
        return 1
    paid = Decimal(str(order.paid_amount or 0))
    return qty if paid > 0 and (paid / qty) >= unit_cost else 1


def _multi_product_cost(db: Session, order: Order) -> Optional[Decimal]:
    """一单多宝贝 → 按 order_details(source='import')各商品行 pricing物理成本×qty 汇总成本。

    需 ≥2 个能取到定价的商品行才算多产品(否则 None, 走原单SKU路径, 保留BOM精度)。
    杜绝塌单漏算(餐桌+床这类: 导入只留主商品, 成本只算一个 → 这里按全部商品行汇总)。
    """
    from app.models.order import OrderDetail
    lines = db.execute(
        select(OrderDetail).where(
            OrderDetail.order_no == order.order_no, OrderDetail.source == "import")
    ).scalars().all()
    if len(lines) < 2:
        return None
    # 口径A(用户拍板 2026-06-22): 成本只算"留下的"子产品。排除被退的那一行
    # (其 amount ≈ 订单剩余退款额 refund_amount; refund 已被 _normalize_refund 归0的单无需排除→全留)。
    _refund = Decimal(str(getattr(order, "refund_amount", None) or 0))
    if _refund > 0:
        lines = [ln for ln in lines
                 if abs(Decimal(str(ln.amount or 0)) - _refund) >= Decimal("0.5")]
        if len(lines) < 1:
            return None
    total = Decimal("0")
    _on = getattr(order, "order_date", None)
    for ln in lines:
        cost = None
        if ln.sku_code:
            ps = db.execute(
                select(PricingSku).where(PricingSku.sku_code == ln.sku_code)
            ).scalar_one_or_none()
            if ps is not None and ps.physical_cost is not None:
                cost = Decimal(str(ps.physical_cost))
        if cost is None and ln.product_code:   # 退一步: 同产品任一有价行
            ps2 = db.execute(
                select(PricingSku).where(
                    PricingSku.product_code == ln.product_code,
                    PricingSku.physical_cost.isnot(None))
            ).scalars().first()
            if ps2 is not None:
                cost = Decimal(str(ps2.physical_cost))
        cost = _asof_pricing(db, ln.sku_code, ln.product_code, _on, "physical_cost", cost)  # 老单老价
        if cost is None:
            return None   # 有商品行查不到定价 → 整单成本不完整, 回退兜底路径(勿把缺行的部分和当整单成本, 否则漏算副商品→利润虚高)
        total += cost * int(ln.qty or 1)
    if total <= 0:
        return None
    # 口径A护栏(2026-06-22): 子行成本和 > 实付×1.1 → 实付只覆盖了部分子产品(部分付款/漏退/qty异常),
    # 无法可靠判定"留下哪些", 返回 None 回退单SKU路径, 不硬套汇总造成假亏(交人工核实实付/qty)。
    _paid = Decimal(str(getattr(order, "paid_amount", None) or 0))
    if _paid > 0 and total > _paid * Decimal("1.1"):
        return None
    return total


def _pricing_wood_for(db: Session, order: Order) -> Optional[Decimal]:
    """该单匹配 SKU 的定价表 wood_cost (木作成本)。与 _pricing_cost_for 对称: 先 sku_code 再 product_code。

    工厂账单只含木作, actual_cost 是木作实报; 这里取定价表里的木作部分, 供 physical_cost 反推非木作
    (= theoretical − wood_est)。取不到 → None (physical_cost 退回旧行为)。老单老价(方案B)。"""
    sku_code = _resolve_sku_code(db, order)
    _on = getattr(order, "order_date", None)
    if sku_code:
        wc = db.execute(
            select(PricingSku.wood_cost).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
        live = Decimal(str(wc)) if wc is not None else None
        v = _asof_pricing(db, sku_code, None, _on, "wood_cost", live)
        if v is not None:
            return v
    if order.product_code:
        wc = db.execute(
            select(PricingSku.wood_cost)
            .where(PricingSku.product_code == order.product_code,
                   PricingSku.wood_cost.isnot(None))
            .limit(1)
        ).scalar_one_or_none()
        live = Decimal(str(wc)) if wc is not None else None
        v = _asof_pricing(db, None, order.product_code, _on, "wood_cost", live)
        if v is not None:
            return v
    return None


def _multi_product_wood(db: Session, order: Order) -> Optional[Decimal]:
    """一单多宝贝 → 各 import 商品行 SKU 的定价表 wood_cost × qty 之和。与 _multi_product_cost 对称。

    任一商品行查不到 wood_cost → None (整单木作估算不完整, 不写 wood_cost_est, physical_cost 退回旧行为)。
    """
    from app.models.order import OrderDetail
    lines = db.execute(
        select(OrderDetail).where(
            OrderDetail.order_no == order.order_no, OrderDetail.source == "import")
    ).scalars().all()
    if len(lines) < 2:
        return None
    total = Decimal("0")
    _on = getattr(order, "order_date", None)
    for ln in lines:
        wc = None
        if ln.sku_code:
            wc = db.execute(
                select(PricingSku.wood_cost).where(PricingSku.sku_code == ln.sku_code)
            ).scalar_one_or_none()
        if wc is None and ln.product_code:
            wc = db.execute(
                select(PricingSku.wood_cost)
                .where(PricingSku.product_code == ln.product_code,
                       PricingSku.wood_cost.isnot(None))
                .limit(1)
            ).scalar_one_or_none()
        wc = _asof_pricing(db, ln.sku_code, ln.product_code, _on, "wood_cost",
                           Decimal(str(wc)) if wc is not None else None)  # 老单老价
        if wc is None:
            return None
        total += Decimal(str(wc)) * int(ln.qty or 1)
    return total if total > 0 else None


def _set_wood_est(order: Order, wood: Optional[Decimal]) -> None:
    """写 order.wood_cost_est (木作估算)。取不到(None/≤0) → 不写, 留原值,
    让 physical_cost 退回旧行为(actual_cost 直接当物理成本)。"""
    if wood is not None and wood > 0:
        order.wood_cost_est = wood.quantize(_CENTS)


def _pricing_parts_for(db: Session, order: Order) -> Optional[Decimal]:
    """该单匹配 SKU 的定价表 external_parts_cost (配件/外采标准估值, 单件)。与 _pricing_wood_for 对称。

    配件取值阶梯 (用户拍板 2026-07-13):
    ① SKU 行外配件**有值(含显式0=标准无外配件)** → 直接用;
    ② SKU 行是 NULL(未填, 如后补的"定制尺寸/尺寸微定制"咨询行) 或无 SKU 行 → 同产品挑行:
       有木作账单锚(actual_cost/件) → 按 |行wood_cost − 锚| 最接近的规格行取其外配件
       (实测: 定制曜黑柜账单8800 → 对上整柜2.1的1598.49, 而非旧 limit(1) 撞到下柜的460);
       无锚 → 按 id 取首行(旧行为)。
    ③ 15%×实付兜底: 用户指示暂不做。
    全部命中不了 → None(est_parts 留空="未知/无定价参照")。老单老价(方案B)。"""
    sku_code = _resolve_sku_code(db, order)
    _on = getattr(order, "order_date", None)
    if sku_code:
        row = db.execute(
            select(PricingSku.external_parts_cost).where(PricingSku.sku_code == sku_code)
        ).first()
        if row is not None and row[0] is not None:   # ① 有值(含0)直接用; NULL=未填 → 掉②
            return _asof_pricing(db, sku_code, None, _on, "external_parts_cost",
                                 Decimal(str(row[0])))
    if order.product_code:
        rows = db.execute(
            select(PricingSku.sku_code, PricingSku.external_parts_cost, PricingSku.wood_cost)
            .where(PricingSku.product_code == order.product_code)
            .order_by(PricingSku.id)
        ).all()
        cands = [(sc, ep, wc) for sc, ep, wc in rows if ep is not None]
        if not cands:
            return None
        best = None
        _ac = getattr(order, "actual_cost", None)
        if _ac is not None and Decimal(str(_ac)) > 0:
            anchor = Decimal(str(_ac)) / max(int(order.qty or 1), 1)
            with_wood = [c for c in cands if c[2] is not None]
            if with_wood:
                best = min(with_wood, key=lambda c: abs(Decimal(str(c[2])) - anchor))
        if best is None:
            best = cands[0]
        return _asof_pricing(db, best[0], order.product_code, _on, "external_parts_cost",
                             Decimal(str(best[1])))
    return None


def _multi_product_parts(db: Session, order: Order) -> Optional[Decimal]:
    """一单多宝贝 → 各 import 商品行 SKU 的定价表 external_parts_cost × qty 之和。与 _multi_product_wood 对称。

    < 2 行 → None(非多宝贝, 走单 SKU 路径)。某行查不到配件估值视作 0(配件可选, 不像木作要求齐全)。"""
    from app.models.order import OrderDetail
    lines = db.execute(
        select(OrderDetail).where(
            OrderDetail.order_no == order.order_no, OrderDetail.source == "import")
    ).scalars().all()
    if len(lines) < 2:
        return None
    total = Decimal("0")
    _on = getattr(order, "order_date", None)
    for ln in lines:
        ep = None
        if ln.sku_code:
            ep = db.execute(
                select(PricingSku.external_parts_cost).where(PricingSku.sku_code == ln.sku_code)
            ).scalar_one_or_none()
        if ep is None and ln.product_code:
            ep = db.execute(
                select(PricingSku.external_parts_cost)
                .where(PricingSku.product_code == ln.product_code).limit(1)
            ).scalar_one_or_none()
        ep = _asof_pricing(db, ln.sku_code, ln.product_code, _on, "external_parts_cost",
                           Decimal(str(ep)) if ep is not None else None)  # 老单老价
        total += Decimal(str(ep or 0)) * int(ln.qty or 1)
    return total


def _set_parts_est(order: Order, parts_unit: Optional[Decimal], eff_qty: int) -> None:
    """写 order.est_parts (配件标准估值 = 单件 external_parts_cost × 真实计价件数)。

    parts_unit None(无定价参照) → 不写, 留 reset 后的 None。0(产品标准无配件) → 写 0。"""
    if parts_unit is not None:
        order.est_parts = (parts_unit * eff_qty).quantize(_CENTS)


def recompute_and_save(db: Session, order: Order, *, ratios: Optional[dict] = None) -> CostBreakdown:
    """反推并把单件理论成本写回 order.theoretical_cost (不动 actual_cost).

    补单/安装SKU 直接归 0, 不走 BOM 反推。
    传 ratios (类目/全店成本率) 时, BOM/定价表都查不到的订单按 实付×成本率 兜底 (用户拍板 2026-06-17),
    标记为「估算」并由 auto_cost_backfill 写异常待人工补实际成本。
    """
    # 每次重算从干净状态开始: 木作估算/配件标准估值只在能取到的分支回填, 旧值不残留(防改判后污染)
    order.wood_cost_est = None
    order.est_parts = None       # 配件标准估值(派生列); 不碰 actual_parts(真实值, 人工/汇总设)
    reason = zero_cost_reason(order)
    if reason is not None:
        order.theoretical_cost = Decimal("0")
        return CostBreakdown(
            order_no=order.order_no, sku_code=order.sku_code, qty=int(order.qty or 1),
            unit_cost=Decimal("0"), total_cost=Decimal("0"),
            resolved=True, note=f"理论成本归0: {reason}",
        )
    # 一单多宝贝: 按 order_details(source='import')各商品行汇总成本(杜绝塌单漏算; 片段封顶在 physical_cost 读取时统一处理)
    _mp = _multi_product_cost(db, order)
    if _mp is not None:
        order.theoretical_cost = _mp.quantize(_CENTS)
        _set_wood_est(order, _multi_product_wood(db, order))
        _mpp = _multi_product_parts(db, order)
        if _mpp is not None:
            order.est_parts = _mpp.quantize(_CENTS)
        return CostBreakdown(
            order_no=order.order_no, sku_code=order.sku_code, qty=int(order.qty or 1),
            unit_cost=_mp, total_cost=_mp, resolved=True,
            note=f"一单多宝贝: order_details 各商品行汇总 ¥{_mp.quantize(_CENTS)}",
        )
    bd = compute(db, order)
    if bd.resolved:
        _unit = _apply_fragment_rule(order, bd)
        order.theoretical_cost = (_unit * _effective_qty(order, _unit)).quantize(_CENTS)
        _set_wood_est(order, _pricing_wood_for(db, order))
        _set_parts_est(order, _pricing_parts_for(db, order), _effective_qty(order, _unit))
        return bd
    # 无 BOM → 回退定价表物理总成本
    cost = _pricing_cost_for(db, order)
    if cost is not None:
        bd.unit_cost = cost.quantize(_CENTS)
        bd.total_cost = (bd.unit_cost * bd.qty).quantize(_CENTS)
        bd.resolved = True
        bd.note = ((bd.note + " | ") if bd.note else "") + "已回退定价表物理总成本"
        _unit = _apply_fragment_rule(order, bd)
        order.theoretical_cost = (_unit * _effective_qty(order, _unit)).quantize(_CENTS)
        _set_wood_est(order, _pricing_wood_for(db, order))
        _set_parts_est(order, _pricing_parts_for(db, order), _effective_qty(order, _unit))
        return bd
    # 最终兜底: 实付 × 类目/全店成本率 (查不到任何 SKU/产品成本时, 如缺产品编码的订单)
    if ratios is not None:
        est = _sales_ratio_cost(db, order, ratios)
        if est is not None and est > 0:
            order.theoretical_cost = est
            bd.unit_cost = est
            bd.total_cost = (est * bd.qty).quantize(_CENTS)
            bd.resolved = True
            bd.cost_incomplete = True
            bd.note = ((bd.note + " | ") if bd.note else "") + \
                f"⚠按销售额×成本率估算 ¥{est} (缺SKU成本, 已进异常待补实际)"
    return bd


def recompute_all(db: Session, *, only_missing: bool = True) -> dict:
    """批量反推. only_missing=True 时补 theoretical_cost 为空 **或为 0** 的订单
    (历史导入曾把未反推订单写成 0, 只查 NULL 会让这批永远卡死, 2026-06-12 修);
    真正应为 0 的 (补单/安装SKU) 由 zero_cost_reason 幂等重新归 0, 不受影响。

    Returns: {updated, skipped_no_bom, total}
    """
    from sqlalchemy import or_
    stmt = select(Order)
    if only_missing:
        stmt = stmt.where(or_(Order.theoretical_cost.is_(None),
                              Order.theoretical_cost == 0))
    orders = db.execute(stmt).scalars().all()
    updated = skipped = 0
    for o in orders:
        bd = recompute_and_save(db, o)
        if bd.resolved:
            updated += 1
        else:
            skipped += 1
    return {"updated": updated, "skipped_no_bom": skipped, "total": len(orders)}


# ------------------- 全自动成本兜底 + 缺成本异常 (用户拍板 2026-06-17) ----------- #


def _flag_cost_exceptions(db: Session, estimated_nos: list[str]) -> dict:
    """给「成本靠销售额估算」的已付款订单写异常(待人工补实际成本); 已补 actual_cost 的自动关掉。幂等去重。"""
    et = "cost_missing_estimated"
    open_pks = set(db.execute(
        select(DataException.source_pk).where(
            DataException.source_table == "orders",
            DataException.exception_type == et,
            DataException.status == "open",
        )
    ).scalars().all())
    created = 0
    for no in estimated_nos:
        if not no or no in open_pks:
            continue
        db.add(DataException(
            source_table="orders", source_pk=no, exception_type=et,
            severity="warning", status="open",
            description=f"订单 {no} 缺工厂/SKU成本, 系统已按销售额×成本率估算入账, 请补录实际工厂成本。",
            suggestion_action="补录工厂成本",
        ))
        open_pks.add(no)
        created += 1
    # 自动关闭: ① 之前估算、现已录入实际成本(actual_cost>0)的异常;
    #           ② 退款/关闭/旧单 误报的异常 — 同时清掉它们被估算出来的 theoretical_cost
    #             (这些不是真实在产销售, 估算值会拖累利润, 用户拍板 2026-06-17)。
    resolved = resolved_refund = 0
    for ex in db.execute(
        select(DataException).where(
            DataException.source_table == "orders",
            DataException.exception_type == et,
            DataException.status == "open",
        )
    ).scalars().all():
        o = db.execute(select(Order).where(Order.order_no == ex.source_pk)).scalar_one_or_none()
        if o is None:
            continue
        if o.actual_cost is not None and Decimal(str(o.actual_cost)) > 0:
            ex.status = "resolved"
            resolved += 1
        elif _skip_cost_estimate(o) is not None:
            ex.status = "resolved"
            o.theoretical_cost = None  # 清掉错误的成本率估算值
            resolved_refund += 1
        elif zero_cost_reason(o) is not None or o.theoretical_cost is None \
                or Decimal(str(o.theoretical_cost or 0)) == 0:
            # 非产品单(差价/样品)已归0, 或成本已不是"按率估算" → 不再算缺成本, 销账
            ex.status = "resolved"
            resolved_refund += 1
    return {"created": created, "resolved": resolved, "resolved_refund_old": resolved_refund}


def auto_cost_backfill(db: Session) -> dict:
    """全自动成本兜底 (用户拍板 2026-06-17, 后台定时自动跑, 不用点按钮):
       1) 对所有"未反推"(NULL/0)订单跑 BOM/定价表反推;
       2) 仍查不到 SKU/产品成本的 → 按 实付×类目成本率(类目不足退全店) 兜底, 账面不虚高;
       3) 给"靠估算/缺成本"的已付款订单写异常待人工补实际成本 (补了自动关闭)。
    返回统计。替代旧 _job_cost_recompute 的 recompute_all。
    """
    # 先把"应归0却被估了整份SKU成本"的非产品单(差价/样品/专链/安装/送货)重新归0 (2026-06-17):
    # 实测 157 单实付几十却背一整套餐边柜成本¥9000, 是总利润为负的主因, 这里一次性纠正。
    rezeroed = 0
    for o in db.execute(
        select(Order).where(Order.theoretical_cost.isnot(None), Order.theoretical_cost != 0)
    ).scalars().all():
        if zero_cost_reason(o) is not None:
            o.theoretical_cost = Decimal("0")
            rezeroed += 1
    if rezeroed:
        db.flush()

    # 多产品单成本自愈 (2026-06-22 用户拍板口径A): 若多产品单(≥2 import子行)当前 theoretical_cost
    # ≠ 子行汇总成本(_multi_product_cost), 重置为 None 待下面按口径A重算。
    # 防"分两次导入"(先订单报表落单产品成本、后销售明细加子行→成本已落不再刷新)的塌单漏算复发。
    # 护栏返回 None 的(成本和>实付的部分付款/qty异常单)与不可定价子行的单 _mp=None → 不动(保留单SKU)。
    from app.models.order import OrderDetail as _OD
    from sqlalchemy import func as _func
    mp_reset = 0
    _multi_nos = db.execute(
        select(_OD.order_no).where(_OD.source == "import")
        .group_by(_OD.order_no).having(_func.count() > 1)
    ).scalars().all()
    for _ono in _multi_nos:
        _o = db.execute(select(Order).where(Order.order_no == _ono)).scalar_one_or_none()
        if _o is None or getattr(_o, "is_custom", False):
            continue
        _mp = _multi_product_cost(db, _o)
        if _mp is not None and Decimal(str(_o.theoretical_cost or 0)) != _mp.quantize(_CENTS):
            _o.theoretical_cost = None
            _o.actual_cost = None    # 多产品按子行汇总; 留 actual 会让 physical_cost 忽略汇总
            mp_reset += 1
    if mp_reset:
        db.flush()

    ratios = category_cost_ratios(db)
    orders = db.execute(
        select(Order).where(or_(Order.theoretical_cost.is_(None), Order.theoretical_cost == 0))
    ).scalars().all()
    updated = skipped = estimated = skipped_refund = 0
    est_nos: list[str] = []
    for o in orders:
        # 退款/关闭/旧单: 只用真实 BOM/定价反推 (ratios=None, 不走率兜底), 不进异常台账
        if _skip_cost_estimate(o) is not None:
            bd = recompute_and_save(db, o, ratios=None)
            if bd.resolved:
                updated += 1
            else:
                skipped += 1
            skipped_refund += 1
            continue
        bd = recompute_and_save(db, o, ratios=ratios)
        if bd.resolved:
            updated += 1
            if "按销售额×成本率估算" in (bd.note or ""):
                estimated += 1
                est_nos.append(o.order_no)
        else:
            skipped += 1
    db.flush()
    exc = _flag_cost_exceptions(db, est_nos)
    db.commit()
    return {
        "updated": updated, "estimated_by_ratio": estimated, "still_missing": skipped,
        "skipped_refund_old": skipped_refund, "rezeroed_non_product": rezeroed,
        "exceptions": exc, "store_ratio": ratios.get("_store"),
        "category_ratios": {k: v for k, v in ratios.items() if k != "_store"},
    }


# ----------------------------- 定价表回填 ---------------------------- #

# 交易关闭/取消的订单无需理论成本 (不影响利润核算)
_CLOSED_STATUSES = {"cancelled"}


def _pricing_cost_for(db: Session, order: Order) -> Optional[Decimal]:
    """按 sku_code (其次 product_code) 从定价表取物理总成本 physical_cost (商品+物流+安装)。

    用物理成本而非会计总成本: 税/平台扣点由会计汇总(accounting_summary)按实付另算,
    这里若返回含税/扣点的会计成本会被重复扣减 (用户拍板 2026-06-18)。physical 缺则退回出厂成本。
    """
    # physical_cost 缺时回退「出厂价 + 物流 + 安装」(=物理总成本), 不能只回退裸出厂价 ——
    # 否则下游双算护栏(theoretical 已含物流安装→运费/安装置0)会把这单的物流安装永久漏掉, 利润虚高。
    sku_code = _resolve_sku_code(db, order)
    # 有效期定价 (工厂调价历史): 订单按 order_date 命中的历史版本优先 (老单老价);
    # 无任何版本 / 落在最后边界之后 → 返回 None → 落到下方 live pricing_sku 逻辑 (=改造前行为, 不影响存量)。
    from app.services import pricing_version_service
    _vp = pricing_version_service.physical_at(
        db, sku_code=sku_code, product_code=order.product_code,
        on_date=getattr(order, "order_date", None))
    if _vp is not None:
        return _vp
    cost_col = func.coalesce(
        PricingSku.physical_cost,
        PricingSku.factory_cost + func.coalesce(PricingSku.logistics_cost, 0) + func.coalesce(PricingSku.install_cost, 0),
    )
    if sku_code:
        c = db.execute(
            select(cost_col).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
        if c is not None:
            return Decimal(str(c))
    # 退一步: 用 product_code 取该产品任一有成本的定价行 (同产品不同 SKU 成本接近)
    if order.product_code:
        c = db.execute(
            select(cost_col)
            .where(PricingSku.product_code == order.product_code,
                   func.coalesce(PricingSku.physical_cost, PricingSku.factory_cost).isnot(None))
            .limit(1)
        ).scalar_one_or_none()
        if c is not None:
            return Decimal(str(c))
    return None


def backfill_theoretical_from_pricing(
    db: Session, *, only_missing: bool = True, skip_closed: bool = True,
) -> dict:
    """用定价表 accounting_cost 回填 order.theoretical_cost (单一真值来源).

    与 recompute_all (按 BOM 反推) 互补: 这个直接信任定价表的会计总成本,
    适合冷启动 / 缺 BOM 的订单。

    - only_missing: 仅补 theoretical_cost 为空的订单
    - skip_closed: 跳过 cancelled 订单 (交易关闭无需理论成本)

    Returns: {updated, skipped_no_pricing, skipped_closed, total}
    """
    stmt = select(Order)
    if only_missing:
        stmt = stmt.where(Order.theoretical_cost.is_(None))
    orders = db.execute(stmt).scalars().all()
    updated = no_pricing = closed = zeroed = 0
    for o in orders:
        if skip_closed and o.status in _CLOSED_STATUSES:
            closed += 1
            continue
        # 补单/安装SKU → 直接归 0, 不查定价表
        if zero_cost_reason(o) is not None:
            o.theoretical_cost = Decimal("0")
            zeroed += 1
            continue
        cost = _pricing_cost_for(db, o)
        if cost is not None:
            _u = cost.quantize(_CENTS)
            o.theoretical_cost = (_u * _effective_qty(o, _u)).quantize(_CENTS)
            updated += 1
        else:
            no_pricing += 1
    db.flush()
    return {
        "updated": updated,
        "zeroed_refill_install": zeroed,
        "skipped_no_pricing": no_pricing,
        "skipped_closed": closed,
        "total": len(orders),
    }


def backfill_est_parts(db: Session, *, skip_closed: bool = True) -> dict:
    """一次性回填 order.est_parts (配件标准估值 = 定价 external_parts_cost × 真实计价件数)。

    与 recompute_and_save 同口径, 供存量订单冷启动(recompute 未必全量跑过)。
    非产品单(补单/专链/服务)→ 标准配件 0; 无定价参照 → 留 None。est_parts 仅作大宗材料
    对账基线 + P3 分摊基数, 不进 physical_cost, 回填零财务风险。
    Returns: {set, skipped_no_pricing, skipped_closed, total}。"""
    orders = db.execute(select(Order)).scalars().all()
    set_cnt = no_pricing = closed = 0
    for o in orders:
        if skip_closed and o.status in _CLOSED_STATUSES:
            closed += 1
            continue
        if zero_cost_reason(o) is not None:
            o.est_parts = Decimal("0")
            set_cnt += 1
            continue
        _mpp = _multi_product_parts(db, o)
        if _mpp is not None:
            o.est_parts = _mpp.quantize(_CENTS)
            set_cnt += 1
            continue
        pu = _pricing_parts_for(db, o)
        if pu is not None:
            _uc = _pricing_cost_for(db, o)   # 真多件判据(与 theoretical 同源单件成本)
            eff = _effective_qty(o, _uc) if _uc is not None else 1
            o.est_parts = (pu * eff).quantize(_CENTS)
            set_cnt += 1
        else:
            no_pricing += 1
    db.flush()
    return {
        "set": set_cnt,
        "skipped_no_pricing": no_pricing,
        "skipped_closed": closed,
        "total": len(orders),
    }
