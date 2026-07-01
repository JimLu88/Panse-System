# -*- coding: utf-8 -*-
"""统一财务口径: 会计总成本 + 利润 (用户拍板 2026-06-17)。全系统唯一来源, 不再各处各算。

会计总成本(每单) = 物理产品成本 + 物流 + 安装/上楼 + 售后 + 平台扣点 + 税费
  - 物理产品成本 = actual_cost(工厂实报, 含木作/打包/外采配件) 否则 theoretical_cost(推算)
  - 物流        = actual_freight
  - 安装/上楼   = install_fee + upstairs_fee
  - 售后        = 本单实际(有则用) 否则 全局均值(预测/缺失时, 用户拍板)
  - 平台扣点    = (实付 − 店铺实收) 若实收>0  否则  实付×(手续费率0.6% + 活动抽成率2%[按生效月])
  - 税费        = 本单 tax 有则用, 否则 实付×税率2%
利润 = 实付 − 退款 − 会计总成本

费率在「管理→财务系数设置」配 (改动 2次警告+密码)。settings key: fin_*。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.services import settings_service

# 默认费率 (用户拍板 2026-06-17)
DEFAULTS = {
    "fin_platform_handling_rate": "0.006",        # 平台手续费 0.6%
    "fin_platform_activity_rate": "0.02",         # 平台活动抽成 2% (如有)
    "fin_platform_activity_since": "2026-05-01",  # 活动抽成生效起始日 (5月起有, 1-4月无)
    "fin_platform_activity_until": "2026-06-30",  # 活动抽成截止日 (只 5-6 月有, 7 月起无, 用户拍板 2026-06-21)
    "fin_tax_rate": "0.02",                       # 税费 2%
    "fin_outsourcing_monthly": "10000",           # 人员外包预估 (无实际录入时, 5月起按此/月预估)
    "fin_outsourcing_est_since": "2026-05-01",    # 人员外包预估生效起始月
    "fin_refill_commission_rate": "0",            # 刷单(补单)佣金率 (占刷单流水, 默认0=没付佣金)
    "fin_wood_cost_ratio": "",                    # 木作占比(定制单非木作推算; 空=自动取定价表中位≈0.67), 用户 2026-06-25 选A
    # 定制成本v2 灰度开关 (用户 2026-06-29): 默认 "0"=关=旧口径完全不变。开="1": 方案A(有真实工厂账单的定制单
    # 不再兜底实付×85%)+方案4(非木作改用定价表配件 est_parts+物流+安装, 不再木作÷占比放大), 且**仅对
    # est_parts>0(定价表配件可信)的有账单定制单生效**; est_parts=0/空(配件不可信)仍走旧口径占比放大+兜底(分桶安全闸)。
    "fin_custom_cost_v2": "0",
}
COEF_LABELS = {
    "fin_platform_handling_rate": "平台手续费率",
    "fin_platform_activity_rate": "平台活动抽成率",
    "fin_platform_activity_since": "平台活动抽成生效起始日",
    "fin_platform_activity_until": "平台活动抽成截止日",
    "fin_tax_rate": "税率",
    "fin_outsourcing_monthly": "人员外包预估(元/月)",
    "fin_outsourcing_est_since": "人员外包预估生效起始月",
    "fin_refill_commission_rate": "刷单佣金率(占刷单流水)",
    "fin_wood_cost_ratio": "木作占比(定制单非木作推算; 空=自动取定价表中位)",
    "fin_custom_cost_v2": "定制成本v2(方案A去兜底+方案4定价表配件; 默认关; 仅对配件可信的有账单定制单生效)",
}

# 售后费用字段 (订单总表内冗余列; 缺则用均值)
_AS_FIELDS = ("compensation_fee", "good_review_refund", "second_visit_fee",
              "return_pack_freight", "factory_compensation", "logistics_compensation")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal("0")


# 木作占比 (用户 2026-06-25 选A): 定制单工厂账单只含木作, base-SKU 定价表非木作不准 →
# 改按 整件物理成本 ≈ 木作账单 ÷ 木作占比 推算(非木作随真实账单等比放大, 不靠定价表)。
# 占比默认取定价表中位(≈0.67), 可在财务系数(fin_wood_cost_ratio)调。
_WOOD_RATIO_DEFAULT = Decimal("0.67")
_WOOD_EST_MAX_MARGIN = Decimal("0.15")   # 定制单 floor (用户 2026-06-26 选A): 木作占比推算后毛利 > 此(即成本<实付×85%) → 兜底实付×85%, 让定制单成本恒≥实付×85%(净利≤~15%, 工厂木作账单只含木作不足信)。floor 只升不降: 账单高于此则保留真实推算
_CUSTOM_FLOOR_MIN_PAID = Decimal("1500")  # 定制 floor 下限: 实付<此 的小额单(仅追加插座/隔板/差价片段, 成本就该是那点) 不 floor
_wood_ratio_cache = {"v": _WOOD_RATIO_DEFAULT}
# 定制成本v2 灰度开关 模块缓存 (load_coefficients 刷新, 供 physical_cost_breakdown 无 coef 时读)。默认关。
_cost_v2_cache = {"on": False}


def custom_cost_v2_on() -> bool:
    """定制成本v2 是否开启 (模块缓存; load_coefficients 时按设置 fin_custom_cost_v2 刷新)。默认 False=旧口径。"""
    return _cost_v2_cache["on"]


def _pricing_wood_ratio_median(db: Session):
    """定价表 木作占比 = wood_cost / (physical_cost − packaging_cost) 中位数; 无数据返回 None。

    分母排除打包(2026-06-26): 让占比反推出的"整件物理"不含打包, 打包再由 physical_cost_breakdown
    单独 +packing 算一次, 否则定制单(木作÷占比已隐含打包)会与 packing 重复。
    """
    from app.models.pricing import PricingSku
    rs = []
    for w, p, pk in db.execute(select(
            PricingSku.wood_cost, PricingSku.physical_cost, PricingSku.packaging_cost)).all():
        denom = (float(p) - float(pk or 0)) if p is not None else 0.0
        if w and denom > 0:
            r = float(w) / denom
            if 0.1 <= r <= 1.0:
                rs.append(r)
    if not rs:
        return None
    rs.sort()
    return Decimal(str(round(rs[len(rs) // 2], 2)))


def wood_cost_ratio() -> Decimal:
    """当前木作占比 (模块缓存; load_coefficients 时按 设置→定价表中位→0.67 刷新)。"""
    return _wood_ratio_cache["v"]


def load_coefficients(db: Session) -> dict:
    """读财务系数 (没配用默认)。返回含解析好的 Decimal/date。"""
    raw = {k: (settings_service.get(db, k, env_fallback=False) or dv) for k, dv in DEFAULTS.items()}
    out = dict(raw)
    out["handling_rate"] = _d(raw["fin_platform_handling_rate"])
    out["activity_rate"] = _d(raw["fin_platform_activity_rate"])
    out["tax_rate"] = _d(raw["fin_tax_rate"])
    try:
        y, m, dd = (int(x) for x in str(raw["fin_platform_activity_since"]).split("-"))
        out["activity_since"] = date(y, m, dd)
    except Exception:  # noqa: BLE001
        out["activity_since"] = date(2026, 5, 1)
    try:
        y, m, dd = (int(x) for x in str(raw["fin_platform_activity_until"]).split("-"))
        out["activity_until"] = date(y, m, dd)
    except Exception:  # noqa: BLE001
        out["activity_until"] = date(2026, 6, 30)
    # 木作占比 (定制单非木作推算): 设置优先, 否则定价表中位, 兜底 0.67, clamp [0.3, 0.95]
    _wr = str(raw.get("fin_wood_cost_ratio") or "").strip()
    wr = _d(_wr) if _wr else (_pricing_wood_ratio_median(db) or _WOOD_RATIO_DEFAULT)
    if not (Decimal("0.3") <= wr <= Decimal("0.95")):
        wr = _WOOD_RATIO_DEFAULT
    out["wood_cost_ratio"] = wr
    _wood_ratio_cache["v"] = wr        # 刷新模块缓存, 供 physical_cost_breakdown(无 coef) 用
    # 定制成本v2 灰度开关刷新 (默认关; 任何非真值字符串都视为关)
    out["custom_cost_v2"] = str(raw.get("fin_custom_cost_v2") or "0").strip().lower() in ("1", "true", "on", "yes")
    _cost_v2_cache["on"] = out["custom_cost_v2"]
    return out


def order_aftersales(o: Order) -> Decimal:
    """本单实际售后费 (各冗余列之和)。"""
    return sum((_d(getattr(o, f, 0)) for f in _AS_FIELDS), Decimal("0"))


def aftersales_avg(db: Session) -> Decimal:
    """全局人均售后费 = 全部售后费 ÷ 订单数 (缺失/预测时填它, 用户拍板"算个总均值")。"""
    sums = db.execute(select(*[func.coalesce(func.sum(getattr(Order, f)), 0) for f in _AS_FIELDS])).one()
    total = sum((_d(x) for x in sums), Decimal("0"))
    cnt = db.execute(select(func.count(Order.id))).scalar() or 0
    return (total / cnt).quantize(Decimal("0.01")) if cnt else Decimal("0")


def physical_cost_breakdown(o: Order) -> dict:
    """物理产品成本(商品成本)的加法拆解 + 封顶 —— 供逐单核对导出逐项公式回推。physical_cost() 复用本函数。

    返回:
      factory_wood   工厂账单木作(actual_cost; 无账单=0)
      estimate_part  定价表估算(配件+物流+安装; 含物流/安装实际调整。推演单=定价表物理整件)
      packing        打包(实际优先否则预估)
      precap_total   = factory_wood + estimate_part + packing (封顶前)
      cap_mode       封顶模式: none / 缺配件85 / 推演封顶85 / 片段85
      cap_label      封顶说明
      final          最终物理成本(封顶后); 触发封顶时 = 实付×0.85

    口径同历史(2026-06-20/25): 工厂账单只含木作→非木作按定价表补; 定制单缺配件→至少实付×85%;
    推演成本>实付→实付×85%; 定金/分期/差价片段(实付<成本×50%)→实付×85%。
    """
    from app.services import sku_utils
    nz = lambda a, e: _d(a) if a is not None else _d(e)   # 实际优先, 否则预估
    paid = _d(o.paid_amount)
    _is_custom = bool(getattr(o, "is_custom", False)) or sku_utils.is_custom_sku_code(
        getattr(o, "sku_code", None), getattr(o, "product_code", None))
    _al, _el = getattr(o, "actual_logistics", None), getattr(o, "est_logistics", None)
    _ai, _ei = getattr(o, "actual_install", None), getattr(o, "est_install", None)
    packing = nz(getattr(o, "actual_packing", None), getattr(o, "est_packing", None))
    # 嵌入 theoretical 的预估打包(定价表 physical_cost 含 packaging, theoretical=physical×件数 故含打包)。
    # 凡 estimate_part 从 theoretical 派生的分支, 先减掉它, 再由上面 packing 项统一算一次(防与打包重复, 2026-06-26 修)。
    est_pack = _d(getattr(o, "est_packing", None))
    cap_mode, cap_label = "none", ""

    # 非产品单(官方服务/专链/邮费/补拍/安装/送货)整单成本归零 (用户 2026-06-26; 复用系统权威检测器
    # zero_cost_reason — 不含样块/样品[另按实际¥13], 不含差价[走片段规则])。否则残留 配件/物流 估值。
    # 定制单排除(2026-07-02 修): 关键词只查标题/SKU, 不看备注 —— 定制单常年用「差价邮费补拍专链」
    # 这类通用链接收补发配件的钱(如"两个T25插座"), 备注里有真实成本依据(custom_order_reconcile_service
    # 已按插座/配件规则推演写回 theoretical_cost); 整单归零会把这笔已推演的真实成本吃掉。定制单交下面
    # 走 theoretical_cost, 该有的封顶/保底(推演封顶85/定制兜底85)照样生效, 不会因此算出离谱数字。
    from app.services.order_cost_service import zero_cost_reason
    if not bool(getattr(o, "is_refill", False)) and not _is_custom and zero_cost_reason(o):
        return {"factory_wood": Decimal("0"), "estimate_part": Decimal("0"), "packing": Decimal("0"),
                "precap_total": Decimal("0"), "cap_mode": "非产品归零",
                "cap_label": "官方服务/专链/邮费/补拍 非产品 → 成本0", "final": Decimal("0")}

    # 逐单真实配件 (用户 2026-06-26): actual_parts 非空 → 改逐项真实计价(木作+物流+安装+打包+真实配件),
    # 跳过占比估算与实付×85% floor。各分项 actual 优先否则 est; 木作取工厂账单否则定价木作估。
    _aparts = getattr(o, "actual_parts", None)
    if _aparts is not None:
        factory_wood = nz(o.actual_cost, getattr(o, "wood_cost_est", None))
        estimate_part = nz(_al, _el) + nz(_ai, _ei) + _d(_aparts)   # 物流 + 安装 + 真实配件
        precap = factory_wood + estimate_part + packing
        cost = precap if precap > 0 else Decimal("0")
        return {
            "factory_wood": factory_wood, "estimate_part": estimate_part, "packing": packing,
            "precap_total": precap, "cap_mode": "实配件分项",
            "cap_label": "逐单真实配件→逐项真实计价(木作+物流+安装+打包+真实配件), 不估不封顶", "final": cost,
        }

    if o.actual_cost is not None:
        factory_wood = _d(o.actual_cost)   # 工厂账单 = 木作
        if _is_custom:
            _ep = _d(getattr(o, "est_parts", None))
            if custom_cost_v2_on() and _ep > 0:
                # 定制成本v2 灰度(用户 2026-06-29, 默认关; 仅 est_parts>0 配件可信 才进):
                #   方案4: 非木作 = 定价表配件(est_parts) + 物流 + 安装 (不再木作÷占比放大);
                #   方案A: 设 cap_mode='v2实配件' → 下方"定制兜底85"因 cap_mode!='none' 自动跳过(不再兜底)。
                estimate_part = _ep + nz(_al, _el) + nz(_ai, _ei)
                precap = factory_wood + estimate_part + packing
                cost = precap
                cap_mode, cap_label = "v2实配件", "定制v2: 非木作=定价表配件+物流+安装(不÷占比); 有账单不兜底"
            else:
                # 旧口径 / 配件不可信(est_parts=0)分桶回退(用户 2026-06-25 选A): base-SKU 定价表非木作不准
                # → 非木作 = 木作账单×(1/占比−1) 推算, 整件物理 ≈ 木作账单 ÷ 木作占比, 下方仍兜底实付×85%。
                ratio = wood_cost_ratio()
                estimate_part = (factory_wood * (Decimal("1") / ratio - Decimal("1"))
                                 if (ratio > 0 and factory_wood > 0) else Decimal("0"))
                precap = factory_wood + estimate_part + packing
                cost = precap
        else:
            # 非定制单: 定价表准 → 用定价表补非木作(配件+物流+安装 = 定价表物理 − 木作)
            wood_est = _d(getattr(o, "wood_cost_est", None))
            can_reconstruct = (wood_est > 0 and o.theoretical_cost is not None
                               and (_d(o.theoretical_cost) - wood_est) > 0)
            if can_reconstruct:
                estimate_part = _d(o.theoretical_cost) - wood_est - est_pack   # 减嵌入打包(下面统一 +packing 一次)
                if _al is not None and _el is not None:
                    estimate_part += _d(_al) - _d(_el)              # 物流换实际(差额)
                if _ai is not None and _ei is not None:
                    estimate_part += _d(_ai) - _d(_ei)              # 安装换实际(差额)
            else:
                estimate_part = nz(_al, _el) + nz(_ai, _ei)         # 无定价参照: 补 物流+安装
            precap = factory_wood + estimate_part + packing
            cost = precap
    else:
        factory_wood = Decimal("0")
        estimate_part = _d(o.theoretical_cost)   # 定价表物理(含物流/安装/打包预估)
        if not _is_custom:
            estimate_part -= est_pack   # 非定制 theoretical=定价表physical(含打包)→减嵌入打包; 定制 theoretical 来自定制报价/兜底(不含打包)不减
        if _al is not None and _el is not None:
            estimate_part += _d(_al) - _d(_el)
        if _ai is not None and _ei is not None:
            estimate_part += _d(_ai) - _d(_ei)
        precap = factory_wood + estimate_part + packing
        cost = precap
        # 推演单(无工厂账单): 推演成本 > 实付 → 实付×85%封顶(追加/差价/凑单片段, 用户 2026-06-25 选A)
        if paid > 0 and cost > paid:
            cost = (paid * Decimal("0.85")).quantize(Decimal("0.01"))
            cap_mode, cap_label = "推演封顶85", "推演成本>实付→实付×85%封顶"
    # 定制单 floor (用户 2026-06-26 选A): 不论有无工厂账单, 定制单成本恒兜底至 实付×85%
    # (= max(木作推算/定制报价, 实付×85%); 工厂木作账单只含木作、定制报价也常偏低 → 至少留~15%毛利)。
    # 仅排除小额追加/差价片段(实付<_CUSTOM_FLOOR_MIN_PAID, 成本就该是那点); cap_mode 仍 none 才 floor
    # (推演封顶85 已把超实付的压到实付×85%, 不重复)。
    if (_is_custom and cap_mode == "none" and paid >= _CUSTOM_FLOOR_MIN_PAID
            and cost < paid * (Decimal("1") - _WOOD_EST_MAX_MARGIN)):
        cost = (paid * Decimal("0.85")).quantize(Decimal("0.01"))
        cap_mode, cap_label = "定制兜底85", "定制单成本兜底至实付×85%(工厂木作账单/定制报价不足信)"
    if cost < 0:
        cost = Decimal("0")
        if cap_mode == "none":   # 加法分量算出负数(罕见, 物流/安装实际远小于预估)→ 归零, 标记便于导出按实值显示
            cap_mode, cap_label = "归零", "成本分量计算为负→归零"
    # 片段封顶(定金/分期/差价: 实付 < 成本×50% → 不背整份成本)
    if cost > 0 and paid > 0 and paid < cost * Decimal("0.5"):
        cost = (paid * Decimal("0.85")).quantize(Decimal("0.01"))
        cap_mode, cap_label = "片段85", "片段(实付<成本×50%)→实付×85%封顶"
    return {
        "factory_wood": factory_wood, "estimate_part": estimate_part, "packing": packing,
        "precap_total": precap, "cap_mode": cap_mode, "cap_label": cap_label, "final": cost,
    }


def physical_cost(o: Order) -> Decimal:
    """物理产品成本 = 工厂实报成本优先, 否则系统推算 (含木作/打包/外采配件)。

    实现委托 physical_cost_breakdown(o)["final"](单一真源, 同时供导出逐项公式回推)。口径:
    非木作补回(工厂账单只含木作→按定价表补配件/物流/安装) / 定制单缺配件→实付×85% /
    推演成本>实付→实付×85% / 定金片段(实付<成本×50%)→实付×85%。详见 physical_cost_breakdown。"""
    return physical_cost_breakdown(o)["final"]


def platform_deduction(o: Order, coef: dict) -> Decimal:
    """平台扣点: 有店铺实收→实付−实收(真实); 否则 实付×(手续费率+活动抽成率[按生效月])。

    护栏(2026-06-20): 分期购/部分到账单 实收 远小于 实付, 直接取 实付−实收 会把"未到账分期款"
    误当平台费(实测 5117408713503179541 分期被算成实付58%的扣点, 单笔虚增费用¥3698)。
    故 实付−实收 超过合理扣点上限(实付×8%, 正常平台扣点仅2-4%)时, 判为部分到账, 改用率算法。"""
    paid = _d(o.paid_amount)
    recv = _d(o.shop_received_amount)
    rate = coef["handling_rate"]
    # 活动抽成只在 [生效日, 截止日] 区间内加 (用户拍板 2026-06-21: 只 5-6 月有活动, 4月前/7月后都没有)。
    # activity_until 缺省(老式手搓 coef)→ 退回只有下界的旧行为, 防 KeyError。
    _until = coef.get("activity_until")
    if (o.order_date and o.order_date >= coef["activity_since"]
            and (_until is None or o.order_date <= _until)):
        rate += coef["activity_rate"]
    if recv > 0 and recv < paid:
        # recv 视为退款后净额→扣退款只留纯平台费(防退款双扣: 收入侧已减过退款)。
        # 钳制(2026-06-26): 退款最多扣到 实付−实收, 防个别单 recv 非退款后净额(毛额)时把退款多扣→平台费虚低/转负落率算法。
        _gap = paid - recv
        diff = _gap - min(_d(getattr(o, "refund_amount", 0)), _gap)
        if Decimal("0") <= diff <= paid * Decimal("0.08"):
            return diff
        # diff 远超合理扣点 → 分期/部分到账, 落到下面率算法, 不把未到账款当平台费
    return (paid * rate).quantize(Decimal("0.01"))


def order_tax(o: Order, coef: dict) -> Decimal:
    """税费: 本单已填 tax 用它, 否则 实付×税率。"""
    if o.tax is not None:
        return _d(o.tax)
    return (_d(o.paid_amount) * coef["tax_rate"]).quantize(Decimal("0.01"))


def cost_breakdown(o: Order, coef: dict, as_avg: Decimal = Decimal("0"),
                   aftersales: "Decimal | None" = None) -> dict:
    """会计总成本逐项明细 (供页面"说明里列明细")。
    aftersales 显式传入(按订单归属, 退款外额外售后)时直接用它; 否则回退 本单冗余列→人均均摊(旧)。"""
    from app.services import sku_utils
    paid = _d(o.paid_amount)
    phys = physical_cost(o)
    # 双算护栏(2026-06-20): theoretical_cost(=定价表物理总成本)已内含预测物流+安装;
    # 用 theoretical 的单不再单独加运费/安装行(否则双算)。
    # 已补非木作的工厂账单单(wood_cost_est 非空): physical_cost 已用 theoretical 的非木作部分
    # (含预测物流安装)补回, 此处也不再单独加(防双算)。
    # 定制单(2026-06-26): physical 走木作占比反推已隐含物流安装, 也不单独加(否则双算; 定制恒无 wood_cost_est)。
    # 仅"未补非木作的纯工厂账单单"(actual_cost 非空 且 wood_cost_est 空 且 非定制)才单独加实际运费/安装(向后兼容)。
    _is_custom_o = bool(getattr(o, "is_custom", False)) or sku_utils.is_custom_sku_code(
        getattr(o, "sku_code", None), getattr(o, "product_code", None))
    if o.actual_cost is not None and not _d(getattr(o, "wood_cost_est", None)) and not _is_custom_o:
        freight = _d(o.actual_freight)
        install = _d(o.install_fee) + _d(o.upstairs_fee)
    else:
        freight = Decimal("0")
        install = Decimal("0")
    if aftersales is not None:
        asales = _d(aftersales)
        asales_est = False
    else:
        asales = order_aftersales(o)
        asales_est = asales == 0
        if asales_est:
            asales = as_avg
    platform = platform_deduction(o, coef)
    tax = order_tax(o, coef)
    total = phys + freight + install + asales + platform + tax
    return {
        "physical": phys, "freight": freight, "install_upstairs": install,
        "aftersales": asales, "aftersales_estimated": asales_est,
        "platform": platform, "tax": tax, "total": total, "paid": paid,
    }


def accounting_cost(o: Order, coef: dict, as_avg: Decimal = Decimal("0"),
                    aftersales: "Decimal | None" = None) -> Decimal:
    """会计总成本 (全扣项)。"""
    return cost_breakdown(o, coef, as_avg, aftersales)["total"]


def net_profit(o: Order, coef: dict, as_avg: Decimal = Decimal("0"),
               aftersales: "Decimal | None" = None) -> Decimal:
    """利润 = 实付 − 退款 − 会计总成本。"""
    return _d(o.paid_amount) - _d(o.refund_amount) - accounting_cost(o, coef, as_avg, aftersales)


# ── 月度/区间报表共用口径 (月度经营数据 与 经营状况 统一, 用户拍板 2026-06-17) ──────────────
# 退款之外的"额外售后"列: 直接赔付客户/二次上门/返厂打包运费/补发运费/万师傅扣款/好评返。
# 不含 平台内·外售后总成本 与 订单赔付费 —— 那些多半就是已从收入扣过的退款, 再计会重复 (用户 Q2 拍板)。
_AS_EXTRA_FIELDS = ("direct_compensation", "second_visit_fee", "return_pack_freight",
                    "refill_freight", "wanshifu_deduction", "good_review_refund")


def extra_aftersales(db: Session, start: date, end: date) -> Decimal:
    """区间内"退款之外"的额外售后支出合计 (按 processed_at)。两个报表共用, 口径一致。"""
    from app.models.marketing import AfterSales
    cols = [func.coalesce(func.sum(getattr(AfterSales, f)), 0) for f in _AS_EXTRA_FIELDS]
    row = db.execute(
        select(*cols).where(AfterSales.processed_at >= start, AfterSales.processed_at <= end)
    ).one()
    return sum((_d(x) for x in row), Decimal("0"))


_DEFAULT_FIXED_COSTS = [{"name": "房租", "amount": 40000, "period": "yearly", "active": True}]


def fixed_cost_items(db: Session) -> list[dict]:
    """自定义固定成本/管理费用项 (房租/水电/软件/折旧…)。存 setting fin_fixed_cost_items(JSON)。
    未设置过 → 返回默认 [房租 ¥40000/年]; 已设置(哪怕空[]) → 用存的, 这样用户可自由增删 (用户拍板 2026-06-18)。"""
    import json
    raw = settings_service.get(db, "fin_fixed_cost_items", env_fallback=False)
    if raw is None:
        return [dict(x) for x in _DEFAULT_FIXED_COSTS]
    try:
        items = json.loads(raw)
        return items if isinstance(items, list) else []
    except Exception:  # noqa: BLE001
        return []


def fixed_costs_monthly(db: Session) -> Decimal:
    """每月固定成本合计 (年度项 ÷12)。"""
    total = Decimal("0")
    for it in fixed_cost_items(db):
        if not it.get("active", True):
            continue
        amt = _d(it.get("amount"))
        if str(it.get("period")) == "yearly":
            amt = amt / 12
        total += amt
    return total.quantize(Decimal("0.01"))


def refill_cost(db: Session, start: date, end: date, coef: dict) -> dict:
    """补单=刷单 的纯成本 (用户拍板 2026-06-18): 流水本金来回滚抵销(非收入), 真正花出去的是
    平台扣点 + 税费 + 运费 + 佣金。不计商品成本(刷单货回流/不消耗), 不计收入(本金回流)。
    佣金 = 刷单流水(实付) × fin_refill_commission_rate (默认0, 在财务系数设置里填)。"""
    from app.models.order import Order
    from app.services.sales_analytics import settled_sale_clause
    orders = db.execute(
        select(Order).where(
            Order.is_refill == True,  # noqa: E712
            settled_sale_clause(),    # 关闭/取消/全退的补单不计刷单成本(用户铁律: 关闭单100%排除出财务)
            Order.order_date >= start, Order.order_date <= end)
    ).scalars().all()
    gmv = platform = tax = freight = Decimal("0")
    for o in orders:
        gmv += _d(o.paid_amount)
        platform += platform_deduction(o, coef)
        tax += order_tax(o, coef)
        freight += _d(o.actual_freight)
    commission = (gmv * _d(coef.get("fin_refill_commission_rate") or "0")).quantize(Decimal("0.01"))
    total = (platform + tax + freight + commission).quantize(Decimal("0.01"))
    return {"count": len(orders), "gmv": gmv.quantize(Decimal("0.01")),
            "platform": platform.quantize(Decimal("0.01")), "tax": tax.quantize(Decimal("0.01")),
            "freight": freight.quantize(Decimal("0.01")), "commission": commission, "total": total}


def accounting_summary(db: Session, start: date, end: date) -> dict:
    """全系统统一会计 P&L (用户拍板 2026-06-18: 月度经营/经营状况/逐单核对/销售汇总/大盘 同口径)。

    收入 = Σ(实付 − 退款)  [真实成交、非补单]
    逐单成本 = 商品 + 物流 + 安装上楼 + 平台扣点(实付−实收) + 税 + 额外售后(按订单归属, 退款不重复计)
    区间成本 = 推广 + 人员外包 + 固定成本(房租等) + 补单(刷单)成本
    净利 = 收入 − 逐单成本 − 区间成本
    退款≠售后: 退款已从收入扣过, 售后只算退款之外的额外赔付。
    """
    from app.models.marketing import PromotionFlow
    from app.models.order import Order
    from app.services import sales_analytics
    coef = load_coefficients(db)
    as_by_order = extra_aftersales_by_order(db)
    orders = db.execute(
        select(Order).where(
            Order.order_date >= start, Order.order_date <= end,
            sales_analytics.settled_sale_clause(), Order.is_refill == False)  # noqa: E712
    ).scalars().all()
    revenue = refund = goods = freight = install = platform = tax = aftersales = Decimal("0")
    goods_est = False
    as_count = 0
    for o in orders:
        rf = _d(o.refund_amount)
        revenue += _d(o.paid_amount) - rf
        refund += rf
        b = cost_breakdown(o, coef, Decimal("0"))   # as_avg=0: 不用人均均摊, 售后改按订单
        goods += b["physical"]; freight += b["freight"]; install += b["install_upstairs"]
        platform += b["platform"]; tax += b["tax"]
        _as = _d(as_by_order.get(o.order_no, 0))
        aftersales += _as
        if _as > 0:
            as_count += 1
        if o.actual_cost is None:
            goods_est = True   # 用推演商品成本(工厂未对账)
    promo = _d(db.execute(
        select(func.coalesce(func.sum(PromotionFlow.amount), 0)).where(
            PromotionFlow.flow_type == "支出",
            PromotionFlow.transaction_date >= start, PromotionFlow.transaction_date <= end)
    ).scalar() or 0)
    outsourcing, os_est = outsourcing_for_range(db, start, end, coef)
    fixed = fixed_costs_monthly(db)
    rc = refill_cost(db, start, end, coef)
    order_cost = goods + freight + install + platform + tax + aftersales
    period_cost = promo + outsourcing + fixed + rc["total"]
    net = revenue - order_cost - period_cost
    return {
        "count": len(orders), "revenue": revenue, "refund": refund,
        "goods": goods, "goods_estimated": goods_est,
        "freight": freight, "install": install, "platform": platform,
        "tax": tax, "aftersales": aftersales, "aftersales_count": as_count, "order_cost": order_cost,
        "promo": promo, "outsourcing": outsourcing, "outsourcing_estimated": os_est,
        "fixed": fixed, "refill": rc, "period_cost": period_cost,
        "total_cost": order_cost + period_cost, "net": net,
        "net_margin": (net / revenue * 100) if revenue else Decimal("0"),
        "coef": coef,
    }


def extra_aftersales_by_order(db: Session) -> dict[str, Decimal]:
    """各订单"退款之外"的额外售后合计 (after_sales.platform_order_no → 金额)。逐单核对用。"""
    from app.models.marketing import AfterSales
    cols = [func.coalesce(func.sum(getattr(AfterSales, f)), 0) for f in _AS_EXTRA_FIELDS]
    out: dict[str, Decimal] = {}
    for row in db.execute(
        select(AfterSales.platform_order_no, *cols).group_by(AfterSales.platform_order_no)
    ).all():
        if row[0]:
            out[row[0]] = sum((_d(x) for x in row[1:]), Decimal("0"))
    return out


def _iter_months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def outsourcing_for_range(db: Session, start: date, end: date, coef: dict) -> tuple[Decimal, bool]:
    """人员外包 (G): 每月预估 = Σ 当月在职人员月工资 (staff_salary_service.monthly_total);
    该月无在职人员(合计=0)时回落 coef fin_outsourcing_monthly。
    当月有实际 OutsourcingExpense 录入时用实际, 但实际 < 工资预估则取工资预估(地板,
    防部分录入月漏算)。返回 (合计, 是否含预估)。
    """
    from app.models.marketing import OutsourcingExpense
    from app.services import staff_salary_service
    fallback_est = _d(coef.get("fin_outsourcing_monthly") or "10000")
    try:
        ey, em, _ = (int(x) for x in str(coef.get("fin_outsourcing_est_since") or "2026-05-01").split("-"))
        est_since = date(ey, em, 1)
    except Exception:  # noqa: BLE001
        est_since = date(2026, 5, 1)
    # 按年月聚合在 Python 做 (跨库: to_char 只 Postgres 有, sqlite 单测会崩)。
    rows = db.execute(
        select(OutsourcingExpense.payment_date, OutsourcingExpense.amount)
        .where(OutsourcingExpense.payment_date >= start, OutsourcingExpense.payment_date <= end)
    ).all()
    actual: dict[str, Decimal] = {}
    for pdate, amt in rows:
        if pdate is None:
            continue
        ym = f"{pdate.year}-{pdate.month:02d}"
        actual[ym] = actual.get(ym, Decimal("0")) + _d(amt)
    total = Decimal("0")
    estimated = False
    for y, m in _iter_months(start, end):
        # 月度人力成本: Σ当月在职人员月工资 (每人有明确在职起 active_from, 本身即时间门)。
        salary_est = staff_salary_service.monthly_total(db, y, m)
        a = actual.get(f"{y}-{m:02d}", Decimal("0"))
        if salary_est > 0:
            # 有在职人员: 工资即该月人力成本, 不受 est_since 限制 (在职起已是时间门)。
            # 实际外包录入更高则取实际(地板防漏), 否则用工资。
            if a > salary_est:
                total += a
            else:
                total += salary_est
                estimated = True
        elif a > 0:
            total += a  # 无在职人员但有实际外包记录 → 用实际
        elif date(y, m, 1) >= est_since:
            total += fallback_est  # 无人无实际 → 回落写死占位 (仅 est_since 后, 防历史月凭空加成本)
            estimated = True
    return total, estimated
