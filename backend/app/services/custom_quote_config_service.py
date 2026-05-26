"""全定制报价参数配置 (后台可调).

集中存放报价引擎用到的所有可调参数, 存在 system_settings 的一个 JSON 里:
  - 利润系数 (工厂 / 畔色)
  - 投影面积对照 (类型 + 系数)
  - 木材/配件单价表 (元 per 计价单位)
  - 人工费表 (品类 × 小/中/大)
  - 打包费 (小/中/大)
  - 大小判定规则 (品类 → [大阈值, 中阈值], 按长度 m)

默认值来自用户「全定制算价 v0.5」+ 人工费物料表。前端设置界面读写本配置。
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.services import settings_service

_KEY = "custom_quote_config"

# 用户人工费表 [小, 中, 大]
_LABOR = {
    "床": [600, 700, 780], "床头柜": [300, 350, 436], "梳妆台": [600, 700, 800],
    "斗柜": [600, 800, 1000], "镜子": [400, 500, 600], "电视柜": [600, 800, 1000],
    "茶几": [400, 500, 600], "组合柜": [300, 436, 500], "小推车": [400, 500, 600],
    "餐边柜": [840, 1070, 1480], "餐桌": [300, 400, 500], "餐凳": [300, 350, 400],
    "岛台": [600, 800, 1000], "书柜": [800, 1000, 1200], "书桌": [700, 800, 900],
    "升降桌": [600, 700, 800], "鞋柜": [900, 1070, 1200], "衣帽架": [300, 300, 300],
}

# 大小判定: 品类 → [大阈值, 中阈值] (长度 m); 长≥大→大, ≥中→中, else 小
_SIZE_RULES = {
    "床": [1.6, 1.35], "床头柜": [0.5, 0.35], "梳妆台": [1.2, 0.8], "斗柜": [1.8, 1.4],
    "镜子": [0.8, 0.6], "电视柜": [2.0, 1.4], "茶几": [1.4, 1.0], "组合柜": [0.8, 0.6],
    "小推车": [0.8, 0.6], "餐边柜": [2.1, 1.4], "餐桌": [2.0, 1.4], "餐凳": [1.6, 1.3],
    "岛台": [1.4, 0.9], "书柜": [1.2, 0.9], "书桌": [1.4, 1.0], "升降桌": [1.8, 1.4],
    "鞋柜": [1.2, 0.9], "衣帽架": [0, 0],
}

# 木材/配件单价 (元 per ㎡/米/付/个/组), 来自全定制算价表
_PRICES = {
    "黑胡桃木-2.2cm": 500, "白橡木-2.2cm": 300, "樱桃木-2.2cm": 300, "白蜡木-2.2cm": 300,
    "红橡木-2.2cm": 240, "榉木-2.2cm": 280, "实木多层板1.8cm": 220, "樱桃木-2.8cm": 520,
    "12mm普通岩板": 200, "12mm洞石岩板": 400, "9mm洞石下挂岩板": 660, "洞石背板": 95,
    "超白玻璃": 250, "钢化玻璃": 250, "抽屉轨道": 22, "电力轨道": 368, "五金件": 100,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "factory_profit_rate": 0.25,    # 工厂利润系数
    "panse_profit_rate": 0.15,      # 畔色利润系数 (售价毛利, 除法)
    "safety_rate": 1.05,            # 保守上浮系数 (宁高不低, 防低估亏本)
    "projection_type": "front",     # front=正面投影(宽×高) / top=俯视(宽×深)
    "projection_rate": 900,         # 投影面积对照系数 元/㎡
    "packing": [100, 200, 400],     # 打包费 [小,中,大]
    "freight": [100, 100, 150],     # 运费 [小,中,大]
    "install": [50, 100, 150],      # 上门安装费 [小,中,大]
    "labor": _LABOR,
    "size_rules": _SIZE_RULES,
    "prices": _PRICES,
}


def get_config(db: Session) -> dict:
    """读配置; 缺失的键用默认值补全 (向后兼容新增参数)。"""
    raw = settings_service.get(db, _KEY, env_fallback=False)
    cfg = dict(DEFAULT_CONFIG)
    if raw:
        try:
            stored = json.loads(raw)
            if isinstance(stored, dict):
                cfg.update(stored)
        except (ValueError, TypeError):
            pass
    return cfg


def save_config(db: Session, patch: dict) -> dict:
    """合并写入 (只覆盖传入的键)，返回完整配置。"""
    cfg = get_config(db)
    cfg.update({k: v for k, v in patch.items() if k in DEFAULT_CONFIG})
    settings_service.set_value(db, _KEY, json.dumps(cfg, ensure_ascii=False),
                               description="全定制报价参数")
    return cfg


def classify_size(cfg: dict, product_type: str, length_m: float) -> str:
    """按长度判定 小/中/大。"""
    rule = cfg.get("size_rules", {}).get(product_type)
    if not rule:
        return "中"
    big, mid = rule
    if length_m >= big:
        return "大"
    if length_m >= mid:
        return "中"
    return "小"


_SIZE_IDX = {"小": 0, "中": 1, "大": 2}


def _material_price(db, name: str) -> float | None:
    """查物料表单价 (配件价格表 = 唯一数据源, 与导入/飞书联动)。

    先精确名匹配, 再"包含"模糊匹配; 查不到返回 None 让上层回落配置默认。
    """
    if db is None or not name:
        return None
    from sqlalchemy import select
    from app.models.material import Material
    row = db.execute(select(Material.price).where(Material.name == name)).scalar_one_or_none()
    if row is None:
        hit = db.execute(
            select(Material.price).where(Material.name.like(f"%{name}%"), Material.price.isnot(None))
        ).first()
        row = hit[0] if hit else None
    return float(row) if row is not None else None


def lookup_price(cfg: dict, material: str, db=None) -> float:
    """材料单价: 物料表优先, 配置兜底。"""
    p = _material_price(db, material)
    if p is not None:
        return p
    prices = cfg.get("prices", {})
    return float(prices.get(material, prices.get(material.split("-")[0], 0)))


def lookup_labor(cfg: dict, product_type: str, length_m: float, db=None) -> float:
    """人工费 (品类 × 大小): 物料表「{品类}-人工费-{小/中/大}型」优先, 配置兜底。"""
    size = classify_size(cfg, product_type, length_m)
    p = _material_price(db, f"{product_type}-人工费-{size}型")
    if p is not None:
        return p
    row = cfg.get("labor", {}).get(product_type)
    return float(row[_SIZE_IDX[size]]) if row else 0.0


def _by_size(cfg: dict, key: str, default: list, product_type: str, length_m: float,
             mat_name_fmt: str, db=None) -> float:
    """按大小取费用: 物料表优先 (名如「打包费用-中型家具」), 配置兜底。"""
    size = classify_size(cfg, product_type, length_m)
    p = _material_price(db, mat_name_fmt.format(size=size))
    if p is not None:
        return p
    arr = cfg.get(key, default)
    return float(arr[_SIZE_IDX[size]])


def lookup_packing(cfg: dict, product_type: str, length_m: float, db=None) -> float:
    return _by_size(cfg, "packing", [100, 200, 400], product_type, length_m, "打包费用-{size}型家具", db)


def lookup_freight(cfg: dict, product_type: str, length_m: float, db=None) -> float:
    return _by_size(cfg, "freight", [100, 100, 150], product_type, length_m, "运费-{size}型家具", db)


def lookup_install(cfg: dict, product_type: str, length_m: float, db=None) -> float:
    return _by_size(cfg, "install", [50, 100, 150], product_type, length_m, "上门安装费-{size}型家具", db)
