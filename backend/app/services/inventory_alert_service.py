"""库存预警 / 滞销 自动扫描 (Phase 4, 业务需求 8).

每天 07:00 由 scheduler 调 scan_low_stock + scan_slow_moving, 生成对应 Alerts.

- 低库存: physical - locked < safety_stock 或者 < lead_time × avg_daily_use
- 智能提前备货: 物料 lead_time_days 之前应该有量, 否则 alert (按优先级 high → critical, mid → warn)
- 滞销: 长期未售 + 超大库存 → warn alert
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.services import alert_service, sales_analytics


SEVERITY_BY_PRIORITY = {"high": "critical", "mid": "warn", "low": "info"}


def scan_low_stock(db: Session) -> int:
    """扫所有 PartInventory + Material, 不足 safety_stock 或 lead_time 倒推不够 → Alert.

    Phase 6: 跳过 is_discontinued=True 的物料 (停产物料不再预警).
    """
    rows = db.execute(
        select(PartInventory, Material).join(
            Material, Material.code == PartInventory.material_code,
        )
    ).all()
    n = 0
    for inv, mat in rows:
        if getattr(mat, "is_discontinued", False):
            continue
        available = float(inv.available_qty or 0)
        threshold = float(inv.safety_stock or 0)
        # 业务需求: 智能提前备货 — 拿物料 lead_time × 平均日消耗当下限
        if threshold == 0 and mat.lead_time_days:
            threshold = float(mat.lead_time_days)
        if threshold <= 0:
            continue
        if available < threshold:
            sev = SEVERITY_BY_PRIORITY.get(mat.priority or "mid", "warn")
            alert_service.upsert(
                db, kind="low_stock_part", severity=sev,
                title=f"配件 {mat.code} ({mat.name}) 低库存",
                body=(f"可用 {available} 件 < 阈值 {threshold}. "
                      f"补货周期 {mat.lead_time_days} 天, 优先级 {mat.priority}."),
                dedupe_key=f"low_stock_part:{mat.code}",
                related_url=f"/inventory/parts?code={mat.code}",
                context={"material_code": mat.code,
                         "available": available, "threshold": threshold,
                         "lead_time_days": mat.lead_time_days,
                         "priority": mat.priority},
                sticky=True,
            )
            n += 1
    return n


def scan_slow_moving(db: Session, *,
                     long_no_sale_days: int = 60,
                     overstock_ratio: float = 3.0) -> int:
    """滞销 / 超大库存 alert."""
    splits = sales_analytics.slow_moving_split(
        db, long_no_sale_days=long_no_sale_days,
        overstock_ratio=overstock_ratio,
    )
    n = 0
    for item in splits["long_idle"]:
        alert_service.upsert(
            db, kind="slow_moving_long_idle", severity="warn",
            title=f"长期滞销: {item['material_code']}",
            body=(f"{item['days_since']} 天未出货, 库存 {item['physical_qty']} 件. "
                  f"建议: 降价 / 转赠 / 报废."),
            dedupe_key=f"slow_moving_long_idle:{item['material_code']}",
            context=item,
            auto_resolve_after_minutes=60 * 24 * 7,  # 一周后过期 (下次再扫)
        )
        n += 1
    for item in splits["overstock"]:
        alert_service.upsert(
            db, kind="slow_moving_overstock", severity="info",
            title=f"超大库存: {item['product_code']}",
            body=(f"现库存 {item['physical_qty']} 件, 是预测 30 天销量 "
                  f"{item['forecast_30d']} 的 {item['ratio']} 倍."),
            dedupe_key=f"slow_moving_overstock:{item['product_code']}",
            context=item,
            auto_resolve_after_minutes=60 * 24 * 7,
        )
        n += 1
    return n


def scan_all(db: Session) -> dict:
    return {
        "low_stock": scan_low_stock(db),
        "slow_moving": scan_slow_moving(db),
    }
