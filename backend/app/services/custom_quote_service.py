"""全定制报价计算引擎 (Module ④ - 完全定制).

完全复刻用户「全定制算价 v0.5」表格的计价管线 (一块板一块板按面积):

  每条木板成本 = 单价 × 长 × 宽 × 数量   (计价单位=平方米)
              = 单价 × 长 × 数量          (米, 如灯带/轨道槽)
              = 单价 × 数量               (个/组/付/件)

  厂内总成本   = Σ木板成本 + 工厂人工费(按 产品×规格 查)
  工厂利润     = 厂内总成本 × 工厂利润系数 (默认 0.25)
  工厂木作总成本 = 厂内总成本 + 工厂利润           ← 与工厂报价(木作+抽屉轨道)对比
  畔色成本     = 工厂木作总成本 + 配件 + 打包 + 运费 + 安装
  最终报价     = 畔色成本 / (1 − 畔色利润系数)    (默认 0.15 → ÷0.85)

旁边附「投影面积估算」做错算/漏板的对照: 正面投影(宽×高) × 经验系数。
系数与利润率都可由后台(配件价格表/系统设置)调整, 这里给默认值。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

_CENTS = Decimal("0.01")

# 可后台调整的默认参数
DEFAULT_FACTORY_PROFIT_RATE = Decimal("0.25")   # 工厂利润系数
DEFAULT_PANSE_PROFIT_RATE = Decimal("0.15")     # 畔色利润系数 (售价毛利, 除法)
DEFAULT_PROJECTION_RATE = Decimal("900")        # 投影面积对照系数 (元/㎡正面投影), 可调

# 按面积计价的单位关键字
_AREA_UNITS = {"平方米", "㎡", "m2", "平米"}
_LENGTH_UNITS = {"米", "m"}


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _q(v: Decimal) -> Decimal:
    return v.quantize(_CENTS, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    """一条木板 / 配件明细。"""
    part: str                         # 部位 / 名称
    material: str                     # 对应材料
    unit_price: Decimal               # 单价 (按 unit)
    qty: Decimal = Decimal("1")
    length_m: Optional[Decimal] = None   # 长/直径 (米)
    width_m: Optional[Decimal] = None    # 宽/高度 (米)
    unit: str = "平方米"              # 计价单位
    is_drawer_rail: bool = False      # 抽屉轨道 (工厂报价含, 对比时计入)

    def cost(self) -> Decimal:
        p, qty = _d(self.unit_price), _d(self.qty)
        if self.unit in _AREA_UNITS:
            return _q(p * _d(self.length_m) * _d(self.width_m) * qty)
        if self.unit in _LENGTH_UNITS:
            return _q(p * _d(self.length_m) * qty)
        return _q(p * qty)   # 个/组/付/件


@dataclass
class QuoteResult:
    wood_cost: Decimal
    labor_fee: Decimal
    factory_in_cost: Decimal          # 厂内总成本
    factory_profit: Decimal           # 工厂利润
    factory_wood_total: Decimal       # 工厂木作总成本
    accessory_total: Decimal
    drawer_rail_total: Decimal        # 抽屉轨道小计 (并入工厂报价对比)
    packing_fee: Decimal
    freight: Decimal
    install_fee: Decimal
    panse_cost: Decimal               # 畔色成本
    final_quote: Decimal              # 最终报价
    factory_quote_compare: Decimal    # 木作总成本 + 抽屉轨道 → 对工厂报价
    projection_estimate: Optional[Decimal]   # 投影面积估算
    projection_area_m2: Optional[Decimal]
    wood_lines: list[dict] = field(default_factory=list)
    accessory_lines: list[dict] = field(default_factory=list)


def compute_quote(
    *,
    wood_lines: list[Line],
    accessory_lines: list[Line],
    labor_fee: Decimal = Decimal("0"),
    packing_fee: Decimal = Decimal("0"),
    freight: Decimal = Decimal("0"),
    install_fee: Decimal = Decimal("0"),
    factory_profit_rate: Decimal = DEFAULT_FACTORY_PROFIT_RATE,
    panse_profit_rate: Decimal = DEFAULT_PANSE_PROFIT_RATE,
    overall_width_m: Optional[Decimal] = None,
    overall_height_m: Optional[Decimal] = None,
    projection_rate: Decimal = DEFAULT_PROJECTION_RATE,
) -> QuoteResult:
    """按表格管线算一个完全定制产品的报价 + 工厂对比 + 投影面积对照。"""
    wood_cost = sum((ln.cost() for ln in wood_lines), Decimal("0"))
    labor_fee = _d(labor_fee)
    factory_in = _q(wood_cost + labor_fee)
    factory_profit = _q(factory_in * _d(factory_profit_rate))
    factory_wood_total = _q(factory_in + factory_profit)

    acc_total = sum((ln.cost() for ln in accessory_lines), Decimal("0"))
    drawer_rail_total = sum(
        (ln.cost() for ln in accessory_lines if ln.is_drawer_rail), Decimal("0")
    )

    packing_fee, freight, install_fee = _d(packing_fee), _d(freight), _d(install_fee)
    panse_cost = _q(factory_wood_total + acc_total + packing_fee + freight + install_fee)

    pr = _d(panse_profit_rate)
    final_quote = _q(panse_cost / (Decimal("1") - pr)) if pr < 1 else panse_cost

    # 工厂报价对比 = 木作总成本 + 抽屉轨道 (工厂只算木作+轨道)
    factory_compare = _q(factory_wood_total + drawer_rail_total)

    # 投影面积对照 (正面投影 宽×高)
    proj_area = proj_est = None
    if overall_width_m and overall_height_m:
        proj_area = _q(_d(overall_width_m) * _d(overall_height_m))
        proj_est = _q(proj_area * _d(projection_rate))

    return QuoteResult(
        wood_cost=_q(wood_cost),
        labor_fee=labor_fee,
        factory_in_cost=factory_in,
        factory_profit=factory_profit,
        factory_wood_total=factory_wood_total,
        accessory_total=_q(acc_total),
        drawer_rail_total=_q(drawer_rail_total),
        packing_fee=packing_fee,
        freight=freight,
        install_fee=install_fee,
        panse_cost=panse_cost,
        final_quote=final_quote,
        factory_quote_compare=factory_compare,
        projection_estimate=proj_est,
        projection_area_m2=proj_area,
        wood_lines=[{"part": l.part, "material": l.material, "cost": float(l.cost())} for l in wood_lines],
        accessory_lines=[{"part": l.part, "material": l.material, "cost": float(l.cost())} for l in accessory_lines],
    )


@dataclass
class BoardSpec:
    """前端/AI 给的一条板规格 (长宽单位 cm, 更贴近下料单)。"""
    part: str
    material: str
    length_cm: float
    width_cm: float
    qty: float = 1
    unit: str = "平方米"
    is_accessory: bool = False        # 配件(把手/灯带/轨道等), 不计入工厂木作
    is_drawer_rail: bool = False


def quote_from_spec(
    db,
    *,
    product_type: str,
    length_m: float,
    boards: list[BoardSpec],
    overall_width_m: Optional[float] = None,
    overall_height_m: Optional[float] = None,
) -> QuoteResult:
    """把"品类 + 板单"按后台配置(单价/人工/打包运费安装/系数)算出整套报价。

    板的长宽按 cm 传入, 自动换算 m。木材单价、人工费(品类×大小)、
    打包/运费/安装(按大小)、利润系数、投影系数全部从配置读。
    """
    from app.services import custom_quote_config_service as cfg_svc

    cfg = cfg_svc.get_config(db)
    prices = cfg.get("prices", {})

    def _price(mat: str) -> Decimal:
        return _d(prices.get(mat, prices.get(mat.split("-")[0], 0)))

    wood_lines, acc_lines = [], []
    for b in boards:
        ln = Line(
            part=b.part, material=b.material, unit_price=_price(b.material),
            qty=_d(b.qty),
            length_m=_d(b.length_cm) / 100 if b.length_cm else None,
            width_m=_d(b.width_cm) / 100 if b.width_cm else None,
            unit=b.unit, is_drawer_rail=b.is_drawer_rail,
        )
        (acc_lines if (b.is_accessory or b.is_drawer_rail) else wood_lines).append(ln)

    return compute_quote(
        wood_lines=wood_lines,
        accessory_lines=acc_lines,
        labor_fee=_d(cfg_svc.lookup_labor(cfg, product_type, length_m)),
        packing_fee=_d(cfg_svc.lookup_packing(cfg, product_type, length_m)),
        freight=_d(cfg_svc.lookup_freight(cfg, product_type, length_m)),
        install_fee=_d(cfg_svc.lookup_install(cfg, product_type, length_m)),
        factory_profit_rate=_d(cfg.get("factory_profit_rate", 0.25)),
        panse_profit_rate=_d(cfg.get("panse_profit_rate", 0.15)),
        overall_width_m=_d(overall_width_m) if overall_width_m else None,
        overall_height_m=_d(overall_height_m) if overall_height_m else None,
        projection_rate=_d(cfg.get("projection_rate", 900)),
    )


def material_diff_surcharge(
    *,
    new_unit_price: Decimal,
    base_unit_price: Decimal,
    area_m2: Decimal,
    factory_profit_rate: Decimal = DEFAULT_FACTORY_PROFIT_RATE,
) -> Decimal:
    """换料增量 (轻度定制): (新料价 − 原料价) × 面积 × (1 + 工厂利润系数)。

    例: 樱桃木→黑胡桃木, 单价差 × 面积, 工厂再赚 25% → × 1.25。
    """
    diff = (_d(new_unit_price) - _d(base_unit_price)) * _d(area_m2)
    return _q(diff * (Decimal("1") + _d(factory_profit_rate)))
