"""定价 22 项配件成本 ↔ BOM 漂移检测 (Plan L7)。

口径: BOM 外配件汇总 = Σ(BomLine.qty_per_product × Material.price), 只算 AC- 前缀配件;
对照定价表 PricingSku.external_parts_cost。超阈值 → 记异常 + 标 stale (只提示, 不自动改价)。

阈值: system_settings.pricing_bom_drift_tolerance (元, 默认 1.0)。
挂钩: api/bom.py (BOM 增改删) / api/materials.py (改价) / 手动 POST /api/pricing-skus/bom-sync-check。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.material import Material
from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts

_logger = logging.getLogger("panse.pricing_bom_sync")

_DEFAULT_TOLERANCE = Decimal("1.0")
_ACCESSORY_PREFIX = "AC-"


def _tolerance(db: Session) -> Decimal:
    try:
        from app.services import settings_service
        raw = settings_service.get(db, "pricing_bom_drift_tolerance", env_fallback=False)
        return Decimal(str(raw)) if raw else _DEFAULT_TOLERANCE
    except Exception:  # pragma: no cover
        return _DEFAULT_TOLERANCE


def _bom_parts_cost(db: Session, sku_code: str) -> Optional[Decimal]:
    """BOM 外配件成本 = Σ qty × Material.price (AC- 前缀)。无 BOM/无价返回 None。"""
    rows = db.execute(
        select(BomLine.qty_per_product, Material.price)
        .join(Material, BomLine.material_code == Material.code)
        .where(
            BomLine.sku_code == sku_code,
            Material.code.like(f"{_ACCESSORY_PREFIX}%"),
        )
    ).all()
    if not rows:
        return None
    total = Decimal("0")
    priced = 0
    for qty, price in rows:
        if price is None:
            continue
        priced += 1
        total += Decimal(qty or 0) * Decimal(price)
    if priced == 0:
        return None   # 配件都没价 → 没法对照
    return total.quantize(Decimal("0.01"))


def _get_or_create_costs(db: Session, sku_code: str) -> PricingSkuCosts:
    row = db.execute(
        select(PricingSkuCosts).where(PricingSkuCosts.sku_code == sku_code)
    ).scalar_one_or_none()
    if row is None:
        row = PricingSkuCosts(sku_code=sku_code)
        db.add(row)
        db.flush()
    return row


def check_sku(db: Session, sku_code: str) -> dict:
    """对单个 SKU 做漂移检查。返回 {sku_code, drift, bom_cost, pricing_cost, stale}。"""
    sku = db.execute(
        select(PricingSku).where(PricingSku.sku_code == sku_code)
    ).scalar_one_or_none()
    if sku is None:
        return {"sku_code": sku_code, "stale": False, "skipped": "无定价行"}
    bom_cost = _bom_parts_cost(db, sku_code)
    if bom_cost is None:
        return {"sku_code": sku_code, "stale": False, "skipped": "无可对照 BOM 配件价"}
    pricing_cost = Decimal(sku.external_parts_cost or 0)
    drift = (bom_cost - pricing_cost).quantize(Decimal("0.01"))
    costs = _get_or_create_costs(db, sku_code)
    if abs(drift) > _tolerance(db):
        reason = f"BOM配件价 {bom_cost} vs 定价外配件 {pricing_cost} (差 {drift})"
        if costs.stale_reason != reason:
            costs.stale_reason = reason
            try:
                from app.services import exception_service
                exception_service.record(
                    db, source_table="pricing_skus", source_pk=sku_code,
                    exception_type="pricing_bom_drift", severity="warning",
                    description=f"{sku_code} 定价配件成本与 BOM 漂移: {reason}。"
                                f"请核对后在定价页重算或手工修正 (系统不自动改价)。",
                    suggestion_action="recompute_pricing_costs",
                )
            except Exception:  # pragma: no cover
                _logger.warning("pricing_bom_drift 异常写入失败 %s", sku_code, exc_info=True)
        db.flush()
        return {"sku_code": sku_code, "stale": True, "drift": float(drift),
                "bom_cost": float(bom_cost), "pricing_cost": float(pricing_cost)}
    # 在容差内 → 清 stale, 盖同步时间戳
    costs.stale_reason = None
    costs.bom_synced_at = datetime.now(timezone.utc)
    db.flush()
    return {"sku_code": sku_code, "stale": False, "drift": float(drift)}


def check_all(db: Session, *, limit: int = 2000) -> dict:
    """全量漂移检查 (BOM 里出现过的 SKU × 定价表交集)。"""
    sku_codes = [
        s for s in db.execute(
            select(BomLine.sku_code).distinct().limit(limit)
        ).scalars().all() if s
    ]
    stale = []
    checked = 0
    for code in sku_codes:
        r = check_sku(db, code)
        if r.get("skipped"):
            continue
        checked += 1
        if r["stale"]:
            stale.append(r)
    return {"checked": checked, "stale_count": len(stale), "stale": stale[:100]}


def check_for_material(db: Session, material_code: str) -> dict:
    """物料改价后: 只查用到该物料的 SKU (BOM 反查)。"""
    sku_codes = [
        s for s in db.execute(
            select(BomLine.sku_code).where(BomLine.material_code == material_code).distinct()
        ).scalars().all() if s
    ]
    results = [check_sku(db, c) for c in sku_codes]
    stale = [r for r in results if r.get("stale")]
    return {"material_code": material_code, "checked": len(results),
            "stale_count": len(stale), "stale": stale}
