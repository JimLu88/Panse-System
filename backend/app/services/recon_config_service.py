"""对账 / 利润 口径配置 — 容差 + 补贴税率 + 软件服务费率, 全局默认 + 按店铺/渠道覆盖。

存在单个 SystemSetting JSON 键 recon_config:
  {"defaults": {tolerance_pct, tolerance_floor, subsidy_tax_rate, software_fee_rate},
   "by_shop": {"畔色店": {subsidy_tax_rate: 0.025, ...}, ...}}
取值: 某店铺有覆盖用覆盖, 否则用全局默认。未来不同渠道税费不同时, 在设置里按店铺填即可。
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.services import settings_service

SETTING_KEY = "recon_config"

DEFAULTS: dict[str, float] = {
    "tolerance_pct": 0.005,       # 对账容差·百分比
    "tolerance_floor": 5.0,       # 对账容差·最小金额(元)
    "subsidy_tax_rate": 0.02,     # 淘宝补贴税率
    "software_fee_rate": 0.006,   # 软件服务费率
}
RATE_KEYS = tuple(DEFAULTS.keys())


def get_config(db: Session) -> dict:
    """返回 {'defaults': {...}, 'by_shop': {...}}, 缺省项用 DEFAULTS 补齐。"""
    raw = settings_service.get(db, SETTING_KEY, env_fallback=False)
    cfg: dict = {}
    if raw:
        try:
            cfg = json.loads(raw)
        except (ValueError, TypeError):
            cfg = {}
    defaults = {**DEFAULTS, **(cfg.get("defaults") or {})}
    by_shop = cfg.get("by_shop") or {}
    return {"defaults": defaults, "by_shop": by_shop}


def set_config(db: Session, *, defaults: Optional[dict] = None, by_shop: Optional[dict] = None) -> dict:
    """合并保存配置 (传入的覆盖已有)。返回保存后的完整配置。"""
    cur = get_config(db)
    if defaults:
        cur["defaults"] = {**cur["defaults"], **{k: float(v) for k, v in defaults.items() if k in DEFAULTS}}
    if by_shop is not None:
        # by_shop 整体替换 (前端传全量), 仅保留合法 rate key
        cleaned = {}
        for shop, rates in (by_shop or {}).items():
            cleaned[shop] = {k: float(v) for k, v in (rates or {}).items() if k in DEFAULTS}
        cur["by_shop"] = cleaned
    settings_service.set_value(db, SETTING_KEY, json.dumps(cur, ensure_ascii=False),
                               description="对账/利润 口径配置(容差+税费率, 全局+按店铺)")
    return cur


def rate(cfg: dict, key: str, shop: Optional[str] = None) -> Decimal:
    """取某项费率/容差: 店铺有覆盖用覆盖, 否则全局默认。cfg 来自 get_config(db)。"""
    by_shop = cfg.get("by_shop") or {}
    if shop and shop in by_shop and key in by_shop[shop]:
        return Decimal(str(by_shop[shop][key]))
    return Decimal(str(cfg.get("defaults", {}).get(key, DEFAULTS.get(key, 0))))
