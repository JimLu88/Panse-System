"""定制报价 v2 — 理顺逻辑 + 提速 (Module ④ v2).

一个分类器前门 + 两条确定性管道 (见 docs/定制报价v2_理顺提速_落地方案.md):

  classify / classify_ai  → {普通定制|特殊定制, 命中的基础产品, (AI)解析出的尺寸/材质/增减}
  普通定制 quote_light  = 真实SKU锚点价 + 尺寸delta(策略C 多档插值) + 材质delta(wood_cost反推) + 增减部位delta
  特殊定制 quote_heavy  = 品类部位模板(BOM聚合) → quote_from_spec 引擎 + 自动推五金

P0 实测(2026-06-16 生产库): Material.area 全空 → 不用板件几何; 同款多档定价 88% → 策略C插值;
PricingSku.wood_cost 95%非空 + MW木材单价表干净 → 材质delta 用 wood_cost÷原料单价 反推面积。
全部自有数据(定价表/配件表/BOM), 不使用任何外部 Excel 数据。

提速: 标准产品库名单 + 品类部位模板 走进程内缓存(TTL 5 分钟, 只存纯数据)。
财务纪律: 本服务纯计算 + 只读查库, 不写订单/财务字段; 由新端点影子并行调用, 旧端点保留。
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.models.material import Material
from app.models.pricing import PricingSku
from app.models.product import Product
from app.services.product_match_service import match

# 木种关键词 (材质识别 / 反推材质delta用); 与 customization_ai_service._WOOD_KEYWORDS 同源
_WOOD_KEYWORDS = ["黑胡桃", "樱桃木", "白蜡木", "红橡木", "白橡木", "榉木", "胡桃木", "橡木", "松木"]

_PRICE_TIERS = {
    "list": "list_price", "daily": "daily_price",
    "small": "small_promo", "mid": "mid_promo", "big": "big_promo",
}


# ───────────────────────── 进程内缓存 (提速) ───────────────────────── #
# 只存纯数据(非 ORM 对象), 跨会话安全; TTL 5 分钟, 写表后自然过期。

_CACHE: dict = {}
_CACHE_TTL = 300.0


def _cached(key, builder, ttl=_CACHE_TTL):
    now = time.monotonic()
    hit = _CACHE.get(key)
    if hit and (now - hit[1]) < ttl:
        return hit[0]
    val = builder()
    _CACHE[key] = (val, now)
    return val


def cache_clear() -> None:
    """物料/产品/模板变更后清缓存(导入/飞书同步后调用)。"""
    _CACHE.clear()


def _parse_json(text) -> dict:
    try:
        m = re.search(r"\{.*\}", text or "", re.DOTALL)
        return json.loads(m.group()) if m else {}
    except Exception:  # noqa: BLE001
        return {}


# ───────────────────────── 工具: 尺寸解析 + 插值 ───────────────────────── #

def parse_length_m(text: Optional[str]) -> Optional[float]:
    """从 SKU 名/文本里抽长度(米)。匹配「1.4米 / 1.4m / 1.4 米」。无→None。"""
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:米|m\b|M\b)", text)
    return float(m.group(1)) if m else None


def interp(points: list[tuple[float, float]], x: float) -> tuple[Optional[float], str]:
    """对 (x, y) 点集在 x 处做分段线性插值/外推。

    返回 (y, method)。method ∈ exact/interp/extrap/single/no-data。
    用于策略C: 同款多档真实价 → 任意尺寸的价。
    """
    pts = sorted((px, py) for px, py in points if px is not None and py is not None)
    if not pts:
        return None, "no-data"
    if len(pts) == 1:
        return pts[0][1], "single"
    for px, py in pts:
        if abs(px - x) < 1e-9:
            return py, "exact"
    if pts[0][0] <= x <= pts[-1][0]:
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if x0 <= x <= x1:
                y = y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 != x0 else y0
                return y, "interp"
    # 外推: 用最近一段斜率
    if x < pts[0][0]:
        x0, y0 = pts[0]
        x1, y1 = pts[1]
    else:
        x0, y0 = pts[-2]
        x1, y1 = pts[-1]
    slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
    return y0 + slope * (x - x0), "extrap"


def detect_wood(text: Optional[str]) -> Optional[str]:
    """从文本识别木种(取最先命中)。"""
    t = text or ""
    for w in _WOOD_KEYWORDS:
        if w in t:
            return w
    return None


def _wood_unit_price(db: Session, wood: str) -> Optional[float]:
    """查木材单价 (MW 前缀, 优先 2.2cm)。wood 如「黑胡桃」→ 命中「黑胡桃木-2.2cm厚度」。"""
    if not wood:
        return None
    rows = db.query(Material).filter(
        Material.code.like("MW%"), Material.name.like(f"%{wood}%"),
        Material.price.isnot(None),
    ).all()
    if not rows:
        rows = db.query(Material).filter(
            Material.name.like(f"%{wood}%"), Material.price.isnot(None),
            ~Material.name.like("%样块%"), ~Material.name.like("%样品%"),
        ).all()
    if not rows:
        return None
    rows.sort(key=lambda m: (0 if "2.2" in (m.name or "") else 1, len(m.name or "")))
    return float(rows[0].price)


# ───────────────────────── 普通定制: 锚点价 + 三类 delta ───────────────────────── #

def _sku_points(skus: list[PricingSku], tier_col: str) -> tuple[list, list]:
    """从同款多档 SKU 构造 (长度, 价) 与 (长度, wood_cost) 点集。"""
    price_pts, wood_pts = [], []
    for s in skus:
        ln = parse_length_m(s.sku) or parse_length_m(s.sku_code)
        if ln is None:
            continue
        price = getattr(s, tier_col, None)
        if price is not None:
            price_pts.append((ln, float(price)))
        if s.wood_cost is not None:
            wood_pts.append((ln, float(s.wood_cost)))
    return price_pts, wood_pts


def product_margin(skus: list[PricingSku]) -> Optional[float]:
    """本款「大促毛利率」(实时算, 不写死): 平均 (1 − 会计成本 / 大促价)。

    与 pricing 现有大促公式一致(比例 = 会计成本 accounting_cost ÷ 大促到手价 big_promo)。
    缺 big_promo/accounting_cost 的 SKU 跳过; 全缺 → 回落存档 gross_margin_rate 列均值; 再缺 → None。
    价格一变下次算价自动跟着变; 不同产品/类目各算各的, 绝不写死。
    """
    ms = []
    for s in skus:
        bp, ac = s.big_promo, s.accounting_cost
        if bp is not None and ac is not None and float(bp) > 0:
            ms.append(1.0 - float(ac) / float(bp))
    if ms:
        return round(sum(ms) / len(ms), 4)
    gm = [float(s.gross_margin_rate) for s in skus if s.gross_margin_rate is not None]
    return round(sum(gm) / len(gm), 4) if gm else None


def quote_light(
    db: Session,
    *,
    base_product_code: str,
    target_length_m: Optional[float] = None,
    target_material: Optional[str] = None,
    add_parts: Optional[list[dict]] = None,
    remove_parts: Optional[list[dict]] = None,
    modify_parts: Optional[list[dict]] = None,
    price_tier: str = "daily",
    factory_profit_rate: float = 0.25,
) -> dict:
    """普通定制报价 (改现有产品): 真实SKU锚点价 + 尺寸delta(策略C) + 材质delta(wood_cost反推) + 增减部位delta。

    全程纯算术, 0 次 AI。返回 {final_price, anchor, breakdown[], ...}; 查不到产品→error。
    """
    tier_col = _PRICE_TIERS.get(price_tier, "daily_price")
    skus = db.query(PricingSku).filter(PricingSku.product_code == base_product_code).all()
    if not skus:
        return {"error": f"定价表无此产品 {base_product_code}", "final_price": None, "breakdown": []}

    prod = db.query(Product).filter(Product.code == base_product_code).first()
    price_pts, wood_pts = _sku_points(skus, tier_col)
    breakdown: list[dict] = []

    # ── 锚点价 (含尺寸) ── 策略C: 多档真实价插值到目标长度 ──
    if target_length_m and price_pts:
        anchor, method = interp(price_pts, target_length_m)
        anchor_wood, _ = interp(wood_pts, target_length_m) if wood_pts else (None, "no-data")
        note = f"策略C 多档插值@{target_length_m}m ({method}, {len(price_pts)}档)"
    else:
        rep = sorted(skus, key=lambda s: parse_length_m(s.sku) or 0)[len(skus) // 2]
        anchor = float(getattr(rep, tier_col, None) or rep.daily_price or rep.list_price or 0)
        anchor_wood = float(rep.wood_cost) if rep.wood_cost is not None else None
        note = f"代表档 {rep.sku or rep.sku_code}"
    if not anchor:
        return {"error": "锚点价缺失(该产品4档价为空)", "final_price": None, "breakdown": []}
    breakdown.append({"label": "标准原价(同尺寸)", "amount": round(anchor, 2), "note": note})

    final = anchor

    # ── 材质 delta ── 优先同材种现成款; 否则 wood_cost 反推面积 ──
    material_delta = 0.0
    if target_material:
        base_wood = detect_wood(prod.name if prod else "") or detect_wood(
            (prod.main_material if prod else "") or "")
        tgt = detect_wood(target_material) or target_material
        if base_wood and tgt and tgt not in (base_wood, ""):
            sib = _find_sibling_by_material(db, prod, tgt) if prod else None
            if sib:
                sib_skus = db.query(PricingSku).filter(
                    PricingSku.product_code == sib.code).all()
                sp, _sw = _sku_points(sib_skus, tier_col)
                if target_length_m and sp:
                    sib_price, _m = interp(sp, target_length_m)
                else:
                    sib_price = float(getattr(sib_skus[0], tier_col, None) or 0) if sib_skus else 0
                if sib_price:
                    material_delta = round(sib_price - anchor, 2)
                    breakdown.append({"label": f"换料→{tgt}(现成款)", "amount": material_delta,
                                      "note": f"切到 {sib.code} {sib.name} 真实价 {round(sib_price,2)}"})
            if material_delta == 0.0:
                orig_u = _wood_unit_price(db, base_wood)
                new_u = _wood_unit_price(db, tgt)
                if orig_u and new_u and anchor_wood:
                    area = anchor_wood / orig_u
                    material_delta = round((new_u - orig_u) * area * (1 + factory_profit_rate), 2)
                    breakdown.append({"label": f"换料→{tgt}(反推)", "amount": material_delta,
                                      "note": f"面积≈{area:.2f}㎡(wood_cost{anchor_wood:.0f}÷{orig_u:.0f}) ×({new_u:.0f}−{orig_u:.0f})×{1+factory_profit_rate}"})
                else:
                    breakdown.append({"label": f"换料→{tgt}", "amount": 0.0,
                                      "note": "缺原料价/木作成本, 需人工核"})
    final += material_delta

    # ── 增减部位 delta (逐部位 cascade: 木作用模板几何×木单价 / 配件×计价单位 → ×人工×厂利÷畔色) ──
    from app.services import custom_quote_config_service as ccfg
    cfg = ccfg.get_config(db)
    category = (prod.category if prod else None) or base_product_code
    addrm_delta, addrm_lines, parts_detail = style_delta(
        db, category=category, length_m=target_length_m,
        add_parts=add_parts, remove_parts=remove_parts, modify_parts=modify_parts, cfg=cfg,
    )
    breakdown.extend(addrm_lines)
    final += addrm_delta

    # ── 工厂价预测 + 盈亏平衡 (定价表 factory_cost/accounting_cost 插值; accounting 已含真实扣点/税) ──
    fac_pts, acc_pts = [], []
    for s in skus:
        ln = parse_length_m(s.sku) or parse_length_m(s.sku_code)
        if ln is None:
            continue
        if getattr(s, "factory_cost", None) is not None:
            fac_pts.append((ln, float(s.factory_cost)))
        if getattr(s, "accounting_cost", None) is not None:
            acc_pts.append((ln, float(s.accounting_cost)))
    factory_predicted = break_even_factory = break_even_buffer = None
    if fac_pts:
        fp = interp(fac_pts, target_length_m)[0] if target_length_m else sum(v for _, v in fac_pts) / len(fac_pts)
        if fp is not None:
            factory_predicted = round(fp, 2)
            if acc_pts:
                ac = interp(acc_pts, target_length_m)[0] if target_length_m else sum(v for _, v in acc_pts) / len(acc_pts)
                if ac is not None:
                    break_even_factory = round(final - (ac - fp), 2)   # 净不亏: 售价 − 非工厂成本(accounting−factory)
                    break_even_buffer = round(break_even_factory - factory_predicted, 2)
    # 保本价(最低可卖): 保持本款大促毛利率 → 售价 × (1 − 该款毛利率); 毛利率实时算, 不写死
    margin = product_margin(skus)
    break_even_sell = round(final * (1 - margin), 2) if margin is not None else None

    # ── 竞品/基准对比 (每次算价都带; 竞品表空则只给本店标准款基准, 永远有对比) ──
    comparison = compare_prices(
        db, category=category,
        wood=(target_material or detect_wood(prod.name if prod else "")),
        size_m=target_length_m, our_price=round(final, 2),
        baseline_price=round(anchor + material_delta, 2),
        baseline_label="本店标准款(同尺寸同材质)",
    )

    return {
        "base_product_code": base_product_code,
        "base_product_name": prod.name if prod else None,
        "category": category,
        "anchor": round(anchor, 2),
        "anchor_method": note,
        "material_delta": material_delta,
        "addremove_delta": round(addrm_delta, 2),
        "final_price": round(final, 2),
        "factory_predicted": factory_predicted,     # 预测工厂价(定价表 factory_cost 插值, 缺则 None)
        "break_even_factory": break_even_factory,    # 盈亏平衡工厂价(净不亏红线: 售价−非工厂成本)
        "break_even_buffer": break_even_buffer,      # 安全垫 = 平衡价 − 预测价 (≈本单利润; 仅内部参考)
        "product_margin": margin,                    # 本款大促毛利率(实时: 平均 1−会计成本/大促价; 缺则 None)
        "break_even_sell": break_even_sell,          # 保本价(最低可卖) = 售价 × (1 − 本款大促毛利率)
        "price_tier": price_tier,
        "breakdown": breakdown,
        "parts_detail": parts_detail,
        "comparison": comparison,
        "ai_used": False,
    }


def _find_sibling_by_material(db: Session, base: Product, wood: str) -> Optional[Product]:
    """同类目里找名称含目标木种的现成产品(用其真实价比反推更准)。"""
    if not base or not base.category:
        return None
    return db.query(Product).filter(
        Product.category == base.category, Product.name.like(f"%{wood}%"),
        Product.code != base.code,
    ).first()


# ─────────── 普通定制·增减部位: 逐部位 cascade (借参考 style-customization 逻辑, 数据自有) ─────────── #

def _material_price_unit(db: Session, name: str) -> tuple[Optional[float], str]:
    """物料表查 (单价, 计价单位)。精确名→模糊(排样块/样品)。查不到→(None,'')。"""
    if not name:
        return None, ""
    from sqlalchemy import func, select
    row = db.execute(select(Material.price, Material.unit).where(Material.name == name)).first()
    if row is None:
        row = db.execute(
            select(Material.price, Material.unit).where(
                Material.name.like(f"%{name}%"), Material.price.isnot(None),
                ~Material.name.like("%样块%"), ~Material.name.like("%样品%"),
                ~Material.name.like("%小样%"),
            ).order_by(func.length(Material.name)).limit(1)
        ).first()
    if row is None:
        return None, ""
    return (float(row[0]) if row[0] is not None else None), (row[1] or "")


def _template_part_dims(category: str, length_m: Optional[float],
                        depth_cm=None, height_cm=None) -> dict:
    """品类+外形 → {部位名: (length_cm, width_cm, qty, material)} (模板几何, 给增减部位估面积)。"""
    try:
        from app.services import custom_board_template as tmpl
        boards = tmpl.generate_boards(
            category, (length_m or 1.0) * 100, depth_cm=depth_cm, height_cm=height_cm)
    except Exception:  # noqa: BLE001
        return {}
    return {b["part"]: (b.get("length_cm", 0), b.get("width_cm", 0),
                        b.get("qty", 1), b.get("material", "")) for b in boards}


def _match_part_dims(name: str, dims_map: dict):
    """部位名 → 模板几何 (含包含式模糊: 「中间背板」→「背板」)。无 → None。"""
    if not name:
        return None
    if name in dims_map:
        return dims_map[name]
    for k, v in dims_map.items():
        if k and (k in name or name in k):
            return v
    return None


def _cost_by_unit(price: float, unit: str, qty: float,
                  length_cm: float, width_cm: float) -> tuple[float, str, float]:
    """按计价单位算单部位材料成本 → (成本, 公式串, 面积㎡)。
    平方米: 长×宽×数量×价; 米: 长×数量×价; 其他(个/付/组/张/件): 数量×价。
    """
    u = unit or ""
    if "平" in u or "㎡" in u or "m2" in u.lower():
        if length_cm > 0 and width_cm > 0:
            area = (length_cm / 100) * (width_cm / 100)
            return area * qty * price, f"{area:.3f}㎡×{qty:g}×{price:g}", area
        return 0.0, "按㎡但缺尺寸→0(需手填)", 0.0
    if u in ("米", "1米", "m"):
        if length_cm > 0:
            meters = length_cm / 100
            return meters * qty * price, f"{meters:.2f}m×{qty:g}×{price:g}", 0.0
        return qty * price, f"{qty:g}×{price:g}(无长按件)", 0.0
    return qty * price, f"{qty:g}×{price:g}", 0.0


def _resolve_part(db: Session, part: dict, dims_map: dict) -> dict:
    """解析一个增/删部位 → 完整明细。尺寸优先显式(可手调), 否则木作用模板几何;
    材料优先显式, 否则模板该部位材料, 再否则部位名本身(配件如「电力轨道」直查物料表)。
    """
    name = (part.get("material") or part.get("name") or "").strip()
    qty = float(part.get("qty", 1) or 1)
    tdims = _match_part_dims(name, dims_map)
    material = (part.get("material_real") or "").strip() or (tdims[3] if tdims else "") or name
    length_cm = float(part.get("length_cm") or 0) or (float(tdims[0]) if tdims else 0.0)
    width_cm = float(part.get("width_cm") or 0) or (float(tdims[1]) if tdims else 0.0)
    price, unit = _material_price_unit(db, material)
    if price is None and material != name:
        price, unit = _material_price_unit(db, name)   # 配件名直查
    price = price or 0.0
    cost, formula, area = _cost_by_unit(price, unit, qty, length_cm, width_cm)
    return {
        "name": name, "material": material, "unit": unit or "件",
        "qty": qty, "length_cm": round(length_cm, 1), "width_cm": round(width_cm, 1),
        "area_m2": round(area, 3), "unit_price": price,
        "material_cost": round(cost, 2), "formula": formula,
        "panse_purchased": is_panse_purchased(material or name),
        "priced": price > 0,
    }


def style_delta(
    db: Session, *, category: str, length_m: Optional[float],
    add_parts: Optional[list], remove_parts: Optional[list], cfg: dict,
    modify_parts: Optional[list] = None, depth_cm=None, height_cm=None,
) -> tuple[float, list, list]:
    """增减部位逐部位 cascade → (净delta, breakdown行, parts_detail可编辑明细)。

    每部位: 材料成本 → ×(1+人工占比) → ×(1+工厂利润) ÷ (1−畔色毛利) = 零售增量。
    追加: +零售; 删除: −材料×remove_credit (决策①: 删件只省材料, 不退人工/利润; 默认0.85, 铁律「只高不低」)。
    改料: 材料差(新−旧); 净增(新料更贵)再×(1+工厂利润)(工厂额外算这部分成本), 净减就材料差。
    """
    dims_map = _template_part_dims(category, length_m, depth_cm, height_cm)
    lr = float(cfg.get("style_labor_ratio", 0.30))
    fpr = float(cfg.get("factory_profit_rate", 0.25))
    pgr = 1.0 - float(cfg.get("panse_profit_rate", 0.15))
    rc = float(cfg.get("style_remove_credit", 0.85))

    def retail(material_cost: float) -> float:
        return material_cost * (1 + lr) * (1 + fpr) / (pgr if pgr > 0 else 1.0)

    total = 0.0
    lines: list = []
    detail: list = []
    casc = f"×{1 + lr:g}人工×{1 + fpr:g}厂利÷{pgr:g}畔色"
    for p in (add_parts or []):
        r = _resolve_part(db, p, dims_map)
        amt = round(retail(r["material_cost"]), 2)
        total += amt
        r["change"], r["delta"] = "add", amt
        detail.append(r)
        note = f"材料{r['material_cost']:g}({r['formula']}){casc}"
        if not r["priced"]:
            note = "⚠物料表无此料价,计0需手填 / " + note
        lines.append({"label": f"追加: {r['name']}", "amount": amt, "note": note})
    for p in (remove_parts or []):
        r = _resolve_part(db, p, dims_map)
        amt = round(r["material_cost"] * rc, 2)   # 决策①: 删件只省材料(不退人工/利润)
        total -= amt
        r["change"], r["delta"] = "remove", -amt
        detail.append(r)
        note = f"材料{r['material_cost']:g}({r['formula']})×{rc:g}(决策①: 删件只扣材料, 不退人工/利润)"
        if not r["priced"]:
            note = "⚠物料表无此料价,计0需手填 / " + note
        lines.append({"label": f"删除: {r['name']}", "amount": -amt, "note": note})
    for p in (modify_parts or []):
        # 改部位(换料): 按材料差; 净增(新料更贵)再×(1+工厂利润)(工厂额外算这部分成本), 净减就材料差
        old_r = _resolve_part(db, {k: v for k, v in p.items() if k != "material_real"}, dims_map)
        new_r = _resolve_part(db, p, dims_map)   # 带 material_real=新料
        dmat = round(new_r["material_cost"] - old_r["material_cost"], 2)
        if dmat > 0:
            amt = round(dmat * (1 + fpr), 2)
            tail = f"材料差+{dmat:g}×(1+{fpr:g}厂利)"
        else:
            amt = dmat
            tail = f"材料差{dmat:g}"
        total += amt
        rr = dict(new_r)
        rr["change"], rr["delta"], rr["from_material"] = "modify", amt, old_r["material"]
        detail.append(rr)
        note = (f"{tail} [旧{old_r['material'] or '原料'}{old_r['material_cost']:g}"
                f"→新{new_r['material']}{new_r['material_cost']:g}({new_r['formula']})]")
        if not new_r["priced"]:
            note = "⚠新料无价,计0需手填 / " + note
        lines.append({"label": f"改: {new_r['name']}→{new_r['material']}", "amount": amt, "note": note})
    return round(total, 2), lines, detail


def compare_prices(
    db: Session, *, category: Optional[str], wood: Optional[str],
    size_m: Optional[float], our_price: float,
    baseline_price: Optional[float] = None, baseline_label: str = "本店标准款标价",
) -> dict:
    """竞品对比块: 竞品表(同类目、按尺寸接近度取5条) + 本店标准款标价基准(永远有)。

    每条算「我们价 vs 它」高/低%。竞品表空→competitors=[], baseline 仍给 → 每次算价都有对比。
    """
    from app.models.competitor import CompetitorPrice
    leaf = (category or "").split("-")[-1]
    rows = []
    try:
        q = db.query(CompetitorPrice)
        if leaf:
            q = q.filter(CompetitorPrice.category.like(f"%{leaf}%"))
        rows = q.limit(300).all()
    except Exception:  # noqa: BLE001
        rows = []

    # A1: 竞品对比排除自家店(畔色木作/畔色…), 只留真·别家竞品
    rows = [r for r in rows if not (r.store and "畔色" in r.store)]

    def price_of(r) -> float:
        v = r.latest_price if r.latest_price is not None else r.daily_price
        return float(v) if v is not None else 0.0

    if wood:
        woody = [r for r in rows if r.wood and (wood in r.wood or r.wood in wood)]
        rows = woody or rows
    rows = [r for r in rows if price_of(r) > 0]
    rows.sort(key=lambda r: abs((parse_length_m(r.sku_name) or 99) - (size_m or 0)))
    comps = []
    for r in rows[:5]:
        cp = price_of(r)
        comps.append({
            "store": r.store, "product": r.product, "sku_name": r.sku_name,
            "wood": r.wood, "price": round(cp, 2),
            "source": "最新抓取" if r.latest_price is not None else "我记价",
            "diff_pct": round((our_price - cp) / cp * 100, 1) if cp else None,
            "is_lower": our_price < cp, "link": r.link,
        })
    baseline = None
    if baseline_price and baseline_price > 0:
        baseline = {
            "label": baseline_label, "price": round(baseline_price, 2),
            "diff_pct": round((our_price - baseline_price) / baseline_price * 100, 1),
            "is_lower": our_price < baseline_price,
        }
    return {
        "our_price": round(our_price, 2),
        "competitors": comps,
        "competitor_available": bool(comps),
        "baseline": baseline,
        "note": ("竞品库暂无同类目数据(待导入新鲜竞品价); 下方为本店标准款标价基准"
                 if not comps else f"匹配 {len(comps)} 条竞品(按尺寸接近度排序)"),
    }


# ───────────────────────── 分类器前门 ───────────────────────── #

def _catalog(db: Session):
    """标准产品库 (名单文本 + 名→code 映射), 缓存。给分类器 AI 注入。"""
    def build():
        names, name2code = [], {}
        for code, name in db.query(Product.code, Product.name).all():
            if name:
                names.append(name)
                name2code[name] = code
        return "、".join(names[:400]), name2code
    return _cached("catalog", build)


_CLASSIFY_AI_SYSTEM = """你是家具定制报价分类助手。标准产品库(名称):
{catalog}

判断客户定制需求, 严格只返回 JSON(无解释/无 markdown):
{{
  "customization_type": "普通定制" 或 "特殊定制",
  "matched_product_name": "命中的标准产品名(尽量完整), 无则 null",
  "target_length_m": 目标整体长度(米,数字) 或 null,
  "target_material": "目标主材(如 黑胡桃/樱桃木/榉木) 或 null",
  "add_parts": [{{"material": "部位/材料名", "qty": 1}}],
  "remove_parts": [{{"material": "部位名", "qty": 1}}],
  "confidence": 0到1的数字,
  "reasoning": "一句话理由"
}}
规则: 命中标准库且只改尺寸/材质/颜色/简单增减 → 普通定制; 全新结构或库里没有 → 特殊定制。"""


def classify_ai(db: Session, *, text: str = "", images=None, provider=None, model: str = "") -> Optional[dict]:
    """AI 增强分类: 自由文字/图 → 结构化(类型+产品+尺寸+材质+增减)。失败返回 None 让上层回落确定性。"""
    if provider is None:
        return None
    catalog, name2code = _catalog(db)
    system = _CLASSIFY_AI_SYSTEM.format(catalog=catalog)
    try:
        if images:
            resp = provider.chat_with_images(system=system, user=text or "(见图)", images=images, max_tokens=600)
        else:
            resp = provider.chat(system=system, user=text or "", max_tokens=600)
    except Exception:  # noqa: BLE001
        return None
    data = _parse_json(getattr(resp, "text", ""))
    if data.get("customization_type") not in ("普通定制", "特殊定制"):
        return None
    code = None
    mname = data.get("matched_product_name")
    if mname:
        code = name2code.get(mname)
        if not code:
            for nm, c in name2code.items():
                if mname in nm or nm in mname:
                    code = c
                    break
    pname = db.query(Product.name).filter(Product.code == code).scalar() if code else None
    return {
        "customization_type": data["customization_type"],
        "base_product_code": code,
        "base_product_name": pname or mname,
        "target_length_m": data.get("target_length_m"),
        "target_material": data.get("target_material"),
        "add_parts": data.get("add_parts") or [],
        "remove_parts": data.get("remove_parts") or [],
        "confidence": round(float(data.get("confidence") or 0.7), 2),
        "reasoning": data.get("reasoning") or "AI 判定",
        "ai_used": True,
    }


def classify(db: Session, *, text: str = "", image_count: int = 0) -> dict:
    """判定普通定制(改现有产品) / 特殊定制(全新), 并匹配基础产品。

    确定性: 命中标准产品库(match 置信≥0.4) → 普通定制; 否则 → 特殊定制。
    (无 AI 或 AI 失败时的回落; 稳且可测、不依赖 AI 在线。)
    """
    m = match(db, text or "", "")
    # 中文描述词(尺寸/材质/动词)会拉低 token 匹配; 不中则用"去噪核心词"再匹配一次
    if not (m.get("product_code") and m.get("confidence", 0) >= 0.4):
        core = re.sub(r"\d+(?:\.\d+)?\s*(?:米|mm|cm|m)", " ", text or "")
        for _w in _WOOD_KEYWORDS:
            core = core.replace(_w, " ")
        core = re.sub(r"[改成为的把要做想换尺寸材质]", " ", core).strip()
        if core and core != (text or "").strip():
            m2 = match(db, core, "")
            if m2.get("confidence", 0) > m.get("confidence", 0):
                m = m2
    base = {
        "target_length_m": parse_length_m(text),
        "target_material": detect_wood(text),
        "add_parts": [],
        "remove_parts": [],
        "ai_used": False,
    }
    if m.get("product_code") and m.get("confidence", 0) >= 0.4:
        return {
            "customization_type": "普通定制",
            "base_product_code": m["product_code"],
            "base_product_name": m["product_name"],
            "matched_sku_code": m.get("sku_code"),
            "confidence": m["confidence"],
            "reasoning": "命中标准产品库 → 在现有产品上改尺寸/材质/增减部位",
            **base,
        }
    return {
        "customization_type": "特殊定制",
        "base_product_code": None,
        "base_product_name": None,
        "matched_sku_code": None,
        "confidence": round(max(0.5, 1 - m.get("confidence", 0)), 2),
        "reasoning": "未命中标准产品库 → 走全定制板单引擎",
        **base,
    }


def apply_size_sanity(db: Session, cfg: dict, result: dict) -> dict:
    """A6 尺寸合理性校验: 命中产品品类×解析长度不合理 → 不自动选定, 降权交候选下拉。

    防「1.5m 窄柜被判成床头柜」: 命中产品末级品类有 size_rules 且长度超「大阈值×系数」,
    则清空 base_product_code(前端不自动算价)、置 size_warning、置信压到≤0.3, 让用户从
    匹配产品 Top-N 下拉手选纠正。命中合理或无长度 → 原样返回。
    """
    from app.services import custom_quote_config_service as ccfg
    code = result.get("base_product_code")
    length = result.get("target_length_m")
    if not code or not length:
        return result
    cat = db.query(Product.category).filter(Product.code == code).scalar()
    if ccfg.size_plausible(cfg, cat, length):
        return result
    leaf = (cat or "").split("-")[-1] or "该品类"
    name = result.get("base_product_name") or code
    return {
        **result,
        "base_product_code": None,
        "base_product_name": None,
        "matched_sku_code": None,
        "confidence": round(min(float(result.get("confidence") or 0.3), 0.3), 2),
        "reasoning": (f"⚠ 疑似误判: {length:g}m 对「{leaf}」不合理(原命中 {name}), "
                      f"已不自动选定, 请从下方匹配产品下拉里确认"),
        "size_warning": True,
    }


def product_candidates(
    db: Session, text: str, *, matched_code=None, matched_name=None,
    matched_conf=0.9, limit: int = 10, length_m: Optional[float] = None,
) -> list[dict]:
    """匹配产品 Top-N 候选 (去噪后按相似度排; 确保命中项在内), 给前端下拉手选纠正。

    去噪: 剥尺寸/增减从句(不要…)/动词, 只留"樱桃木窄柜"这类产品标识词再匹配,
    否则整句噪声会把 match_ranked 全打成 0%。
    """
    from app.services.product_match_service import match_ranked
    core = re.sub(r"\d+(?:\.\d+)?\s*(?:米|mm|cm|m|公分|MM|CM|M)", " ", text or "")
    core = re.split(r"[，,。;；、]|不要|去掉|去除|改成|改为|加上|计算价格|算价|样式|定制", core)[0]
    core = re.sub(r"[的把要做想换]", " ", core).strip() or (text or "")

    def _overlap(name: str) -> float:
        # 字符重叠度兜底: token 相似度对家具长名常打 0, 用共享字符比例给备选一个可读的%
        sa, sb = set(core), set(name or "")
        return len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0

    cands = []
    for x in match_ranked(db, core, "", limit=limit * 2):
        conf = max(float(x["product_confidence"]), _overlap(x["product_name"]))
        sku = (x.get("skus") or [{}])[0].get("sku")   # 代表SKU(匹配度最高那条), 同名产品靠它区分
        cands.append({"product_code": x["product_code"], "product_name": x["product_name"],
                      "sku": sku, "confidence": round(conf, 2)})
    mc = round(float(matched_conf or 0.9), 2)
    hit = next((c for c in cands if c["product_code"] == matched_code), None) if matched_code else None
    if matched_code and hit is None:
        rep = db.query(PricingSku.sku).filter(PricingSku.product_code == matched_code).first()
        cands.append({"product_code": matched_code, "product_name": matched_name or matched_code,
                      "sku": rep[0] if rep else None, "confidence": mc})
    elif hit is not None:
        hit["confidence"] = max(hit["confidence"], mc)   # 命中项用分类器置信(更准)
    # A6: 尺寸对该品类不合理的候选降权(防 1.5m 把床头柜排前)
    if length_m:
        from app.services import custom_quote_config_service as ccfg
        _cfg = ccfg.get_config(db)
        _codes = [c["product_code"] for c in cands if c.get("product_code")]
        _cats = dict(db.query(Product.code, Product.category)
                     .filter(Product.code.in_(_codes)).all()) if _codes else {}
        for c in cands:
            if not ccfg.size_plausible(_cfg, _cats.get(c["product_code"]), length_m):
                c["confidence"] = round(c["confidence"] * 0.3, 2)
                c["size_flag"] = True
    cands.sort(key=lambda c: c["confidence"], reverse=True)
    return cands[:limit]


# ───────────────────────── 特殊定制: 部位模板 + 自动推五金 ───────────────────────── #

def suggest_part_template(db: Session, category: str, *, top: int = 40) -> list[dict]:
    """从同品类现有产品的 BOM 聚合出「标准部位清单」(部位名 + 最常见材料 + 出现频次)。

    供特殊定制时自动带板单骨架(尺寸仍由设计/客户填)。全部自有 BOM 数据。缓存 5 分钟。
    """
    if not category:
        return []
    return _cached(f"tmpl:{category}:{top}", lambda: _aggregate_template(db, category, top))


def _aggregate_template(db: Session, category: str, top: int) -> list[dict]:
    from collections import Counter

    from app.models.bom import BomLine
    leaf = category.split("-")[-1]
    codes = [p.code for p in db.query(Product).filter(
        Product.category.like(f"%{leaf}%")).all()]
    if not codes:
        return []
    lines = db.query(BomLine).filter(BomLine.product_code.in_(codes)).all()
    freq: Counter = Counter()
    mat_of: dict[str, Counter] = {}
    for ln in lines:
        key = (ln.material_name or ln.material_code or "").strip()
        if not key:
            continue
        freq[key] += 1
        mat_of.setdefault(key, Counter())[ln.material_name or ln.material_code] += 1
    out = []
    for name, n in freq.most_common(top):
        common_mat = mat_of[name].most_common(1)[0][0] if mat_of.get(name) else name
        out.append({"part": name, "default_material": common_mat, "freq": n})
    return out


# A3 常用部位(增减部位下拉兜底, 与品类 BOM 模板合并)
_COMMON_PARTS = [
    "顶板", "底板", "侧板", "背板", "中间背板", "层板", "隔板", "竖隔板",
    "抽屉", "抽屉面板", "门板", "柜门", "脚", "脚架", "踢脚",
    "拉手", "把手", "电力轨道", "灯带", "挂衣杆", "镜子", "玻璃门", "玻璃层板",
]

# A4 品类分段: 多段柜体的部位带「段-」前缀(让客服分清上柜/中段/下柜的顶板); 单段柜/桌/床用本体部位。
# (段前缀名仍能命中模板几何: _match_part_dims 子串匹配「上柜-顶板」→「顶板」。)
_CATEGORY_SEGMENTS: dict[str, list[str]] = {
    "餐边柜": ["上柜", "中段", "下柜"], "组合柜": ["上柜", "中段", "下柜"],
    "酒柜": ["上柜", "中段", "下柜"], "餐厅柜": ["上柜", "中段", "下柜"],
    "书柜": ["上柜", "下柜"], "鞋柜": ["上柜", "下柜"], "玄关柜": ["上柜", "下柜"],
}
_SEGMENT_PARTS = ["顶板", "底板", "侧板", "层板", "门板", "抽屉面板", "背板"]    # 随段重复
_WHOLE_PARTS = ["脚架", "踢脚", "灯带", "电力轨道", "挂衣杆", "玻璃门", "拉手", "把手", "镜子"]  # 整体不分段


def part_options(db: Session, *, category: str = "") -> dict:
    """A3 增减部位可选项: 常用部位 + 该品类 BOM 模板部位 + 物料表可选料名。

    给前端可搜索下拉(替代手输, 防判错)。物料表排除 人工费/打包/运费/安装/样块/样品/小样。
    返回 {parts:[...], materials:[...]} 两组字符串(前端分组展示, 仍允许手填自定义)。
    """
    leaf = (category or "").split("-")[-1].strip()
    segs = _CATEGORY_SEGMENTS.get(leaf)
    if segs:
        # A4: 多段品类 → 每段一套部位(上柜-顶板/中段-顶板/…) + 整体部位
        parts: list[str] = [f"{seg}-{p}" for seg in segs for p in _SEGMENT_PARTS]
        parts += [p for p in _WHOLE_PARTS if p not in parts]
    else:
        parts = list(_COMMON_PARTS)
    if category:
        for p in suggest_part_template(db, category, top=30):
            nm = p.get("part")
            if nm and nm not in parts:
                parts.append(nm)
    seen: set[str] = set()
    mats: list[str] = []
    rows = db.query(Material.name).filter(Material.price.isnot(None)).limit(3000).all()
    for (name,) in rows:
        if not name or name in seen:
            continue
        if any(x in name for x in ("人工费", "打包", "运费", "安装", "样块", "样品", "小样")):
            continue
        seen.add(name)
        mats.append(name)
    mats.sort()
    return {"parts": parts, "materials": mats[:400], "segments": segs or []}


# 自动推五金阈值 (后台可调; 逻辑借鉴参考项目, 数据用我们自己的)
def infer_hardware(boards: list[dict]) -> list[dict]:
    """从板单部位名推断五金: 抽屉→轨道×抽数; 层板→层板托×4; 门→铰链×2; (开口但无把手)→反弹器。

    boards: [{part, qty?}, ...] → 返回追加配件 [{material, qty, unit}]。
    """
    drawer = door = shelf = 0
    has_handle = False
    for b in boards:
        name = (b.get("part") or "").lower()
        qty = float(b.get("qty", 1) or 1)
        # 抽屉只按"面板"计抽数; 围板/侧板/后板/底板不重复计(否则 9 抽柜被算成 45 抽)
        if "drawer" in name or ("抽屉" in name and "面" in name):
            drawer += qty
        if "门" in name or "door" in name:
            door += qty
        # 层板托只给横向层板/隔板; 竖隔板(竖向分隔)不算
        if "竖" not in name and ("层板" in name or "隔板" in name or "shelf" in name):
            shelf += qty
        if "把手" in name or "handle" in name:
            has_handle = True
    extra: list[dict] = []
    if drawer > 0:
        extra.append({"material": "抽屉轨道", "qty": drawer, "unit": "付", "is_drawer_rail": True})
    if shelf > 0:
        extra.append({"material": "层板托", "qty": shelf * 4, "unit": "个"})
    if door > 0:
        extra.append({"material": "铰链", "qty": door * 2, "unit": "个"})
    if (drawer + door) > 0 and not has_handle:
        extra.append({"material": "反弹器", "qty": drawer + door, "unit": "个"})
    return extra


# 畔色自购材料(工厂报价不含 → 排除出"工厂木作对比"; 工厂五金只含 螺丝/螺栓/抽屉轨道)
_PANSE_PURCHASED_KW = ["玻璃", "岩板", "洞石", "洞洞板", "电力轨道", "多层板", "把手", "亚克力", "镜子"]


def is_panse_purchased(material: str) -> bool:
    """该材料是否畔色自购(工厂报价不含, 不计入工厂木作对比, 但计入畔色零售价)。"""
    m = material or ""
    return any(k in m for k in _PANSE_PURCHASED_KW)


def quote_heavy(
    db: Session,
    *,
    product_type: str,
    length_m: float,
    boards: list[dict],
    overall_width_m: Optional[float] = None,
    overall_height_m: Optional[float] = None,
    auto_hardware: bool = True,
) -> dict:
    """特殊定制(全新): 板单 → quote_from_spec 引擎 + 自动推五金。

    boards: [{part, material, length_cm, width_cm, qty, unit?, is_accessory?, is_drawer_rail?}]
    尺寸来自设计/客户(本引擎不臆造)。返回引擎 QuoteResult 的 dict + 自动五金清单。
    """
    from app.services.custom_quote_service import BoardSpec, quote_from_spec

    specs = [
        BoardSpec(
            part=b.get("part", ""), material=b.get("material", ""),
            length_cm=float(b.get("length_cm", 0) or 0),
            width_cm=float(b.get("width_cm", 0) or 0),
            qty=float(b.get("qty", 1) or 1), unit=b.get("unit", "平方米"),
            # 畔色自购材料(玻璃/岩板/背板多层板/把手等)自动排除出工厂木作对比
            is_accessory=bool(b.get("is_accessory")) or is_panse_purchased(b.get("material", "")),
            is_drawer_rail=bool(b.get("is_drawer_rail")),
        )
        for b in boards
    ]
    inferred = infer_hardware(boards) if auto_hardware else []
    for hw in inferred:
        specs.append(BoardSpec(
            part=hw["material"], material=hw["material"], length_cm=0, width_cm=0,
            qty=hw["qty"], unit=hw.get("unit", "个"),
            is_accessory=True, is_drawer_rail=bool(hw.get("is_drawer_rail")),
        ))

    r = quote_from_spec(
        db, product_type=product_type, length_m=length_m, boards=specs,
        overall_width_m=overall_width_m, overall_height_m=overall_height_m,
    )
    # 盈亏平衡工厂价 (净不亏): 售价 − 畔色非工厂成本((配件−抽屉轨道)+打包+运费+安装) − 售价×(平台扣点+税)
    from app.services import custom_quote_config_service as ccfg
    cfg = ccfg.get_config(db)
    plat = float(cfg.get("platform_fee_rate", 0.05))
    tax = float(cfg.get("tax_rate", 0.0))
    final = float(r.final_quote)
    predicted = float(r.factory_quote_compare)
    non_factory = (float(r.accessory_total) - float(r.drawer_rail_total)
                   + float(r.packing_fee) + float(r.freight) + float(r.install_fee))
    break_even = round(final - non_factory - final * (plat + tax), 2)
    buffer = round(break_even - predicted, 2)
    break_even_sell = round(final - buffer, 2)   # B2: 保本价(最低可卖, 全成本不含畔色利润)
    return {
        "product_type": product_type,
        "final_price": final,
        "wood_cost": float(r.wood_cost),
        "labor_fee": float(r.labor_fee),
        "accessory_total": float(r.accessory_total),
        "factory_quote_compare": predicted,
        "factory_predicted": predicted,           # 预测工厂价(=木作总成本+抽屉轨道)
        "break_even_factory": break_even,          # 盈亏平衡工厂价(净不亏红线: 高于此本单亏)
        "break_even_buffer": buffer,               # 安全垫 = 平衡价 − 预测价 (≈本单利润)
        "break_even_sell": break_even_sell,        # 保本价(最低可卖, 全成本不含畔色利润; 售价−本单利润)
        "break_even_note": f"净不亏: 售价{final:.0f} − 非工厂成本{non_factory:.0f} − 平台税{final*(plat+tax):.0f}",
        "panse_cost": float(r.panse_cost),
        "inferred_hardware": inferred,
        "wood_lines": r.wood_lines,
        "accessory_lines": r.accessory_lines,
    }
