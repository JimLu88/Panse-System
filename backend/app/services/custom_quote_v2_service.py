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

# 木种/板材关键词 (材质识别 / 反推材质delta用); 与 customization_ai_service._WOOD_KEYWORDS 同源
# 板材(多层板/海洋板)放末尾: detect_wood 取最先命中, 实木主材优先, 板材不抢识别。
_WOOD_KEYWORDS = ["黑胡桃", "樱桃木", "白蜡木", "红橡木", "白橡木", "榉木", "胡桃木", "橡木", "松木",
                  "多层板", "海洋板"]

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
    """从 SKU 名/文本里抽长度(米)。优先「长/长度X米」(防描述里"高度2.3米"被当长度);
    无显式"长"则回退首个米数(SKU 名通常只有长度)。无→None。"""
    if not text:
        return None
    m = re.search(r"(?:长度|长)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:米|m\b|M\b)", text)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:米|m\b|M\b)", text)
    return float(m.group(1)) if m else None


def parse_height_cm(text: Optional[str]) -> Optional[float]:
    """从文本抽高度(cm)。匹配「高度2.3米 / 高2.3m / 高度230cm / 总高2300mm」。无→None。"""
    if not text:
        return None
    m = re.search(r"(?:总高|高度|高)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(毫米|厘米|mm|cm|米|m)", text)
    if not m:
        return None
    v, u = float(m.group(1)), m.group(2)
    if u in ("米", "m"):
        return round(v * 100, 1)
    if u in ("mm", "毫米"):
        return round(v / 10, 1)
    return round(v, 1)   # cm / 厘米


def parse_dims_triplet(text: Optional[str]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """解析「长*宽(深)*高」三元组(行业最常见写法, 常不带单位): 1.5*0.6*0.95 / 1500×600×950 /
    150x60x95。单位启发: 三数最大值 ≤5→米 / ≤400→cm / 其余→mm(同一写法内单位一致)。
    返回 (长m, 宽cm, 高cm); 不匹配 → (None, None, None)。
    (2026-07-12 用户"1.5*0.6*0.95"完全没出价的根因之一: 旧解析器必须带米/cm单位。)"""
    if not text:
        return None, None, None
    m = re.search(r"(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)", text)
    if not m:
        return None, None, None
    a, b, c = (float(m.group(i)) for i in (1, 2, 3))
    mx = max(a, b, c)
    if mx <= 5:          # 米
        scale_m = 1.0
    elif mx <= 400:      # cm
        scale_m = 0.01
    else:                # mm
        scale_m = 0.001
    la, wb, hc = a * scale_m, b * scale_m, c * scale_m
    return round(la, 3), round(wb * 100, 1), round(hc * 100, 1)


_REMOVE_PART_RE = re.compile(
    r"(?:不要|不带|去掉|去除|不装|不需要|无需)\s*([一-龥]{0,6}?(?:底板|背板|顶板|隔板|挡板|"
    r"侧板|拉门|柜门|抽屉|玻璃|脚|腿|轮子|轨道|门))")


def parse_remove_parts(text: Optional[str]) -> list[dict]:
    """确定性解析「不要X/去掉X」→ remove_parts (AI 不可用时的兜底; 2026-07-12 '不要底板'曾被丢)。"""
    if not text:
        return []
    return [{"material": m, "qty": 1} for m in dict.fromkeys(_REMOVE_PART_RE.findall(text))]


# 特殊定制的品类猜测(未命中产品库时给板单引擎/前端预填一个方向; 顺序敏感, 具体词在前)
_CATEGORY_GUESS = [
    ("岛台", ("岛台", "中岛", "吧台")), ("餐边柜", ("餐边柜", "餐柜", "边柜")),
    ("书柜", ("书柜", "书架")), ("衣柜", ("衣柜",)), ("床头柜", ("床头柜",)),
    ("电视柜", ("电视柜",)), ("鞋柜", ("鞋柜",)), ("浴室柜", ("浴室柜",)),
    ("餐桌", ("餐桌", "饭桌")), ("书桌", ("书桌", "办公桌", "写字台")),
    ("床", ("床",)), ("柜", ("柜",)), ("桌", ("桌",)),
]


def guess_category(text: Optional[str]) -> Optional[str]:
    t = text or ""
    for cat, kws in _CATEGORY_GUESS:
        if any(k in t for k in kws):
            return cat
    return None


def _auto_top_cabinet(text: Optional[str], add_parts: list) -> list:
    """描述提到「顶柜」或「高出(来的)部分加(到)顶/上柜」→ 确保 add_parts 含一个顶柜部位
    (顶柜高度=总高−标准高 由 quote_light 的 _autofill_box_parts 自动算)。"""
    parts = list(add_parts or [])
    t = text or ""
    wants_top = ("顶柜" in t) or (("高出" in t or "高过" in t) and ("顶" in t or "上柜" in t))
    if wants_top and not any("顶柜" in (p.get("material") or p.get("name") or "") for p in parts):
        parts.append({"material": "顶柜", "qty": 1})
    return parts


# 全景柜/多段柜自动拆顶柜: 下柜高度一般固定, 变动的是顶柜(用户 2026-07-18)
_CABINET_CATS = ("餐边柜", "组合柜", "酒柜", "餐厅柜", "书柜", "鞋柜", "玄关柜", "电视柜", "岛台", "柜")


def parse_lower_cabinet_height_cm(text: Optional[str]) -> Optional[float]:
    """抽「下柜/底柜/地柜 高度 Xcm」的分段高度(区别于总高)。无→None。"""
    if not text:
        return None
    m = re.search(r"(?:下柜|底柜|地柜)\s*(?:高度|高)?\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(毫米|厘米|mm|cm|米|m)?", text)
    if not m:
        return None
    v, u = float(m.group(1)), (m.group(2) or "cm")
    if u in ("米", "m"):
        return round(v * 100, 1)
    if u in ("mm", "毫米"):
        return round(v / 10, 1)
    return round(v, 1)


def detect_top_cabinet(text: Optional[str], total_h_cm, *, lower_h_cm=None,
                       std_h_cm=None, category=None) -> tuple[Optional[float], str]:
    """多段柜(餐边柜/全景柜等): 总高 > 下柜(优先客户给的)或标准柜身高 → 顶柜高 = 差额。
    返回 (顶柜高cm 或 None, 提示串)。下柜一般不改高度, 变动的是顶柜(用户 2026-07-18)。"""
    t = text or ""
    is_cab = (category and any(c in category for c in _CABINET_CATS)) or any(c in t for c in _CABINET_CATS)
    if not is_cab or not total_h_cm:
        return None, ""
    base_h = lower_h_cm or std_h_cm
    if not base_h:
        return None, ""
    excess = round(float(total_h_cm) - float(base_h), 1)
    if excess < 5:                      # 差额太小, 视作单段柜不拆
        return None, ""
    src = "下柜" if lower_h_cm else "标准柜身"
    hint = (f"检测到多段柜: 总高 {total_h_cm:g}cm − {src} {base_h:g}cm = 顶柜 {excess:g}cm; "
            f"已自动加「顶柜」按加部位算价, 下柜按标准不变(下柜一般不改高度); 不需要可在③删除。")
    return excess, hint


def _ensure_top_cabinet(add_parts: Optional[list], height_cm: Optional[float]) -> list:
    """确保 add_parts 里有一个顶柜(有则补高度, 无则追加); 不重复添加。"""
    parts = [dict(p) for p in (add_parts or [])]
    for p in parts:
        nm = (p.get("material") or p.get("name") or "")
        if "顶柜" in nm or "上柜" in nm:
            if height_cm and not float(p.get("height_cm") or 0):
                p["height_cm"] = height_cm
            return parts
    part = {"material": "顶柜", "qty": 1}
    if height_cm:
        part["height_cm"] = height_cm
    parts.append(part)
    return parts


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
    # 外推: 用最近一段斜率; 跳过重复 x(同尺寸多变体如洞石/洞洞板, 否则 slope=0 把外推压平 → 1.5m 不追加费用)
    if x < pts[0][0]:
        x0, y0 = pts[0]
        x1, y1 = next(((px, py) for px, py in pts if px != x0), pts[-1])
    else:
        x1, y1 = pts[-1]
        x0, y0 = next(((px, py) for px, py in reversed(pts) if px != x1), pts[0])
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
    def _thick_rank(nm: str) -> int:
        nm = nm or ""
        if "2.2" in nm:
            return 0          # 实木主材优先 2.2cm 厚
        if "1.8" in nm or "18mm" in nm:
            return 1          # 板材(多层板/海洋板)取 1.8cm/18mm, 避开 0.9cm 薄背板
        return 2
    rows.sort(key=lambda m: (_thick_rank(m.name), len(m.name or "")))
    return float(rows[0].price)


# ───────────────────────── 普通定制: 锚点价 + 三类 delta ───────────────────────── #

def _sku_points(skus: list[PricingSku], tier_col: str) -> tuple[list, list]:
    """从同款多档 SKU 构造 (长度, 价) 与 (长度, wood_cost) 点集。"""
    price_pts, wood_pts = [], []
    for s in skus:
        ln = _resolve_length_m(s)
        if ln is None:
            continue
        price = getattr(s, tier_col, None)
        if price is not None:
            price_pts.append((ln, float(price)))
        if s.wood_cost is not None:
            wood_pts.append((ln, float(s.wood_cost)))
    return price_pts, wood_pts


def _area_points(skus: list[PricingSku], tier_col: str, depth_pts: list) -> tuple[list, list]:
    """面积一致定价点云: (底面积㎡, 价) 与 (底面积㎡, wood_cost)。

    宽取 size_info 的 深/宽; 缺则按 depth_pts 插值该长度的标准宽; 长或宽都拿不到 → 跳过该 SKU。
    按面积排序后做「累计取大」(cummax): 保证 价/木作 随面积单调不降(更大绝不更便宜, 兜个别档倒挂)。
    该款完全无宽数据(如部分床头柜)→ 返回空 → 调用方自动退回按长度插值(老口径, 行为不变)。"""
    p_by_area: dict[float, float] = {}
    w_by_area: dict[float, float] = {}
    for s in skus:
        ln = _resolve_length_m(s)
        if ln is None:
            continue
        _l, w, _h = _parse_size_info(s.size_info)
        if (w is None or w <= 0) and depth_pts:
            w = interp(depth_pts, ln)[0]
        if not w or w <= 0:
            continue
        area = round(ln * (float(w) / 100.0), 4)
        price = getattr(s, tier_col, None)
        if price is not None:
            p_by_area[area] = float(price)          # 同面积(黑/白岩板)同价, 覆盖即可
        if s.wood_cost is not None:
            w_by_area[area] = float(s.wood_cost)

    def _cummax(d: dict) -> list:
        out, run = [], None
        for a in sorted(d):
            run = d[a] if run is None else max(run, d[a])
            out.append((a, run))
        return out
    return _cummax(p_by_area), _cummax(w_by_area)


def _parse_size_info(size_info: Optional[str]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """'长度：1400mm；深度：750mm；高度：750mm' → (长cm, 深/宽cm, 高cm)。缺→None。mm→cm(÷10)。"""
    import re
    if not size_info:
        return None, None, None

    def _g(pat: str) -> Optional[float]:
        m = re.search(pat, size_info)
        return round(float(m.group(1)) / 10.0, 1) if m else None

    return (_g(r"长\s*度?\s*[：:]\s*(\d+(?:\.\d+)?)"),
            _g(r"(?:深|宽)\s*度?\s*[：:]\s*(\d+(?:\.\d+)?)"),
            _g(r"高\s*度?\s*[：:]\s*(\d+(?:\.\d+)?)"))


def _resolve_length_m(s) -> Optional[float]:
    """SKU 的主变体长度(米): 优先 SKU 名/编码的「X米」, 否则退回 size_info 长度(cm÷100)。
    修床头柜等按 size_info 尺寸分档(SKU 名只叫「标准/窄款」无「X米」)的产品 —— 否则解析不到长度
    → price_pts 空 → 退回代表档固定价(改长度/宽高价都不动)。"""
    ln = parse_length_m(s.sku) or parse_length_m(s.sku_code)
    if ln is not None:
        return ln
    l_cm, _d, _h = _parse_size_info(s.size_info)
    return round(l_cm / 100.0, 3) if l_cm else None


def _dim_points(skus: list[PricingSku]) -> tuple[list, list]:
    """同款多档 SKU 的 size_info → (长m, 深/宽cm) + (长m, 高cm) 点集, 供按目标长度插值出"标准宽高"。"""
    depth_pts, height_pts = [], []
    for s in skus:
        ln = _resolve_length_m(s)
        if ln is None:
            continue
        _l, d, h = _parse_size_info(s.size_info)
        if d is not None:
            depth_pts.append((ln, d))
        if h is not None:
            height_pts.append((ln, h))
    return depth_pts, height_pts


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
    target_width_cm: Optional[float] = None,
    target_height_cm: Optional[float] = None,
    target_material: Optional[str] = None,
    add_parts: Optional[list[dict]] = None,
    remove_parts: Optional[list[dict]] = None,
    modify_parts: Optional[list[dict]] = None,
    price_tier: str = "daily",
    base_sku_code: Optional[str] = None,
    factory_profit_rate: float = 0.25,
) -> dict:
    """普通定制报价 (改现有产品): 真实SKU锚点价 + 尺寸delta(策略C) + 材质delta(wood_cost反推) + 增减部位delta。

    全程纯算术, 0 次 AI。返回 {final_price, anchor, breakdown[], ...}; 查不到产品→error。
    """
    tier_col = _PRICE_TIERS.get(price_tier, "daily_price")
    skus = db.query(PricingSku).filter(PricingSku.product_code == base_product_code).all()
    if not skus:
        return {"error": f"定价表无此产品 {base_product_code}", "final_price": None, "breakdown": []}
    # SKU 匹配(#29): 选了具体 SKU → 锁定同变体(去尺寸签名相同的档), 不混洞石/洞洞板, 大尺寸沿本变体外推
    if base_sku_code:
        chosen = next((s for s in skus if base_sku_code in (s.sku_code or "", s.sku or "")), None)
        if chosen:
            vk = _sku_variant_key(chosen.sku or chosen.sku_code or "")
            same = [s for s in skus if _sku_variant_key(s.sku or s.sku_code or "") == vk]
            if same:
                skus = same

    prod = db.query(Product).filter(Product.code == base_product_code).first()
    price_pts, wood_pts = _sku_points(skus, tier_col)
    depth_pts, height_pts = _dim_points(skus)
    std_w = interp(depth_pts, target_length_m)[0] if (target_length_m and depth_pts) else None
    std_h = interp(height_pts, target_length_m)[0] if (target_length_m and height_pts) else None
    eff_w = float(target_width_cm) if target_width_cm else std_w   # 没填宽 → 用该长度的标准宽
    breakdown: list[dict] = []

    # ── 锚点价 ── 面积一致定价 (2026-07-04 修「越小越贵」非单调 bug) ──
    #   老法 = 按长度插锚点价 + 宽/高偏离"该长度标准宽"的木作溢价; 但标准宽随长度缩, 把绝对宽固定在
    #   高于标准再缩长度 → 宽溢价暴涨盖过锚点跌 → 越短越贵 (岩板/玻璃桌尤甚, 锚点对长度不敏感)。
    #   新法 = 按【实际底面积 长×宽】插真实 SKU 价, 长、宽同一 ¥/㎡ → 更大必更贵 (单调)。
    #   宽=标准时与老口径吻合 (实测 2669≈2670); 该款无宽数据 → 自动退回按长度插值 (行为不变)。
    area_price_pts, area_wood_pts = _area_points(skus, tier_col, depth_pts)
    target_area = (target_length_m * eff_w / 100.0) if (target_length_m and eff_w) else None
    used_area = bool(target_area and area_price_pts)
    if used_area:
        anchor, method = interp(area_price_pts, target_area)
        anchor_wood, _ = interp(area_wood_pts, target_area) if area_wood_pts else (None, "no-data")
        note = f"面积定价 {target_length_m}m×{eff_w:.0f}cm≈{target_area:.3f}㎡ ({method}, {len(area_price_pts)}点)"
        # 面积远小于最小采样SKU时: 平坦价曲线线性外推被大额固定成本截距顶高(实测洞石餐边柜 0.6m×浅进深
        # →¥14k 而实际~5k) → 改按过最小SKU点的面积正比估(取更低), 贴"小很多的定制约小很多的价"; wood同缩。
        a0, p0 = area_price_pts[0]                                   # _cummax 已升序, [0]=最小面积
        if a0 > 0 and target_area < a0:
            prop = round(p0 * (target_area / a0), 2)
            if prop < anchor:
                if anchor_wood is not None and area_wood_pts:
                    anchor_wood = round(area_wood_pts[0][1] * (target_area / a0), 2)
                anchor, method = prop, "area-prop"
                note = (f"面积定价 {target_length_m}m×{eff_w:.0f}cm≈{target_area:.3f}㎡: 远小于最小标准款"
                        f"({a0:.2f}㎡/¥{p0:.0f}) → 按面积正比估 ¥{prop:.0f} ⚠系统估算, 建议人工核价")
    elif target_length_m and price_pts:
        anchor, method = interp(price_pts, target_length_m)
        anchor_wood, _ = interp(wood_pts, target_length_m) if wood_pts else (None, "no-data")
        note = f"策略C 长度插值@{target_length_m}m ({method}, {len(price_pts)}档; 无宽数据退长度口径)"
        l0, lp0 = min(price_pts)                                     # 最小长度档
        if l0 > 0 and target_length_m < l0:
            prop = round(lp0 * (target_length_m / l0), 2)
            if prop < anchor:
                if anchor_wood is not None and wood_pts:
                    anchor_wood = round(min(wood_pts)[1] * (target_length_m / l0), 2)
                anchor, method = prop, "len-prop"
                note = (f"策略C @{target_length_m}m: 远小于最小档({l0}m/¥{lp0:.0f}) → 按长度正比估 "
                        f"¥{prop:.0f} ⚠系统估算, 建议人工核价")
    else:
        rep = sorted(skus, key=lambda s: _resolve_length_m(s) or 0)[len(skus) // 2]
        anchor = float(getattr(rep, tier_col, None) or rep.daily_price or rep.list_price or 0)
        anchor_wood = float(rep.wood_cost) if rep.wood_cost is not None else None
        note = f"代表档 {rep.sku or rep.sku_code}"
    if not anchor:
        return {"error": "锚点价缺失(该产品4档价为空)", "final_price": None, "breakdown": []}
    # 尺寸超出标准 SKU 范围(偏大)仍走线性外推 → 标注为外推估算, 提示人工复核(偏小已在上面走面积正比)
    if "extrap" in note:
        note += " ⚠尺寸超出标准SKU范围, 锚点为外推估算, 建议人工复核"
    breakdown.append({"label": "底面积定价(长×宽)" if used_area else "标准原价(同尺寸)",
                      "amount": round(anchor, 2), "note": note})

    final = anchor

    # ── 材质 delta ── target_material 为物料库全名(带厚度/贴皮, 表驱动下拉)精确取价; 换料/换厚度均算 ──
    material_delta = 0.0
    base_wood = detect_wood(prod.name if prod else "") or detect_wood(
        (prod.main_material if prod else "") or "")
    tgt_wood = detect_wood(target_material) if target_material else None
    # 整柜换料仅限「目标是实木主材且与底材不同」。洞石/岩板/玻璃等面板/配件材质不是整柜换料
    # (要岩板背板/玻璃门请走增减部位), 同一木种也不换。防 AI 把产品名里的"洞石"当换料算出
    # 虚假差额 + 污染顶柜木种(2026-07-18: 洞石餐边柜 target_material=洞石 曾误算 −4062 且顶柜按洞石计价)。
    if target_material and not tgt_wood:
        breakdown.append({"label": f"材质「{target_material}」", "amount": 0.0,
                          "note": "面板/配件类材质(非实木主材), 不作整柜换料; 岩板/玻璃等部件请在增减部位加"})
        target_material = None                                       # 不作整柜材质, 也不拿去当顶柜/箱体木种
    elif target_material and tgt_wood and (not base_wood or tgt_wood != base_wood):
        tgt = tgt_wood                                               # 短木名(找现成同款)
        new_u = _wood_unit_price(db, target_material)                # 全名精确取价(带厚度)
        orig_u = _wood_unit_price(db, base_wood) if base_wood else None
        if not new_u:
            breakdown.append({"label": f"换料→{target_material}", "amount": 0.0,
                              "note": "该材质不在物料库, 需人工核价"})
        elif orig_u and abs(new_u - orig_u) > 1e-6:                  # 价差非0(换料/换厚度)才算
            # 换料只调"材质成本差额"(同一产品, 不换产品): (新木单价−原木单价)×木作面积×(1+厂利)。
            # 用户拍板 2026-06-20: 樱桃木→榉木应只减两者木材差额, 绝不能减掉整份产品价(原 −8757 是
            # 错误地切到另一个更便宜的现成产品)。现成同款只作"可切换"参考提示, 不替换价格。
            sib = _find_sibling_by_material(db, prod, tgt) if (prod and tgt != base_wood) else None
            sib_note = ""
            if sib:
                sib_skus = db.query(PricingSku).filter(
                    PricingSku.product_code == sib.code).all()
                sp, _sw = _sku_points(sib_skus, tier_col)
                if target_length_m and sp:
                    sib_price, _m = interp(sp, target_length_m)
                else:
                    sib_price = float(getattr(sib_skus[0], tier_col, None) or 0) if sib_skus else 0
                if sib_price:
                    sib_note = f"; 参考现成同款 {sib.code} {sib.name} ¥{round(sib_price,2)}(客户认可可直接切该款)"
            if anchor_wood:
                area = anchor_wood / orig_u
                material_delta = round((new_u - orig_u) * area * (1 + factory_profit_rate), 2)
                breakdown.append({"label": f"换料→{target_material}", "amount": material_delta,
                                  "note": f"只调材质差额: 面积≈{area:.2f}㎡(wood{anchor_wood:.0f}÷原{orig_u:.0f})"
                                          f"×(新{new_u:.0f}−原{orig_u:.0f})×(1+{factory_profit_rate}){sib_note}"})
            else:
                breakdown.append({"label": f"换料→{target_material}", "amount": 0.0,
                                  "note": f"缺木作成本(wood_cost)无法反推面积, 需人工核{sib_note}"})
    final += material_delta

    # ── 尺寸变体 delta ── 宽度已并入上面「底面积定价」锚点(长宽同价/㎡, 不再单独加宽溢价);
    #   这里只处理【高度】偏离标准高。有顶柜等木作盒子时: 高出部分由顶柜单独算价, 整柜不按高缩放
    #   (避免重复计价; 用户拍板 2026-06-20)。std_w/std_h 已在锚点段算好。 ──
    size_delta = 0.0
    has_box_part = any(_is_box_part((p.get("material") or p.get("name") or "")) for p in (add_parts or []))
    if used_area and target_width_cm and std_w and abs(float(target_width_cm) - std_w) > 0.1:
        breakdown.append({
            "label": f"宽 {float(target_width_cm):.0f}cm(标准{std_w:.0f}) 已计入底面积定价",
            "amount": 0.0, "note": "长、宽同一 ¥/㎡, 宽度差异已体现在上面的底面积定价里"})
    if anchor_wood and std_h and target_height_cm and not has_box_part:
        th = float(target_height_cm)
        height_factor = th / std_h
        if abs(height_factor - 1) > 1e-3:
            size_delta = round(float(anchor_wood) * (height_factor - 1) * (1 + factory_profit_rate), 2)
            breakdown.append({
                "label": f"高度变体(高{th:.0f}, 标准{std_h:.0f}cm)",
                "amount": size_delta,
                "note": f"木作{float(anchor_wood):.0f}×(高比{height_factor:.3f}−1)×(1+{factory_profit_rate})",
            })
    final += size_delta

    # ── 增减部位 delta (逐部位 cascade: 木作用模板几何×木单价 / 配件×计价单位 → ×人工×厂利÷畔色) ──
    from app.services import custom_quote_config_service as ccfg
    cfg = ccfg.get_config(db)
    category = (prod.category if prod else None) or base_product_code
    # 顶柜等木作盒子: 总高>标准高 → 自动算 顶柜高=总高−标准高、长=柜宽、宽=柜深、木种=柜体木 (用户拍板 2026-06-20)
    _box_wood = target_material
    if not _box_wood and prod:
        _box_wood = detect_wood(prod.name or "") or detect_wood(prod.main_material or "")
    _box_wood = _box_wood or "樱桃木"
    # 直接算价(未过分类器文本)时的顶柜兜底: 柜类 + 总高明显超标准柜身高 + 未显式给顶柜 → 自动加顶柜
    # (下柜按标准不变, 高出部分归顶柜; 用户 2026-07-18。走了分类器的已在 classify 里加过, has_box→不重复)
    if (std_h and target_height_cm and float(target_height_cm) > float(std_h) + 5
            and any(c in str(category) for c in _CABINET_CATS)
            and not any(_is_box_part((p.get("material") or p.get("name") or "")) for p in (add_parts or []))):
        add_parts = _ensure_top_cabinet(add_parts, round(float(target_height_cm) - float(std_h), 1))
    add_parts = _autofill_box_parts(
        add_parts, length_m=target_length_m, depth_cm=target_width_cm, std_w=std_w,
        total_h_cm=target_height_cm, std_h=std_h, box_wood=_box_wood)
    addrm_delta, addrm_lines, parts_detail = style_delta(
        db, category=category, length_m=target_length_m,
        add_parts=add_parts, remove_parts=remove_parts, modify_parts=modify_parts, cfg=cfg,
        depth_cm=target_width_cm, height_cm=target_height_cm,   # 部位尺寸随总宽/高联动(用户 2026-06-20: 高出部分加到顶柜)
    )
    breakdown.extend(addrm_lines)
    final += addrm_delta

    # ── 工厂价预测 + 盈亏平衡 (定价表 factory_cost/accounting_cost 插值; accounting 已含真实扣点/税) ──
    fac_pts, acc_pts = [], []
    for s in skus:
        ln = _resolve_length_m(s)
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
        "size_delta": size_delta,
        "std_width_cm": round(std_w, 1) if std_w else None,    # 该长度的标准宽/深(cm), 前端预填
        "std_height_cm": round(std_h, 1) if std_h else None,
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


def _is_box_part(name: str) -> bool:
    """名字含「柜」→ 木作盒子(顶柜/吊柜/边柜/地柜…), 配合 height_cm>0 走6块板算价。"""
    return bool(name) and ("柜" in name)


def _box_material_cost(length_cm: float, width_cm: float, height_cm: float,
                       qty: float, wood_unit: float) -> tuple[float, float, str]:
    """木作盒子: 顶+底+1层板(3×长宽) + 左右2侧(2×宽高) + 背(长高) 板面积 × 木单价 → (材料成本, 面积㎡, 公式)。"""
    L, W, H = length_cm, width_cm, height_cm
    area = (3 * (L * W) + 2 * (W * H) + (L * H)) / 10000.0
    cost = area * qty * wood_unit
    formula = f"盒{area:.3f}㎡(顶底层3×{L:.0f}×{W:.0f}+2侧2×{W:.0f}×{H:.0f}+背{L:.0f}×{H:.0f})×{wood_unit:g}"
    return round(cost, 2), area, formula


def _autofill_box_parts(add_parts, *, length_m, depth_cm, std_w, total_h_cm, std_h, box_wood):
    """顶柜等木作盒子: 总高>标准高 → 自动算 高=总高−标准高(高出部分)、长=柜宽、宽=柜深、木种=柜体木
    (用户拍板 2026-06-20: 高出部分加到顶柜, 自动重算顶柜尺寸)。已显式填的字段不覆盖。"""
    if not add_parts:
        return add_parts
    out = []
    for p in (add_parts or []):
        p = dict(p)
        name = (p.get("material") or p.get("name") or "")
        if _is_box_part(name):
            if not float(p.get("height_cm") or 0) and total_h_cm and std_h and float(total_h_cm) > float(std_h):
                p["height_cm"] = round(float(total_h_cm) - float(std_h), 1)
            if not float(p.get("length_cm") or 0) and length_m:
                p["length_cm"] = round(float(length_m) * 100, 1)
            if not float(p.get("width_cm") or 0):
                p["width_cm"] = round(float(depth_cm or std_w or 0), 1)
            if not (p.get("material_real") or "").strip() and box_wood:
                p["material_real"] = box_wood
        out.append(p)
    return out


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
    height_cm = float(part.get("height_cm") or 0)
    # 木作盒子(顶柜等, 名含「柜」且有高度): 6块板面积×木单价, 不查物料表 (用户拍板 2026-06-20)
    if _is_box_part(name) and height_cm > 0 and length_cm > 0 and width_cm > 0:
        box_wood = (part.get("material_real") or "").strip() or material or "樱桃木"
        wu = _wood_unit_price(db, box_wood) or 0.0
        cost, area, formula = _box_material_cost(length_cm, width_cm, height_cm, qty, wu)
        return {
            "name": name, "material": box_wood, "unit": "盒(6板)",
            "qty": qty, "length_cm": round(length_cm, 1), "width_cm": round(width_cm, 1),
            "height_cm": round(height_cm, 1), "area_m2": round(area, 3), "unit_price": wu,
            "material_cost": cost, "formula": formula,
            "panse_purchased": False, "priced": wu > 0,
        }
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
  "target_width_cm": 目标深度/宽(厘米,数字) 或 null,
  "target_height_cm": 目标整体高度(厘米,数字; "高度2.3米"→230) 或 null,
  "target_material": "目标主材(如 黑胡桃/樱桃木/榉木) 或 null",
  "add_parts": [{{"material": "部位/材料名", "qty": 1}}],
  "remove_parts": [{{"material": "部位名", "qty": 1}}],
  "confidence": 0到1的数字,
  "reasoning": "一句话理由"
}}
规则: 命中标准库且只改尺寸/材质/颜色/简单增减 → 普通定制; 全新结构或库里没有 → 特殊定制。
尺寸: "高度/总高 X米/Xcm" 填 target_height_cm(厘米), "长度 X米" 填 target_length_m(米) —— 别把高度当长度。
顶柜: 客户说"高出来的部分加到顶柜"/"加顶柜"/"顶上加柜" → add_parts 里加 {{"material": "顶柜", "qty": 1}}(高度差额系统自动算, 你不用算)。"""


def _sane_dim(v, lo: float, hi: float) -> Optional[float]:
    """AI 抽的尺寸出合理区间(如 0.95米被写成 950cm)→ 弃用, 让规则解析值顶上 (2026-07-12 实测坑)。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


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
    # 尺寸: 明确写「A×B×C」三元组时优先信它(客户显式几何), 防 AI 幻觉长度(实测把 27.5 读成 2.75m)
    _tl, _tw, _th = parse_dims_triplet(text)
    length_m = _tl if _tl is not None else (_sane_dim(data.get("target_length_m"), 0.2, 6) or parse_length_m(text))
    width_cm = _tw if _tw is not None else _sane_dim(data.get("target_width_cm"), 10, 500)
    height_cm = _th if _th is not None else (_sane_dim(data.get("target_height_cm"), 10, 350) or parse_height_cm(text))
    add_parts = _auto_top_cabinet(text, data.get("add_parts") or [])
    cat_guess = guess_category(text)
    lower_h = parse_lower_cabinet_height_cm(text)
    top_h, top_hint = detect_top_cabinet(text, height_cm, lower_h_cm=lower_h, category=cat_guess)
    if top_h:
        add_parts = _ensure_top_cabinet(add_parts, top_h)
    return {
        "customization_type": data["customization_type"],
        "base_product_code": code,
        "base_product_name": pname or mname,
        "target_length_m": length_m,
        "target_width_cm": width_cm,
        "target_height_cm": height_cm,
        "target_material": data.get("target_material") or detect_wood(text),
        "add_parts": add_parts,
        "remove_parts": data.get("remove_parts") or parse_remove_parts(text),
        "category_guess": cat_guess,
        "lower_cabinet_height_cm": lower_h,
        "top_cabinet_height_cm": top_h,
        "top_cabinet_hint": top_hint or None,
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
    # 尺寸: 显式单位写法优先, 否则「长*宽*高」三元组兜底(1.5*0.6*0.95 这类最常见, 2026-07-12)
    _tl, _tw, _th = parse_dims_triplet(text)
    # 高度: 有「A×B×C」三元组时用它的总高(防 parse_height_cm 把"下柜高度92"当整体高)
    _height = _th if _th is not None else parse_height_cm(text)
    _add = _auto_top_cabinet(text, [])
    # 多段柜(全景柜等)自动拆顶柜: 总高 − 下柜(客户给的) = 顶柜高(2026-07-18)
    _low_h = parse_lower_cabinet_height_cm(text)
    _top_h, _top_hint = detect_top_cabinet(text, _height, lower_h_cm=_low_h, category=guess_category(text))
    if _top_h:
        _add = _ensure_top_cabinet(_add, _top_h)
    base = {
        "target_length_m": _tl if _tl is not None else parse_length_m(text),
        "target_width_cm": _tw,
        "target_height_cm": _height,
        "target_material": detect_wood(text),
        "add_parts": _add,
        "remove_parts": parse_remove_parts(text),
        "lower_cabinet_height_cm": _low_h,
        "top_cabinet_height_cm": _top_h,
        "top_cabinet_hint": _top_hint or None,
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
    cat_guess = guess_category(text)
    _dims_txt = "、".join(x for x in (
        f"长{base['target_length_m']}米" if base.get("target_length_m") else "",
        f"深{base['target_width_cm']:g}cm" if base.get("target_width_cm") else "",
        f"高{base['target_height_cm']:g}cm" if base.get("target_height_cm") else "") if x)
    return {
        "customization_type": "特殊定制",
        "base_product_code": None,
        "base_product_name": None,
        "matched_sku_code": None,
        "confidence": round(max(0.5, 1 - m.get("confidence", 0)), 2),
        "category_guess": cat_guess,
        "reasoning": ("未命中标准产品库 → 走全定制板单引擎"
                      + (f"; 已解析: {_dims_txt or '无尺寸'}"
                         + (f", 材质{base['target_material']}" if base.get("target_material") else "")
                         + (f", 品类猜测「{cat_guess}」" if cat_guess else ", 描述里没有品类词(如 岛台/餐边柜), 请在下方③填品类"))),
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


def _sku_variant_key(name: str) -> str:
    """SKU 变体签名 = 去掉尺寸后的名(洞石/洞洞板等变体区分; 同变体不同尺寸 → 同签名)。"""
    return re.sub(r"[-\s]*\d+(?:\.\d+)?\s*米", "", name or "").strip()


def sku_candidates(db: Session, text: str, product_code: str, *, limit: int = 10) -> list[dict]:
    """该产品各 SKU 的匹配候选(按与描述字符重叠%排序), 给前端 SKU 下拉锁变体。"""
    skus = db.query(PricingSku).filter(PricingSku.product_code == product_code).all()
    core = re.split(r"[，,。;；、]|计算价格|算价|样式", text or "")[0]
    sa = set(core)
    out = []
    for s in skus:
        name = s.sku or s.sku_code or ""
        sb = set(name)
        conf = len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0
        price = s.big_promo if s.big_promo is not None else s.daily_price
        out.append({"sku_code": s.sku_code, "sku_name": name,
                    "price": round(float(price), 2) if price is not None else None,
                    "confidence": round(conf, 2)})
    out.sort(key=lambda c: c["confidence"], reverse=True)
    return out[:limit]


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
        top_sku = (x.get("skus") or [{}])[0]   # 匹配度最高的代表SKU, 同名产品靠它区分
        # 纳入 SKU 级相关度: 玻璃底座/玻璃门等变体只在 SKU 名里体现, 产品名相似度低,
        # 否则「樱桃木玻璃柜」这类靠变体命中的产品会被产品名分数压下、浮不上来 (2026-07-04)。
        conf = max(float(x["product_confidence"]), float(top_sku.get("confidence") or 0),
                   _overlap(x["product_name"]))
        sku = top_sku.get("sku")
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
                # 只标 ⚠ 警告、不埋没: 尺寸对该品类偏大仍按相关度保留在候选里。否则用户
                # 明确点名的产品(如 1m「轻盈边柜」, 品类却标成床头柜上限0.5m)会被 ×0.3 踢出候选、
                # 连手选都选不到。轻微降权让同相关度的它稍下沉即可 (2026-07-04)。
                c["confidence"] = round(c["confidence"] * 0.9, 2)
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
    # A: 改材质下拉 = 物料库 MW 木材/板材全名(自带厚度/贴皮, 表驱动); detect_wood 仍用短名做文本识别
    wood_rows = db.query(Material.name).filter(
        Material.code.like("MW%"), Material.price.isnot(None)).all()
    woods = sorted({nm for (nm,) in wood_rows if nm and not any(
        x in nm for x in ("样块", "样品", "小样"))})
    return {"parts": parts, "materials": mats[:400], "segments": segs or [],
            "woods": woods}


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
