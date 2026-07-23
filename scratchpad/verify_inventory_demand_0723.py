from datetime import date, timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models.order import Order
from app.services import (
    inventory_demand_service as demand,
    inventory_monthly_report_service as monthly,
    product_inventory_service,
    settings_service,
)


db = SessionLocal()
try:
    cfg = product_inventory_service.get_forecast_config(db)
    rows = demand.load_observations(
        db,
        start=date.today() - timedelta(days=90),
        end=date.today(),
        cfg=cfg,
    )
    large = sorted(
        [r for r in rows if r.raw_qty > 5],
        key=lambda x: x.raw_qty,
        reverse=True,
    )
    sync = demand.sync_quantity_anomalies(db, cfg=cfg)
    plan = monthly.build_monthly_plan(
        db, year=2026, month=8, as_of=date.today()
    )
    db.commit()
    print({
        "mode": cfg["mode"],
        "windows": [7, 15, 30, 60, 90],
        "large_orders": [
            {
                "order_no": r.order_no,
                "raw_qty": r.raw_qty,
                "effective_qty": r.effective_qty,
                "kind": r.kind,
                "anomaly": r.anomaly,
            }
            for r in large[:10]
        ],
        "anomaly_sync": sync,
        "august": {
            "products": len(plan["products"]),
            "hot_products": plan["hot_product_count"],
            "suggested_total": plan["suggested_total"],
            "custom_task_forecast": plan["custom_task_forecast"],
            "top": [
                {
                    "product_code": x["product_code"],
                    "name": x["product_name"],
                    "suggested": x["suggested_restock"],
                    "target": x["target_stock"],
                    "on_hand": x["on_hand"],
                    "free_in_production": x["free_in_production"],
                    "policy": x["policy"],
                }
                for x in plan["products"][:10]
            ],
        },
        "feishu_configured": bool(settings_service.get(
            db, "feishu_push_chat_id", env_fallback=False
        )),
    })
finally:
    db.close()
