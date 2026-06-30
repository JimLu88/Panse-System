# -*- coding: utf-8 -*-
"""配件(外采)对账服务 — 配件 epic (用户 2026-06-26; 方向1: 分类 + BOM 驱动)。

三件事:
  A. aggregate_related_purchases —— 填了 related_order_no 的配件采购单按订单汇总写 Order.actual_parts。
  B. bulk_material_recon —— **按 Material.category 分组、BOM 驱动** 的对账: 每分类每发货月
     历史平均 | 预估(Σ发货单BOM里该类外采配件 price×qty) | 实际(工厂月度对账总额) | 差异%。
     **铁律: 消费窗口按订单「发货日期 ship_date」圈定(生产周期~30天)。** 取代旧硬编码关键词登记表。
  C. 工厂月度对账总额录入(PartsMonthlyRecon, material_key 存「分类」)。
  D. export_shipped_orders —— 导当月发货单清单(全部 / 按分类逐单展开 BOM 部位+预设尺寸)给工厂对账。

口径: 总账准(差异落当期); 逐单这块是有界/可校准的近似(物理限制绕不开)。
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material
from app.models.order import Order, PartPurchase, PartsMonthlyRecon
from app.services import sales_analytics

_CENTS = Decimal("0.01")

# 工厂自备/木作/木材/人工(WD-/MW-/MP-)在 BOM 里是工厂自备 → 不进 BOM 驱动的配件「标准消耗」对账。
_FACTORY_PREFIXES = ("WD", "MW", "MP")
# 但「支付宝自购采购」侧只排纯工厂服务(木作 WD / 人工 MP); 自购木材(MW, 如榉木自采、工厂未含)与
# 特殊件按用户口径(2026-06-29)当零星采购照常记入(不在 BOM 的会标 not_in_bom 待逐单核对)。
_PURCHASE_EXCLUDE_PREFIXES = ("WD", "MP")
_UNCATEGORIZED = "未分类"

# 结算模式 (用户 2026-06-27): 五金都在五金店买 → 月结(月度总额对账, 工厂/供应商填);
# 其余(杂项/洞石饰面板/木皮/大宗料…)= 零星采购(用了才买, 支付宝备注/导入逐项归账, 实际=Σ真实采购)。
# 月结类才"导清单给对方填总额"; 零星类没"对方", 实际从采购单(PartPurchase)来。
# 玻璃 2026-06-27 改月结(用户: 玻璃也有固定供应商按月对账)。
_MONTHLY_SETTLE_CATEGORIES = ("五金", "电力轨道", "岩板", "玻璃")   # 有固定供应商、按月对账的; 杂项/洞石饰面板/木皮等=零星


def _settle_mode(category: str) -> str:
    return "月结" if category in _MONTHLY_SETTLE_CATEGORIES else "零星"

# 非配件采购(代扣/理财/服务费/淘天扣款…)排除关键词 — 与 purchases.list_purchases 一致。
_NON_PART_KW = ("代扣", "代付", "资金扣回", "消费券", "理财", "申购",
                "服务费", "手续费", "余额宝", "转入", "转出", "单次转", "转账")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _ym(d: Optional[date]) -> Optional[str]:
    return f"{d.year:04d}-{d.month:02d}" if d else None


def _size_area(size: Optional[str]) -> Decimal:
    """从尺寸备注取前两个数相乘当"面积"(定制单同料合并时取最大者偏保守)。无数→0。"""
    if not size:
        return Decimal("0")
    nums = re.findall(r"\d+(?:\.\d+)?", size)
    if len(nums) >= 2:
        return Decimal(nums[0]) * Decimal(nums[1])
    return Decimal(nums[0]) if nums else Decimal("0")


def _looks_non_part(p: PartPurchase) -> bool:
    name = (p.material_name or "")
    if any(k in name for k in _NON_PART_KW):
        return True
    if p.supplier and "淘天" in p.supplier:
        return True
    # 纯工厂服务(木作 WD / 人工 MP)= 工厂账单覆盖, 不是自购配件 → 不进 actual_parts(防货款双算);
    # 自购木材(MW, 榉木自采)/特殊件按用户口径当零星照常记(用户 2026-06-29), 不在此排除。
    if (p.material_code or "").split("-", 1)[0].upper() in _PURCHASE_EXCLUDE_PREFIXES:
        return True
    return False


def _purchase_amount(p: PartPurchase) -> Optional[Decimal]:
    if p.total_amount is not None:
        return _d(p.total_amount)
    if p.amount is not None:
        return _d(p.amount)
    return None


# ── 零星采购 → 订单(多单按 BOM 占比分摊) 的共用拆分逻辑 ───────────────────────
def _split_order_nos(s: Optional[str]) -> list[str]:
    """related_order_no 拆成订单号 token 列表(一笔零星采购可对应多个平台订单号, \n/空格/逗号分隔)。

    按 token 精确比对 Order.order_no(不抽数字), 故 26-28 位支付宝商户号会作单 token 比对、对不上→
    自然落 unmatched, 不会误抠出假订单号; 同时兼容非纯数字订单号(测试/历史)。去重保序。
    """
    if not s:
        return []
    seen: set = set()
    out: list[str] = []
    for tok in re.split(r"[\s,;]+", str(s).strip()):
        tok = tok.strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _purchase_category(p: PartPurchase, mat_info: dict) -> str:
    """该采购的配件分类: 料号→分类优先; 无料号/无分类则按物料名关键词推断。"""
    if p.material_code:
        cat = (mat_info.get(p.material_code) or {}).get("category")
        if cat:
            return cat
    from app.services.material_category_service import _ac_category
    return _ac_category(p.material_name or "") or _UNCATEGORIZED


def _resolve_purchase_cat(p: PartPurchase, cons_o: dict, mat_info: dict) -> tuple[str, bool]:
    """把一笔采购在某订单上锚定到该单 BOM 的料/分类, 返回 (分类, 是否在该单BOM内)。

    防分类错配致双算(用户红线): 真实采购必须覆盖该单 BOM 里"对应那一类"的预估, 而非凭独立推断
    新增一类(否则 real(X)+est(Y) 同一笔配件钱算两次)。
      1) 料号命中该单 BOM 某料 → 用该料在 BOM 的分类(保证 real 覆盖对应 est);
      2) 否则关键词分类若也在该单 BOM 分类集合里 → 用之(可覆盖);
      3) 都不在 → (关键词分类, False): 该料不在该单 BOM, 标记待人工确认(不静默与其它类 est 叠加误读)。
    """
    if p.material_code:
        for cat, c in cons_o.items():
            for m in c.get("materials", []):
                if m.get("material_code") == p.material_code:
                    return cat, True
    pc = _purchase_category(p, mat_info)
    return (pc, True) if pc in cons_o else (pc, False)


def _monthly_supplier_names(db: Session) -> set:
    """payment_terms 含「月结」的供应商名(用于把零星采购里偶发的月结供应商排除出"零星覆盖")。"""
    from app.models.supplier import Supplier
    out: set = set()
    for name, terms in db.execute(select(Supplier.name, Supplier.payment_terms)).all():
        if name and terms and "月结" in terms:
            out.add(name)
    return out


def _category_usage(cons_cat: Optional[dict]) -> Decimal:
    """该订单某分类的"用量"权重(多单分摊用): 面积料(尺寸可解析)按面积, 计数料按数量, ×该行件数。

    实现用户口径「按各单该配件 BOM 用量(面积/数量)占比分摊」: 同一笔采购的同种料跨单, 面积大的单分得多。
    """
    if not cons_cat:
        return Decimal("0")
    total = Decimal("0")
    for m in cons_cat.get("materials", []):
        area = _size_area(m.get("size_note"))
        unit = area if area > 0 else Decimal("1")
        total += unit * _d(m.get("qty"))
    return total


def _allocate_purchases(db: Session, mat_info, bom_by_pcsku, bom_by_pc):
    """把所有有效配件采购单按平台订单号拆分(多单按该分类 BOM 用量占比分摊金额), 返回:
      order_real     {order_no: {category: Decimal}}   每单每类的真实采购成本(已分摊, 含月结+零星)
      order_objs     {order_no: Order}
      sporadic_cover {(order_no, category): [evidence...]}  非月结供应商的零星覆盖
                     (供月结导出/对账扣除 + 红字提示; evidence 含 流水号/金额/供应商)
      unmatched      [平台订单号都对不上真实订单的采购]
    """
    monthly = _monthly_supplier_names(db)
    rows = db.execute(
        select(PartPurchase).where(
            PartPurchase.related_order_no.isnot(None),
            PartPurchase.related_order_no != "",
        )
    ).scalars().all()

    order_objs: dict[str, Order] = {}

    def _order(no: str):
        if no not in order_objs:
            order_objs[no] = db.execute(
                select(Order).where(Order.order_no == no)).scalar_one_or_none()
        return order_objs[no]

    cons_cache: dict[str, dict] = {}

    def _cons(o: Order):
        if o.order_no not in cons_cache:
            cons_cache[o.order_no] = _order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc)
        return cons_cache[o.order_no]

    order_real: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    sporadic_cover: dict[tuple, list] = defaultdict(list)
    unmatched: list[dict] = []

    for p in rows:
        if _looks_non_part(p):
            continue
        amt = _purchase_amount(p)
        if amt is None or amt <= 0:
            continue
        amt = amt.quantize(_CENTS)
        valid = [(no, _order(no)) for no in _split_order_nos(p.related_order_no)]
        valid = [(no, o) for no, o in valid if o is not None]
        if not valid:
            unmatched.append({"purchase_no": p.purchase_no, "related_order_no": p.related_order_no,
                              "amount": float(amt), "material_name": p.material_name,
                              "supplier": p.supplier})
            continue
        is_monthly = bool(p.supplier and p.supplier in monthly)
        # 逐单把采购锚定到该单 BOM 的料/分类(防分类错配致双算), 并取该类用量作分摊权重
        per: list[tuple] = []   # (order, cat, in_bom, usage)
        for no, o in valid:
            cons_o = _cons(o)
            cat, in_bom = _resolve_purchase_cat(p, cons_o, mat_info)
            per.append((o, cat, in_bom, _category_usage(cons_o.get(cat))))
        # 分摊金额: 单单全额; 多单按用量占比(无用量→均分), 余额给用量最大的单, 保证 Σ=amt 守恒。
        n = len(per)
        if n == 1:
            shares = [amt]
        else:
            weights = [w for (_, _, _, w) in per]
            tot = sum(weights, Decimal("0"))
            if tot > 0:
                shares = [(amt * w / tot).quantize(_CENTS) for w in weights]
                lead = max(range(n), key=lambda i: weights[i])
            else:
                each = (amt / n).quantize(_CENTS)
                shares = [each] * n
                lead = 0
            shares[lead] += amt - sum(shares, Decimal("0"))   # 余额给主单, 守恒不漏分
        for (o, cat, in_bom, _w), sh in zip(per, shares):
            if sh <= 0:
                continue
            order_real[o.order_no][cat] += sh
            if not is_monthly:
                sporadic_cover[(o.order_no, cat)].append({
                    "purchase_no": p.purchase_no, "flow_no": p.alipay_flow_no,
                    "supplier": p.supplier, "amount": float(sh),
                    "material_name": p.material_name, "in_bom": in_bom,
                })
    return order_real, order_objs, sporadic_cover, unmatched


def _sporadic_covered(db: Session, mat_info, bom_by_pcsku, bom_by_pc) -> dict:
    """{(order_no, category): [evidence...]} —— 非月结(零星/现付)供应商已覆盖的(订单×分类)。"""
    return _allocate_purchases(db, mat_info, bom_by_pcsku, bom_by_pc)[2]


def _sporadic_note(cover: list) -> str:
    """红字提示文案: 含已付金额 + 支付宝流水号证据, 让月结供应商核对、勿重复计入。"""
    total = sum((_d(e.get("amount")) for e in cover), Decimal("0")).quantize(_CENTS)
    flows = [str(e.get("flow_no")) for e in cover if e.get("flow_no")]
    tail = (" 流水" + "/".join(flows[:3])) if flows else ""
    return f"查看是否为零星采购,非月结付款 — 已走支付宝现付 ¥{total}{tail},请勿计入月结"


# ── A. 逐单配件采购 → actual_parts 汇总 ──────────────────────────────────────
def aggregate_related_purchases(db: Session, *, apply: bool = False) -> dict:
    """填了 related_order_no 的配件采购单 → 按订单(多单按 BOM 占比分摊)写 Order.actual_parts。

    - 多单: 一笔零星采购对应多个平台订单号 → 按各单该分类 BOM 用量占比分摊金额(无 BOM 用量→均分)。
    - 逐类覆盖: actual_parts = Σ各分类[有真实零星采购→真实金额(覆盖该类预估), 否则→该类 BOM 预估],
      即只覆盖"对应的零星配件预估", 其它分类保留 BOM 预估(用户 2026-06-28 口径)。
    apply=False(默认): 只算预览, 不落库; 返回每单 physical_cost 变化 + 逐类明细供人工核对。
    apply=True: 写 actual_parts 并 commit(该单 physical_cost 转「逐项真实计价」)。
    """
    from app.services.order_financials import physical_cost, physical_cost_breakdown

    mat_info, bom_by_pcsku, bom_by_pc = _load_bom_and_materials(db)
    order_real, order_objs, sporadic_cover, unmatched = _allocate_purchases(
        db, mat_info, bom_by_pcsku, bom_by_pc)

    items: list[dict] = []
    skipped: list[dict] = []
    applied = 0
    for ono in sorted(order_real):
        o = order_objs.get(ono)
        real = order_real[ono]
        if o is None:
            items.append({"order_no": ono, "matched": False,
                          "parts_total": float(sum(real.values(), Decimal("0")))})
            continue
        # 安全门(用户 2026-06-29 抽查纠错): 只对【正常成交 + 非定制 + 非补单 + 成本未被封顶】的订单覆盖。
        # 取消/未付款=非成交; 定制单 BOM 是模板不可靠; 定金/片段/兜底单(实付远低于整件)用真实配件会虚高
        # (逐项真实计价分支去掉了实付×85%封顶)→ 一律跳过, 不动这些单的成本。
        bd = physical_cost_breakdown(o)   # 当前 actual_parts 为空 → 该单"自然"封顶状态
        reason = None
        if (o.status or "") in ("cancelled", "pending_payment"):
            reason = "非成交单(%s)" % o.status
        elif bool(getattr(o, "is_refill", False)):
            reason = "补单"
        elif bool(getattr(o, "is_custom", False)):
            reason = "定制单(BOM为模板)"
        elif bd.get("cap_mode") not in (None, "", "none"):
            reason = "成本被封顶(%s)" % bd.get("cap_mode")
        if reason:
            skipped.append({"order_no": ono, "reason": reason,
                            "real_parts": float(sum(real.values(), Decimal("0")))})
            continue
        cons = _order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc)
        # 基线 = 该单原本的配件预估 est_parts(缺则退回 BOM 估总额)。只把"被真实采购覆盖的分类"换成真实值,
        # 其余配件仍按原预估 —— 不用 BOM 全类汇总(否则会比定价预估大很多 → 整单成本虚高)。
        base = _d(o.est_parts) if o.est_parts is not None else sum(
            (_d(c.get("amount")) for c in cons.values()), Decimal("0"))
        real_total = sum(real.values(), Decimal("0"))
        est_covered = sum((_d(cons[c].get("amount")) for c in real if c in cons), Decimal("0"))
        new_parts = (base - est_covered + real_total).quantize(_CENTS)
        if new_parts < 0:
            new_parts = real_total.quantize(_CENTS)
        cat_rows: list[dict] = []
        not_in_bom_n = 0
        for c in sorted(real):
            in_bom = c in cons
            if not in_bom:
                not_in_bom_n += 1
            cat_rows.append({
                "category": c, "real": float(real[c]),
                "est": float(_d(cons.get(c, {}).get("amount"))),
                "not_in_bom": (not in_bom),
                "evidence": sporadic_cover.get((ono, c), []),
            })
        old_parts = o.actual_parts
        old_phys = physical_cost(o)
        o.actual_parts = new_parts
        new_phys = physical_cost(o)
        # 健全性门: 归账是"用真实配件替换配件预估", 整单物理成本最多只该上升≈新增的真实配件额。
        # 若成本上升远超真实配件额(多因 wood_cost_est 与 theoretical_cost 数据不一致 → actual_parts 分支
        # 换算木作基线致整单虚高, 如 theo=2108 却 wood_est=5200)→ 还原并跳过, 不动该单成本, 列出供人工核对。
        if (new_phys - old_phys) > real_total + Decimal("100"):
            o.actual_parts = old_parts
            skipped.append({
                "order_no": ono,
                "reason": "成本异常上升%.0f(>真实配件%.0f, 疑 wood_est/理论成本不一致)" % (
                    float(new_phys - old_phys), float(real_total)),
                "real_parts": float(real_total),
            })
            continue
        if apply:
            applied += 1
        else:
            o.actual_parts = old_parts   # dry-run 还原
        items.append({
            "order_no": ono, "matched": True, "categories_count": len(real),
            "product_name": o.product_name, "is_custom": bool(o.is_custom),
            "est_parts_base": float(base), "real_total": float(real_total),
            "old_actual_parts": float(_d(old_parts)) if old_parts is not None else None,
            "new_actual_parts": float(new_parts),
            "old_physical_cost": float(old_phys), "new_physical_cost": float(new_phys),
            "physical_delta": float((new_phys - old_phys).quantize(_CENTS)),
            "not_in_bom_categories": not_in_bom_n,
            "categories": cat_rows,
        })

    if apply:
        db.commit()

    matched = [i for i in items if i.get("matched")]
    return {
        "applied": apply,
        "applied_count": applied,
        "matched_orders": len(matched),
        # 跳过的订单(取消/定制/补单/成本被封顶) — 不覆盖成本, 列出原因供人工核对
        "skipped_orders": len(skipped),
        "skipped": skipped,
        # unmatched = 平台订单号全部对不上真实订单的"采购单"数(非订单数)
        "unmatched_purchases_count": len(unmatched),
        "unmatched_purchases": unmatched,
        # 真实采购分类不在该单 BOM 内、需人工核对的订单数(防分类错配高估)
        "flagged_not_in_bom_orders": sum(1 for i in matched if i.get("not_in_bom_categories")),
        "total_parts_amount": float(sum((_d(i.get("new_actual_parts")) for i in matched), Decimal("0"))),
        "items": items,
    }


# ── BOM 驱动的物料消耗 (取代硬编码关键词; 用户 2026-06-26 方向1) ──────────────
def _settled_shipped_orders(db: Session) -> list[Order]:
    """成交 + 已发货(ship_date 非空) + 非补单 的订单(对账消费窗口基底, 按发货日期)。"""
    return db.execute(
        select(Order).where(
            sales_analytics.settled_sale_clause(),
            Order.is_refill.is_(False),
            Order.ship_date.isnot(None),
        )
    ).scalars().all()


def _product_code_variants(code: Optional[str]) -> list[str]:
    """订单 product_code 用 P+11, BOM 用 PPS+11 → 两形态都试(镜像 accessory_checklist)。"""
    if not code:
        return []
    code = code.strip()
    out = {code}
    if code.startswith("PPS"):
        out.add("P" + code[3:])
    elif code.startswith("P"):
        out.add("PPS" + code[1:])
    return list(out)


def _load_bom_and_materials(db: Session):
    """一次性载入: 物料(category/price/name/unit) + BOM(按 (product_code,sku_code) 与 product_code 两键)。"""
    from app.models.bom import BomLine
    mat_info: dict[str, dict] = {}
    for code, name, unit, price, cat in db.execute(
        select(Material.code, Material.name, Material.unit, Material.price, Material.category)
    ).all():
        mat_info[code] = {"name": name, "unit": unit, "price": _d(price), "category": cat}
    bom_by_pcsku: dict[tuple, list] = defaultdict(list)
    bom_by_pc: dict[str, list] = defaultdict(list)
    for pc, sc, mcode, qty, remark, mname in db.execute(
        select(BomLine.product_code, BomLine.sku_code, BomLine.material_code,
               BomLine.qty_per_product, BomLine.remark, BomLine.material_name)
    ).all():
        rec = (mcode, _d(qty), remark, mname)
        bom_by_pcsku[(pc, sc)].append(rec)
        bom_by_pc[pc].append(rec)
    return mat_info, bom_by_pcsku, bom_by_pc


def _order_bom_lines(o: Order, bom_by_pcsku, bom_by_pc) -> list:
    """该订单的 BOM 行(镜像 _bom_rows_for_order: 先 product_code+sku_code 精确, 再 product_code 回退)。"""
    pcs = _product_code_variants(o.product_code)
    for pc in pcs:
        if (pc, o.sku_code) in bom_by_pcsku:
            return bom_by_pcsku[(pc, o.sku_code)]
    for pc in pcs:
        if pc in bom_by_pc:
            return bom_by_pc[pc]
    return []


def _order_is_custom(o: Order) -> bool:
    if bool(getattr(o, "is_custom", False)):
        return True
    from app.services import sku_utils
    try:
        return bool(sku_utils.is_custom_sku_code(o.sku_code, o.product_code))
    except Exception:  # noqa: BLE001
        return False


def _order_category_consumption(o: Order, mat_info, bom_by_pcsku, bom_by_pc) -> dict[str, dict]:
    """该订单按分类的外采配件消耗: {category: {amount, materials:[{...}]}}。排木作/工厂自备(WD/MW/MP);
    qty = qty_per_product × 订单件数。

    - 非定制: 同料同尺寸去重。
    - 定制单(用户 2026-06-27): BOM 是"模板"(同一种料堆了多个尺寸, 如洞洞板 1155/955/755), 实际一单只用
      一种 → **同料只取一行**(面积最大者, 成本偏保守); 模板有多个尺寸时标 `size_uncertain` 让清单提示
      人工确认尺寸。这同时修正了对账预估对定制单的"同料多算"。
    """
    qmul = Decimal(int(o.qty or 1))
    is_custom = _order_is_custom(o)
    by_cat: dict[str, dict] = {}
    seen: set = set()
    custom_pick: dict[str, dict] = {}   # 定制单: mcode -> 选中料(面积最大) + alt(被合并的其它尺寸数)

    def _emit(mcode, q, price, size, cat, name, info, alt=0):
        amt = (q * price).quantize(_CENTS)
        c = by_cat.setdefault(cat, {"amount": Decimal("0"), "materials": []})
        c["amount"] += amt
        m = {
            "material_code": mcode, "part_name": name, "category": cat,
            "qty": float(q), "unit": info.get("unit"), "price": float(price),
            "amount": float(amt), "size_note": size,
        }
        if alt > 0:
            m["size_uncertain"] = True
            m["alt_size_count"] = alt
        c["materials"].append(m)

    for mcode, qty, remark, mname in _order_bom_lines(o, bom_by_pcsku, bom_by_pc):
        if (mcode or "").split("-", 1)[0].upper() in _FACTORY_PREFIXES:
            continue
        info = mat_info.get(mcode) or {}
        cat = info.get("category") or _UNCATEGORIZED
        name = info.get("name") or mname or mcode
        size = (remark or "").strip() or None
        q = qty * qmul
        price = info.get("price", Decimal("0"))
        if is_custom:
            area = _size_area(size)
            prev = custom_pick.get(mcode)
            if prev is None:
                custom_pick[mcode] = {"q": q, "price": price, "size": size, "cat": cat,
                                      "name": name, "info": info, "area": area, "alt": 0}
            else:
                prev["alt"] += 1
                if area > prev["area"]:
                    custom_pick[mcode] = {"q": q, "price": price, "size": size, "cat": cat,
                                          "name": name, "info": info, "area": area, "alt": prev["alt"]}
            continue
        dk = (mcode, "".join(size.split()) if size else "")
        if dk in seen:
            continue
        seen.add(dk)
        _emit(mcode, q, price, size, cat, name, info)

    for mcode, p in custom_pick.items():
        _emit(mcode, p["q"], p["price"], p["size"], p["cat"], p["name"], p["info"], alt=p["alt"])
    return by_cat


def _ac_categories(db: Session) -> list[str]:
    """配件库里 AC(外采)物料用到的分类(去重, 排工厂自备前缀)。"""
    cats: set = set()
    for code, cat in db.execute(
        select(Material.code, Material.category).where(Material.category.isnot(None))
    ).all():
        if (code or "").split("-", 1)[0].upper() in _FACTORY_PREFIXES:
            continue
        if cat:
            cats.add(cat)
    return sorted(cats)


# ── B. 分类 × 发货月 对账 (BOM 驱动, 发货日期口径) ───────────────────────────
def bulk_material_recon(db: Session, *, granularity: str = "month") -> dict:
    """配件对账(方向1, 分类+BOM 驱动): 每分类每发货月 历史平均 | 预估 | 实际(工厂月度) | 差异%。

    - 预估 standard_consume = Σ(发货成交单 BOM 里该分类外采配件 price×qty)(按 ship_date 分月)。
    - 实际 factory_actual = 工厂月度对账总额(PartsMonthlyRecon, key=分类, 多供应商求和; 没录=None)。
    - 历史平均 historical_avg = 过去已对账月「每单实际」均值 × 本月发货单数(无历史→回退预估)。
    """
    mat_info, bom_by_pcsku, bom_by_pc = _load_bom_and_materials(db)
    orders = _settled_shipped_orders(db)
    sporadic_cover = _sporadic_covered(db, mat_info, bom_by_pcsku, bom_by_pc)

    std: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    cnt: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    spor: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for o in orders:
        ym = _ym(o.ship_date)
        if ym is None:
            continue
        for cat, c in _order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc).items():
            cover = sporadic_cover.get((o.order_no, cat))
            # 月结类里, 该单该类已走零星采购(支付宝现付)的部分 → 从月结预估「按金额净额」扣除(防月结多付),
            # 已现付额单列 sporadic_excluded; 剩余额(该类还有别的料没现付)仍按月结计入。零星类不受影响。
            if cover and _settle_mode(cat) == "月结":
                cover_total = sum((_d(e.get("amount")) for e in cover), Decimal("0"))
                spor[cat][ym] += cover_total
                remain = _d(c["amount"]) - cover_total
                if remain > 0:                       # 仅扣已现付, 剩余仍进月结预估(不丢整类)
                    std[cat][ym] += remain
                    cnt[cat][ym] += 1
                continue
            std[cat][ym] += c["amount"]
            cnt[cat][ym] += 1

    fact_all: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for key, ym, amt in db.execute(
        select(PartsMonthlyRecon.material_key, PartsMonthlyRecon.year_month, PartsMonthlyRecon.actual_total)
    ).all():
        fact_all[key][ym] += _d(amt)

    # 零星类"实际" = 真实采购单(支付宝备注/导入)按分类×采购月汇总 (用户 2026-06-27)。
    # 经 PartPurchase.material_code → 配件库分类; 未编码的采购映射不到分类(诚实留空, 进未分类不强塞)。
    purch_all: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for mcode, amt, pdate in db.execute(
        select(PartPurchase.material_code, PartPurchase.amount, PartPurchase.purchase_date)
        .where(PartPurchase.amount.isnot(None))
    ).all():
        cat = (mat_info.get(mcode) or {}).get("category") if mcode else None
        ym = _ym(pdate)
        # 自购木材(MW)等零星采购计入对账; 仅排纯工厂服务(木作 WD/人工 MP)(用户 2026-06-29)。
        if cat and ym and (mcode or "").split("-", 1)[0].upper() not in _PURCHASE_EXCLUDE_PREFIXES:
            purch_all[cat][ym] += _d(abs(amt))

    out_cats = []
    all_keys = sorted(set(_ac_categories(db)) | set(std.keys()) | set(fact_all.keys()) | set(purch_all.keys()))
    for cat in all_keys:
        mode = _settle_mode(cat)   # 月结(五金/电力轨道) | 零星(其余)
        std_p, cnt_p = std.get(cat, {}), cnt.get(cat, {})
        fact_p, purch_p = fact_all.get(cat, {}), purch_all.get(cat, {})
        spor_p = spor.get(cat, {})
        periods = sorted(set(std_p) | set(cnt_p) | set(fact_p) | set(purch_p) | set(spor_p))
        rows = []
        t_std = t_fact = t_purch = t_actual = t_spor = Decimal("0")
        hist_rates: list[Decimal] = []
        for ym in periods:
            s = std_p.get(ym, Decimal("0")).quantize(_CENTS)
            c = cnt_p.get(ym, 0)
            has_fact = ym in fact_p
            fact = fact_p.get(ym, Decimal("0")).quantize(_CENTS) if has_fact else None
            has_purch = ym in purch_p
            purch = purch_p.get(ym, Decimal("0")).quantize(_CENTS) if has_purch else None
            # 月结类"实际"=工厂月度对账总额; 零星类"实际"=真实采购单(支付宝备注/导入)
            actual = fact if mode == "月结" else purch
            has_actual = has_fact if mode == "月结" else has_purch
            if hist_rates and c > 0:
                hist = ((sum(hist_rates, Decimal("0")) / Decimal(len(hist_rates))) * c).quantize(_CENTS)
            else:
                hist = s
            var = (actual - s).quantize(_CENTS) if (has_actual and actual is not None) else None
            var_pct = float((var / s * 100).quantize(_CENTS)) if (var is not None and s > 0) else None
            sp = spor_p.get(ym, Decimal("0")).quantize(_CENTS)
            rows.append({
                "period": ym, "historical_avg": float(hist), "standard_consume": float(s),
                "factory_actual": float(fact) if has_fact else None, "has_factory_actual": has_fact,
                "actual_purchase": float(purch) if has_purch else None, "has_actual_purchase": has_purch,
                "settle_mode": mode,
                "actual": float(actual) if (has_actual and actual is not None) else None, "has_actual": has_actual,
                "variance": float(var) if var is not None else None, "variance_pct": var_pct,
                "order_count": c,
                # 月结类里已走零星采购、已从月结预估扣除的金额(防双算多付; 见 export 红字提示)
                "sporadic_excluded": float(sp),
            })
            t_std += s
            t_spor += sp
            if has_fact:
                t_fact += fact
            if has_purch:
                t_purch += purch
            if has_actual and actual is not None:
                t_actual += actual
                if c > 0:
                    hist_rates.append(actual / Decimal(c))
        t_var = (t_actual - t_std).quantize(_CENTS)
        out_cats.append({
            "key": cat, "name": cat, "settle_mode": mode, "periods": rows,
            "total_standard": float(t_std),
            "total_factory_actual": float(t_fact), "total_actual_purchase": float(t_purch),
            "total_actual": float(t_actual), "total_variance": float(t_var),
            "total_sporadic_excluded": float(t_spor),
            "total_variance_pct": float((t_var / t_std * 100).quantize(_CENTS)) if t_std > 0 else None,
        })
    return {"granularity": granularity, "ship_date_basis": True, "category_driven": True, "materials": out_cats}


# ── C. 工厂月度对账总额 录入/查询/删除 (material_key 存「分类」) ──────────────
def list_monthly_recon(db: Session, *, material_key: Optional[str] = None,
                       year_month: Optional[str] = None) -> list[dict]:
    q = select(PartsMonthlyRecon)
    if material_key:
        q = q.where(PartsMonthlyRecon.material_key == material_key)
    if year_month:
        q = q.where(PartsMonthlyRecon.year_month == year_month)
    rows = db.execute(
        q.order_by(PartsMonthlyRecon.year_month.desc(), PartsMonthlyRecon.id.desc())
    ).scalars().all()
    return [{
        "id": r.id, "material_key": r.material_key, "material_name": r.material_key,
        "year_month": r.year_month, "supplier": r.supplier,
        "actual_total": float(_d(r.actual_total)), "note": r.note,
    } for r in rows]


def save_monthly_recon(db: Session, *, material_key: str, year_month: str, actual_total,
                       supplier: Optional[str] = None, note: Optional[str] = None,
                       recon_id: Optional[int] = None) -> dict:
    """录入/更新 某分类某月工厂返回的对账总额。recon_id 给了=更新, 否则新增(同分类同月可多供应商)。"""
    material_key = (material_key or "").strip()
    if not material_key:
        raise ValueError("分类不能为空")
    amt = _d(actual_total)
    if recon_id is not None:
        row = db.get(PartsMonthlyRecon, recon_id)
        if row is None:
            raise ValueError(f"对账记录 {recon_id} 不存在")
        row.material_key, row.year_month = material_key, year_month
        row.supplier, row.actual_total, row.note = (supplier or None), amt, (note or None)
    else:
        row = PartsMonthlyRecon(material_key=material_key, year_month=year_month,
                                supplier=supplier or None, actual_total=amt, note=note or None)
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "material_key": row.material_key, "material_name": row.material_key,
            "year_month": row.year_month, "supplier": row.supplier,
            "actual_total": float(_d(row.actual_total)), "note": row.note}


def delete_monthly_recon(db: Session, recon_id: int) -> bool:
    row = db.get(PartsMonthlyRecon, recon_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


# ── D. 当月「已发货」订单清单导出 (发给工厂对账) ──────────────────────────────
def _orders_shipped_in(db: Session, year_month: str) -> list[Order]:
    return [o for o in _settled_shipped_orders(db) if _ym(o.ship_date) == year_month]


def export_shipped_orders(db: Session, *, year_month: str,
                          material_key: Optional[str] = None) -> dict:
    """导当月(发货月)已发货成交订单清单。

    material_key(=分类)空 = 全部发货单(基础列, 工厂自挑); 给了 = 只列 BOM 里有该分类外采配件的单,
    逐单展开该分类的配件部位 + 预设尺寸(去重、排木作)。按发货日期口径(ship_date)。
    """
    orders = _orders_shipped_in(db, year_month)
    out_orders: list[dict] = []
    t_est = Decimal("0")

    if material_key:
        from app.services import sku_utils
        mat_info, bom_by_pcsku, bom_by_pc = _load_bom_and_materials(db)
        sporadic_cover = _sporadic_covered(db, mat_info, bom_by_pcsku, bom_by_pc)
        for o in orders:
            cons = _order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc).get(material_key)
            if not cons:
                continue
            parts = sorted(cons["materials"], key=lambda p: p["part_name"])
            est = cons["amount"]
            t_est += est
            # 该单该类已走零星采购(支付宝现付) → 红字提示工厂勿计入月结(防多付); 带流水号证据。
            cover = sporadic_cover.get((o.order_no, material_key))
            out_orders.append({
                "order_no": o.order_no,
                "order_date": o.order_date.isoformat() if o.order_date else None,
                "ship_date": o.ship_date.isoformat() if o.ship_date else None,
                "customer_name": o.customer_name,
                "product_name": o.product_name,
                "sku": o.sku,
                "est_parts": float(est),
                "sporadic": bool(cover),
                "sporadic_note": _sporadic_note(cover) if cover else None,
                "is_custom": bool(getattr(o, "is_custom", False))
                or sku_utils.is_custom_sku_code(o.sku_code, o.product_code),
                "bom_parts": [{
                    "part_name": p["part_name"], "material_code": p["material_code"],
                    "category": p["category"], "qty": p["qty"], "unit": p["unit"],
                    "size_note": p["size_note"],
                    # 单价(Material.price) + 总价(=qty×price), _emit 已算好直接透传
                    "unit_price": p.get("price"), "total_price": p.get("amount"),
                    "size_uncertain": p.get("size_uncertain", False),
                    "alt_size_count": p.get("alt_size_count", 0),
                } for p in parts],
            })
    else:
        for o in orders:
            est = _d(o.est_parts)
            t_est += est
            out_orders.append({
                "order_no": o.order_no,
                "order_date": o.order_date.isoformat() if o.order_date else None,
                "ship_date": o.ship_date.isoformat() if o.ship_date else None,
                "customer_name": o.customer_name,
                "product_name": o.product_name,
                "sku": o.sku,
                "est_parts": float(est),
            })

    out_orders.sort(key=lambda x: (x["ship_date"] or "", x["order_no"]))
    return {
        "year_month": year_month,
        "material_key": material_key,
        "material_name": material_key,
        "order_count": len(out_orders),
        "total_est_parts": float(t_est.quantize(_CENTS)),
        "orders": out_orders,
    }


def build_shipped_orders_xlsx(db: Session, *, year_month: str,
                              material_key: Optional[str] = None):
    """导清单 → xlsx(扁平表格)。订单号首列且每行重复; 按分类时逐件展开 BOM 并带
    材料单价 + 总价(=qty×price)+预估合计行; 下单日期(order_date)与发货日(ship_date)并列。
    返回 (openpyxl.Workbook, data_dict)。零星/定制/尺寸提示折进产品/尺寸单元格。
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    d = export_shipped_orders(db, year_month=year_month, material_key=material_key)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = (material_key or "全部发货单")[:28]

    if material_key:
        headers = ["订单号", "下单日期", "发货日", "产品", "SKU(含尺寸)", "类别",
                   "部位/料", "数量", "预设尺寸", "材料单价", "总价"]
        widths = [22, 12, 12, 14, 18, 10, 16, 8, 22, 11, 11]
        money_cols = [10, 11]
        ws.append(headers)
        for o in d["orders"]:
            prod = o.get("product_name") or ""
            if o.get("is_custom"):
                prod = (prod + " ⚠定制·模板").strip()
            if o.get("sporadic"):
                prod = (prod + " ⚠已零星现付·勿计月结").strip()
            for p in (o.get("bom_parts") or [{}]):
                size = p.get("size_note") or "—"
                if p.get("size_uncertain"):
                    size = f"{size} ⚠模板尺寸取最大,请确认"
                ws.append([
                    o["order_no"], o.get("order_date") or "", o.get("ship_date") or "",
                    prod, o.get("sku") or "", p.get("category") or "",
                    p.get("part_name") or "", p.get("qty"), size,
                    p.get("unit_price"), p.get("total_price"),
                ])
        ws.append(["合计(预估,请工厂核对)", "", "", "", "", "", "", "", "", "",
                   d["total_est_parts"]])
    else:
        headers = ["订单号", "下单日期", "发货日", "客户", "产品", "SKU(含尺寸)", "预估配件"]
        widths = [22, 12, 12, 12, 16, 20, 11]
        money_cols = [7]
        ws.append(headers)
        for o in d["orders"]:
            ws.append([o["order_no"], o.get("order_date") or "", o.get("ship_date") or "",
                       o.get("customer_name") or "", o.get("product_name") or "",
                       o.get("sku") or "", o.get("est_parts")])
        ws.append(["合计(预估)", "", "", "", "", "", d["total_est_parts"]])

    head_fill = PatternFill("solid", fgColor="E6F1FB")
    for c in ws[1]:
        c.font = Font(bold=True)
        c.fill = head_fill
        c.alignment = Alignment(vertical="center", wrap_text=True)
    for i, wd in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = wd
    # 订单号列(第1列)强制文本(@) — 19位订单号防 Excel 转科学计数法丢精度
    for (cell,) in ws.iter_rows(min_row=2, min_col=1, max_col=1):
        cell.number_format = "@"
    for mc in money_cols:
        for (cell,) in ws.iter_rows(min_row=2, min_col=mc, max_col=mc):
            if isinstance(cell.value, (int, float)):
                cell.number_format = "0.00"
            cell.alignment = Alignment(horizontal="right")
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{ws.max_row}"
    return wb, d


# ── E. 双算自检: 月结分类里被零星采购覆盖的(订单×分类) ──────────────────────────
def detect_sporadic_monthly_overlap(db: Session) -> list[dict]:
    """列出「月结分类却已走零星采购」的(订单×分类) —— 这些已从月结预估扣除/在导出标红,
    供人工核对、防工厂月结重复计费多付。空列表=无重叠(干净)。
    """
    mat_info, bom_by_pcsku, bom_by_pc = _load_bom_and_materials(db)
    sporadic_cover = _sporadic_covered(db, mat_info, bom_by_pcsku, bom_by_pc)
    out: list[dict] = []
    for (ono, cat), cover in sporadic_cover.items():
        if _settle_mode(cat) != "月结":
            continue
        o = db.execute(select(Order).where(Order.order_no == ono)).scalar_one_or_none()
        total = sum((_d(e.get("amount")) for e in cover), Decimal("0")).quantize(_CENTS)
        out.append({
            "order_no": ono, "category": cat,
            "ship_month": _ym(o.ship_date) if o else None,
            "product_name": o.product_name if o else None,
            "sporadic_amount": float(total),
            "evidence": cover,
        })
    out.sort(key=lambda x: (x.get("ship_month") or "", x["category"], x["order_no"]))
    return out
