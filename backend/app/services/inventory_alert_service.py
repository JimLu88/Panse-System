"""库存预警 / 滞销 自动扫描 (Phase 4, 业务需求 8).

每天 07:00 由 scheduler 调 scan_low_stock + scan_slow_moving, 生成对应 Alerts.

- 低库存: physical - locked < safety_stock 或者 < lead_time × avg_daily_use
- 智能提前备货: 物料 lead_time_days 之前应该有量, 否则 alert (按优先级 high → critical, mid → warn)
- 滞销: 长期未售 + 超大库存 → warn alert
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from math import sqrt
from statistics import mean, stdev
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.order import Order
from app.services import alert_service, sales_analytics


SEVERITY_BY_PRIORITY = {"high": "critical", "mid": "warn", "low": "info"}

# 服务水平 95% 对应的 Z 值
_Z_95 = 1.65


def compute_dynamic_safety_stock(
    db: Session, material_code: str, *, lead_time_days: int = 0,
) -> float:
    """Phase 9 Tier 2 #3: 动态安全库存 = 平均日消耗 × lead_time + Z × σ × √lead_time.

    用过去 60 天该物料的实际日消耗 (从 lock_ledger 的 consume 行算).
    Z=1.65 对应 95% 服务水平.

    返回 float (件数). lead_time=0 时退化为 0.
    """
    if lead_time_days <= 0:
        return 0.0
    cutoff = date.today() - timedelta(days=60)
    from app.models.inventory_lock import InventoryLockLedger
    rows = db.execute(
        select(InventoryLockLedger).where(
            InventoryLockLedger.material_code == material_code,
            InventoryLockLedger.kind == "consume",
            InventoryLockLedger.created_at >= cutoff,
        )
    ).scalars().all()
    if not rows:
        return float(lead_time_days)   # 没历史 → 保守取 lead_time 件
    # 按天聚合
    by_day: dict[date, float] = {}
    for r in rows:
        d = r.created_at.date()
        by_day[d] = by_day.get(d, 0.0) + float(r.qty)
    # 补齐 60 天的 0 (没有出货的天, 包括今天)
    days = []
    for i in range(60):
        d = date.today() - timedelta(days=i)
        days.append(by_day.get(d, 0.0))
    mu = mean(days)
    sigma = stdev(days) if len(days) > 1 else 0.0
    return mu * lead_time_days + _Z_95 * sigma * sqrt(lead_time_days)


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
        # Phase 9 Tier 2 #3: 动态安全库存 (基于历史消耗 + σ + 服务水平 95%)
        if threshold == 0 and mat.lead_time_days:
            threshold = compute_dynamic_safety_stock(
                db, mat.code, lead_time_days=mat.lead_time_days,
            )
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
