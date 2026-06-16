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


def quote_light(
    db: Session,
    *,
    base_product_code: str,
    target_length_m: Optional[float] = None,
    target_material: Optional[str] = None,
    add_parts: Optional[list[dict]] = None,
    remove_parts: Optional[list[dict]] = None,
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
    breakdown.append({"label": "锚点价", "amount": round(anchor, 2), "note": note})

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

    # ── 增减部位 delta ──
    addrm_delta = 0.0
    for p in (add_parts or []):
        unit_price = _part_price(db, p.get("material") or p.get("name") or "")
        qty = float(p.get("qty", 1) or 1)
        cost = round(unit_price * qty * (1 + factory_profit_rate), 2)
        addrm_delta += cost
        breakdown.append({"label": f"追加: {p.get('material') or p.get('name')}", "amount": cost,
                          "note": f"{unit_price}×{qty}×{1+factory_profit_rate}"})
    for p in (remove_parts or []):
        unit_price = _part_price(db, p.get("material") or p.get("name") or "")
        qty = float(p.get("qty", 1) or 1)
        cost = round(unit_price * qty * (1 + factory_profit_rate), 2)
        addrm_delta -= cost
        breakdown.append({"label": f"删除: {p.get('material') or p.get('name')}", "amount": -cost,
                          "note": f"−{unit_price}×{qty}×{1+factory_profit_rate}"})
    final += addrm_delta

    return {
        "base_product_code": base_product_code,
        "base_product_name": prod.name if prod else None,
        "anchor": round(anchor, 2),
        "anchor_method": note,
        "material_delta": material_delta,
        "addremove_delta": round(addrm_delta, 2),
        "final_price": round(final, 2),
        "price_tier": price_tier,
        "breakdown": breakdown,
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


def _part_price(db: Session, name: str) -> float:
    """部位/配件单价: 物料表精确→模糊(排样块)。查不到→0(上层标注需人工)。"""
    if not name:
        return 0.0
    from sqlalchemy import func, select
    row = db.execute(select(Material.price).where(Material.name == name)).scalar_one_or_none()
    if row is None:
        hit = db.execute(
            select(Material.price).where(
                Material.name.like(f"%{name}%"), Material.price.isnot(None),
                ~Material.name.like("%样块%"), ~Material.name.like("%样品%"),
            ).order_by(func.length(Material.name)).limit(1)
        ).first()
        row = hit[0] if hit else None
    return float(row) if row is not None else 0.0


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
    return {
        "product_type": product_type,
        "final_price": float(r.final_quote),
        "wood_cost": float(r.wood_cost),
        "labor_fee": float(r.labor_fee),
        "accessory_total": float(r.accessory_total),
        "factory_quote_compare": float(r.factory_quote_compare),
        "panse_cost": float(r.panse_cost),
        "inferred_hardware": inferred,
        "wood_lines": r.wood_lines,
        "accessory_lines": r.accessory_lines,
    }
