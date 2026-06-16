"""定制报价 v2 — 品类参数化板单模板.

给「品类 + 外形尺寸(长L×深D×高H, cm) + 配置(抽屉/门/层板数)」, 自动生成板单
(每块板的部位/材料/长宽/数量 由 L/D/H 公式算出), 直接喂 quote-heavy 算价。
把"特殊定制材料"从手搓/AI 拆板 → "填外形→自动出板", 起点板单完整、可前端再调。

材料默认: 主材 樱桃木-2.2cm; 背板 实木多层板1.8cm; 抽屉围板 松木-0.9cm(均可传参覆盖)。
说明: 几何为工程近似(板厚扣减、列均分), 给一份合理起点板单, 非逐毫米精确; 用户在前端可改。
财务纪律: 纯计算, 不写任何表。
"""
from __future__ import annotations

import math
from typing import Optional

MAIN_MATERIAL = "樱桃木-2.2cm"
BACK_MATERIAL = "实木多层板1.8cm"
DRAWER_MATERIAL = "松木-0.9cm"
_T = 2.0  # 板厚 cm (内空扣减近似)

# 品类画像: family + 默认 深/高(cm) + 默认 列/抽屉/门/层板数。前端可覆盖任意项。
CATEGORY_PROFILE: dict[str, dict] = {
    "餐边柜": {"family": "cabinet", "D": 40, "H": 80, "cols": 3, "drawers": 0, "doors": 3, "shelves": 1},
    "电视柜": {"family": "cabinet", "D": 42, "H": 53, "cols": 3, "drawers": 1, "doors": 2, "shelves": 1},
    "斗柜": {"family": "cabinet", "D": 45, "H": 90, "cols": 1, "drawers": 5, "doors": 0, "shelves": 0},
    "床头柜": {"family": "cabinet", "D": 40, "H": 45, "cols": 1, "drawers": 2, "doors": 0, "shelves": 0},
    "书柜": {"family": "cabinet", "D": 30, "H": 200, "cols": 2, "drawers": 0, "doors": 0, "shelves": 5},
    "鞋柜": {"family": "cabinet", "D": 35, "H": 100, "cols": 2, "drawers": 0, "doors": 2, "shelves": 3},
    "组合柜": {"family": "cabinet", "D": 40, "H": 80, "cols": 2, "drawers": 1, "doors": 1, "shelves": 1},
    "梳妆台": {"family": "cabinet", "D": 40, "H": 75, "cols": 1, "drawers": 2, "doors": 0, "shelves": 0},
    "岛台": {"family": "cabinet", "D": 60, "H": 90, "cols": 2, "drawers": 2, "doors": 1, "shelves": 1},
    "餐桌": {"family": "table", "D": 80, "H": 75},
    "书桌": {"family": "table", "D": 60, "H": 75},
    "餐凳": {"family": "table", "D": 35, "H": 45},
    "床": {"family": "bed", "D": 200, "H": 40},
}


def _leaf(category: Optional[str]) -> str:
    return (category or "").split("-")[-1].strip()


def _add(out: list, part: str, material: str, length_cm: float, width_cm: float, qty: float,
         is_accessory: bool = False) -> None:
    if qty > 0 and length_cm > 0 and width_cm > 0:
        out.append({
            "part": part, "material": material,
            "length_cm": round(length_cm, 1), "width_cm": round(width_cm, 1),
            "qty": int(qty) if float(qty).is_integer() else round(qty, 2),
            "is_accessory": is_accessory,
        })


def _cabinet(L, D, H, cols, drawers, doors, shelves, main, back, drawer) -> list[dict]:
    out: list[dict] = []
    _add(out, "顶板", main, L, D, 1)
    _add(out, "底板", main, L, D, 1)
    _add(out, "左右侧板", main, D, H, 2)
    if cols > 1:
        _add(out, "竖隔板", main, D, H - 2 * _T, cols - 1)
    inner_w = (L - (cols + 1) * _T) / max(cols, 1)   # 每列内宽
    if shelves > 0:
        _add(out, "层板", main, inner_w, D - _T, shelves * cols)
    _add(out, "背板", back, L, H, 1)
    if doors > 0:
        _add(out, "门板", main, (L - (doors + 1)) / doors, H - 2 * _T, doors)
    if drawers > 0:
        rows = math.ceil(drawers / max(cols, 1))
        dw = inner_w
        dh = (H - 2 * _T) / max(rows, 1)
        _add(out, "抽屉面板", main, dw, dh, drawers)
        _add(out, "抽屉侧板", drawer, D - 3, dh - 2, drawers * 2)
        _add(out, "抽屉后板", drawer, dw - 2, dh - 2, drawers)
        _add(out, "抽屉底板", drawer, dw - 2, D - 3, drawers)
    return out


def _table(L, D, H, main) -> list[dict]:
    out: list[dict] = []
    _add(out, "桌面", main, L, D, 1)
    _add(out, "桌腿", main, 8, H - 3, 4)
    _add(out, "裙板-长边", main, L - 12, 10, 2)
    _add(out, "裙板-短边", main, D - 12, 10, 2)
    return out


def _bed(L, D, H, main, drawer) -> list[dict]:
    # L=床宽(对应 SKU 的「X米」), D=床长(默认2米), H=床头高
    out: list[dict] = []
    _add(out, "床头板", main, L, H, 1)
    _add(out, "床围-左右", main, D, 25, 2)
    _add(out, "床围-前后", main, L, 25, 2)
    _add(out, "龙骨", main, D, 8, 3)
    _add(out, "铺板条", drawer, L, 10, 13)
    return out


def generate_boards(
    category: str,
    length_cm: float,
    *,
    depth_cm: Optional[float] = None,
    height_cm: Optional[float] = None,
    cols: Optional[int] = None,
    drawers: Optional[int] = None,
    doors: Optional[int] = None,
    shelves: Optional[int] = None,
    main_material: str = MAIN_MATERIAL,
    back_material: str = BACK_MATERIAL,
    drawer_material: str = DRAWER_MATERIAL,
) -> list[dict]:
    """品类 + 外形尺寸 → 板单。未给的深/高/数量用品类画像默认。"""
    prof = CATEGORY_PROFILE.get(_leaf(category)) or CATEGORY_PROFILE.get(category) or CATEGORY_PROFILE["餐边柜"]
    L = float(length_cm)
    D = float(depth_cm) if depth_cm else prof.get("D", 40)
    H = float(height_cm) if height_cm else prof.get("H", 80)
    fam = prof.get("family", "cabinet")
    if fam == "table":
        return _table(L, D, H, main_material)
    if fam == "bed":
        return _bed(L, D, H, main_material, drawer_material)
    return _cabinet(
        L, D, H,
        cols if cols is not None else prof.get("cols", 1),
        drawers if drawers is not None else prof.get("drawers", 0),
        doors if doors is not None else prof.get("doors", 0),
        shelves if shelves is not None else prof.get("shelves", 0),
        main_material, back_material, drawer_material,
    )


# 品牌精简系数: 通用模板按"满配"估材料, 畔色实际设计偏精简(板薄/部件少),
# 经 5 单真实工厂价校准 ≈0.70(平均偏高比 1.42 的倒数)。可调; 收更多单后回归。
TEMPLATE_MATERIAL_FACTOR = 0.62


def quote_from_template(
    db,
    category: str,
    length_cm: float,
    *,
    material_factor: float = TEMPLATE_MATERIAL_FACTOR,
    **kwargs,
) -> dict:
    """品类 + 外形 → 自动板单 → quote-heavy 引擎报价(含自动推五金)。

    返回报价 + 生成的板单(展示用真实尺寸)。计价时木作板面积 × material_factor 做品牌精简校准,
    五金/配件不缩。前端可改板单后走 /v2/quote-heavy 出未缩版精确价。
    """
    from app.services.custom_quote_v2_service import quote_heavy
    boards = generate_boards(category, length_cm, **kwargs)   # 真实板单(展示)
    priced = []
    for b in boards:
        nb = dict(b)
        if not b.get("is_accessory"):
            nb["width_cm"] = round(float(b["width_cm"]) * material_factor, 2)   # 面积×系数
        priced.append(nb)
    r = quote_heavy(
        db,
        product_type=_leaf(category),
        length_m=length_cm / 100,
        boards=priced,
        overall_width_m=(kwargs.get("depth_cm") or 0) / 100 or None,
        overall_height_m=(kwargs.get("height_cm") or 0) / 100 or None,
    )
    r["generated_boards"] = boards
    r["material_factor"] = material_factor
    return r
