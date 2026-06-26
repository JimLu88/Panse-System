# -*- coding: utf-8 -*-
"""配件分类 — 批量给物料归类 + 通用消耗配件(双面胶/螺丝)入全产品 BOM (用户 2026-06-26, 方向1)。

189 个 AC 配件原本全平铺无分类。auto_categorize 按名字关键词把它们归到用户定的分类
(五金/玻璃/岩板/洞石饰面板/电力轨道/铝合金槽/杂项/床铺板/软包…), 非 AC 按 code 前缀归
(木作/人工/木材/特殊件)。规则不可能 100% 准 → 默认 dry-run 出预览给人工核, 配件库页可逐个改。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material

# 非 AC 物料按 code 前缀归类(外采对账只看 AC, 这几类是工厂自备, 归类只为配件库整洁)
PREFIX_CATEGORY = {"WD": "木作", "MP": "人工", "MW": "木材", "SP": "特殊件"}

# AC 配件按名字关键词归类 —— **顺序敏感, 先匹配先归**(用户 2026-06-26 给的分类)。
# 排序要点: 岩板先于洞石饰面板(9mm洞石岩板归岩板); 电力轨道先于五金(别被"轨道"抢走);
#           五金先于玻璃(玻璃床头柜橡胶垫归五金不归玻璃)。
AC_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("岩板", ["岩板"]),
    ("洞石饰面板", ["洞石", "饰面板", "纹理饰面"]),
    ("电力轨道", ["电力轨道", "xpower", "power轨", "明装电力"]),
    ("铝合金槽", ["铝合金槽", "铝槽"]),
    ("五金", [
        "灯带", "变压器", "灯开关", "灯堵头", "充电灯", "无线充电", "夜灯", "小夜灯", "射灯",
        "反弹抽屉", "抽屉轨道", "托底抽屉", "翻盖支撑", "支撑杆", "气撑", "液压杆", "支撑",
        "床板拉绳", "拉绳", "结构胶", "橡胶垫", "胶垫", "磁力", "磁条", "磁吸",
        "金属腿", "金属条", "金属脚", "金属杆", "金属侧板", "侧板", "铰链", "把手", "拉手", "拉杆",
        "挂杆", "挂钩", "挂件", "不锈钢", "升降", "电机", "桌架", "桌轨", "滑轨", "导轨", "轨道",
        "插座", "嵌入插", "五金", "锁", "脚钉", "封边", "气压",
        "螺栓", "螺母",
    ]),
    ("玻璃", ["玻璃", "镜"]),
    ("杂项", [
        "洞洞板", "亚克力", "AA柱", "隔板", "套杆", "预埋", "静音棉", "3m胶", "3M胶",
        "置物架", "连接片", "堵头", "软木板", "插排", "脚垫",
        "双面胶", "螺丝",   # 用户 2026-06-27: 双面胶/螺丝 改归杂项(原在五金)
    ]),
    ("床铺板", ["铺板"]),
    ("软包", ["软包", "编织"]),
]


def _ac_category(name: str) -> Optional[str]:
    n = (name or "").lower()
    for cat, kws in AC_CATEGORY_RULES:
        if any(k.lower() in n for k in kws):
            return cat
    return None


def category_for(material: Material) -> Optional[str]:
    pfx = (material.code or "").split("-", 1)[0].upper()
    if pfx == "AC":
        return _ac_category(material.name)
    return PREFIX_CATEGORY.get(pfx)


def auto_categorize(db: Session, *, apply: bool = False, only_empty: bool = True) -> dict:
    """按规则给物料归类。only_empty=True 只补未分类的(不覆盖人工已设)。apply=False 只出预览。

    返回 {applied, changed, total, by_category:{cat:{count, sample[]}}, uncategorized_sample[]}。
    """
    mats = db.execute(select(Material)).scalars().all()
    by_cat: dict[str, list[str]] = defaultdict(list)
    uncat: list[str] = []
    changed = 0
    for m in mats:
        if only_empty and m.category:
            by_cat[m.category].append(m.name)   # 已有分类: 计入展示, 不改
            continue
        cat = category_for(m)
        if cat:
            if apply and m.category != cat:
                m.category = cat
                changed += 1
            by_cat[cat].append(m.name)
        else:
            uncat.append(f"{m.code} {m.name}")
    if apply:
        db.commit()
    return {
        "applied": apply,
        "changed": changed,
        "total": len(mats),
        "uncategorized": len(uncat),
        "uncategorized_sample": uncat[:30],
        "by_category": {
            k: {"count": len(v), "sample": v[:10]}
            for k, v in sorted(by_cat.items(), key=lambda x: -len(x[1]))
        },
    }


# 通用消耗配件: 几乎每个产品都用, 但 BOM 里没单列 → 建物料 + 加进每个产品每个 SKU 的 BOM。
DEFAULT_CONSUMABLES = [
    {"name": "双面胶", "unit": "个", "price": Decimal("0.1"), "category": "杂项"},
    {"name": "螺丝", "unit": "个", "price": Decimal("0.1"), "category": "杂项"},
]


def ensure_consumables_in_boms(db: Session, *, items: Optional[list[dict]] = None,
                               apply: bool = False) -> dict:
    """给每个产品的每个 SKU 的 BOM 加上通用消耗配件(双面胶/螺丝), 缺料就建 AC 物料。

    BOM 按 (product_code, sku_code) 粒度匹配订单 → 必须按每个已存在的 (product_code, sku_code)
    各加一行, 否则带 sku_code 的订单会漏。已有该料的行跳过(幂等)。apply=False 只预览。
    """
    from app.models.bom import BomLine
    from app.services import material_coder

    items = items or DEFAULT_CONSUMABLES
    # 1) 确保物料存在(按名字找, 没有就建 AC 码)
    mats: dict[str, Material] = {}
    created_mat = []
    for it in items:
        m = db.execute(select(Material).where(Material.name == it["name"])).scalar_one_or_none()
        if m is None:
            code = material_coder.next_code(db, "AC") if apply else f"AC-(new:{it['name']})"
            if apply:
                m = Material(code=code, name=it["name"], unit=it.get("unit"),
                             price=it.get("price"), category=it.get("category"), is_custom=False)
                db.add(m)
                db.flush()
            created_mat.append({"name": it["name"], "code": code})
        else:
            if apply and it.get("category") and not m.category:
                m.category = it["category"]
        mats[it["name"]] = m

    # 2) 取所有已存在的 (product_code, sku_code, product_name) BOM 锚点
    anchors = db.execute(
        select(BomLine.product_code, BomLine.sku_code, BomLine.product_name).distinct()
    ).all()
    # 已有(product_code, sku_code, material_code) → 幂等跳过
    existing = set(db.execute(
        select(BomLine.product_code, BomLine.sku_code, BomLine.material_code)
    ).all())

    added = 0
    for it in items:
        m = mats[it["name"]]
        for pc, sc, pname in anchors:
            if not apply:
                added += 1
                continue
            if (pc, sc, m.code) in existing:
                continue
            db.add(BomLine(
                product_code=pc, sku_code=sc, product_name=pname,
                material_code=m.code, material_name=m.name,
                qty_per_product=Decimal("1"), unit=it.get("unit"),
            ))
            existing.add((pc, sc, m.code))
            added += 1
    if apply:
        db.commit()
    return {
        "applied": apply,
        "materials_created": created_mat,
        "bom_anchors": len(anchors),
        "bom_lines_added": added,
        "consumables": [it["name"] for it in items],
    }
