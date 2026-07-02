from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.models.inventory import ProductInventory, SemiFinishedInventory
from app.models.product import Product
from app.schemas.product_inventory import (
    ProductInventoryCreate,
    ProductInventoryOut,
    ProductInventoryWithStats,
)
from app.services import exception_service, product_inventory_service


class ProductInventoryPatch(BaseModel):
    qty: Optional[Decimal] = None
    locked_qty: Optional[Decimal] = None
    safety_stock: Optional[Decimal] = None
    lead_time_days: Optional[int] = None
    slow_moving_days: Optional[int] = None
    reorder_point: Optional[Decimal] = None
    remark: Optional[str] = None


class SemiInventoryPatch(BaseModel):   # R5 半成品/白坯 库存维护
    on_hand_qty: Optional[Decimal] = None
    in_production_qty: Optional[Decimal] = None
    name: Optional[str] = None
    remark: Optional[str] = None

router = APIRouter(prefix="/api/inventory/products", tags=["inventory"])


@router.get("/forecast-config")
def get_forecast_config_api(db: Session = Depends(get_db)):
    """日均销量公式 + 大促时段配置 + 当前大促备货状态。"""
    from app.services import product_inventory_service as svc
    return {**svc.get_forecast_config(db), "promo": svc.promo_status(db)}


@router.put("/forecast-config")
def put_forecast_config_api(
    payload: dict,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """保存公式/大促配置 (留痕修改档案), 下次刷新统计即按新公式。"""
    from app.services import field_change_service
    from app.services import product_inventory_service as svc
    old = svc.get_forecast_config(db)
    cfg = svc.save_forecast_config(db, payload or {})
    for k in ("mode", "halflife_days", "window_days", "promo_periods"):
        field_change_service.record(
            db, table="system_settings", pk=f"daily_sales_{k}", field=k,
            old=str(old.get(k)), new=str(cfg.get(k)),
            actor=getattr(_, "username", None), row_label="销量公式/大促配置",
            field_label={"mode": "公式模式", "halflife_days": "半衰期(天)",
                         "window_days": "窗口(天)", "promo_periods": "大促时段"}.get(k),
        )
    db.commit()
    return {**cfg, "promo": svc.promo_status(db)}


@router.get("", response_model=list[ProductInventoryWithStats])
def list_product_inventory(
    warehouse: Optional[str] = None,
    product_code: Optional[str] = None,
    warning_only: bool = Query(False, description="只显示需要关注的库存 (warning/danger/critical/excess)"),
    include_all: bool = Query(False, description="含还没建库存行的产品(虚拟行, has_inventory=False, 前端折叠)"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(ProductInventory)
    if warehouse:
        stmt = stmt.where(ProductInventory.warehouse == warehouse)
    if product_code:
        stmt = stmt.where(ProductInventory.product_code == product_code)
    stmt = stmt.order_by(ProductInventory.product_code, ProductInventory.sku).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()

    result = []
    covered: set[str] = set()
    # 一次性算 ABC 分层 + 配置(方向4), 每行复用; 避免逐行重算全表排名
    _cfg = product_inventory_service.get_forecast_config(db)
    _abc = product_inventory_service.compute_abc_map(db, _cfg)
    _inprod = product_inventory_service.compute_in_production_split(db)  # R1 在产拆自由/客户单, 一次算复用
    for inv in rows:
        covered.add(inv.product_code)
        stats = product_inventory_service.compute_product_stats(
            db, inv, abc_map=_abc, cfg=_cfg, in_production_split=_inprod)
        # 需关注筛选: 正常(ok) 和 按需生产(mto, 定制/长尾) 都不算"需关注"
        if warning_only and stats["warning_status"] in ("ok", "mto"):
            continue
        row_dict = {
            "id": inv.id,
            "warehouse": inv.warehouse,
            "product_code": inv.product_code,
            "sku": inv.sku,
            "product_name": getattr(inv, "product_name", None),
            "has_inventory": True,
            "spec": inv.spec,
            "unit": inv.unit,
            "physical_qty": inv.physical_qty,
            "locked_qty": inv.locked_qty,
            "safety_stock": inv.safety_stock,
            "lead_time_days": inv.lead_time_days,
            "slow_moving_days": inv.slow_moving_days,
            "reorder_point": inv.reorder_point,
            "remark": inv.remark,
            **stats,
        }
        result.append(ProductInventoryWithStats(**row_dict))

    # 含全部产品: 把还没建库存行的产品也带出来(虚拟行, 前端折叠到"无库存")
    if include_all and not warning_only and not (warehouse or product_code):
        from app.models.product import Product
        pstmt = select(Product)
        if covered:
            pstmt = pstmt.where(Product.code.notin_(covered))
        for p in db.execute(pstmt.order_by(Product.code)).scalars().all():
            result.append(ProductInventoryWithStats(
                id=None, warehouse="-", product_code=p.code, sku=None,
                product_name=getattr(p, "name", None), has_inventory=False,
                spec=None, unit=None, physical_qty=Decimal("0"), locked_qty=Decimal("0"),
                safety_stock=None, lead_time_days=None, slow_moving_days=None,
                reorder_point=None, remark=None,
                available_qty=0.0, daily_sales_30d=0.0, lead_time_days_computed=None,
                safety_stock_computed=0.0, reorder_point_computed=0.0, days_of_stock=None,
                warning_status="ok", auto_reorder_qty=0.0,
            ))
    return result


@router.post("/refresh", response_model=dict)
def refresh_inventory_stats(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """把从订单历史推算的提前期/安全库存/预警线批量回写到库存表（幂等）。"""
    n = product_inventory_service.refresh_all_inventory(db)
    db.commit()
    return {"updated": n, "message": f"已更新 {n} 条成品库存推算字段"}


@router.post("", response_model=ProductInventoryOut, status_code=201)
def add_product_inventory_row(payload: ProductInventoryCreate, db: Session = Depends(get_db)):
    product = db.execute(
        select(Product).where(Product.code == payload.product_code)
    ).scalar_one_or_none()
    if product is None:
        exception_service.record(
            db,
            source_table="product_inventory",
            source_pk=payload.product_code,
            exception_type="unknown_product_code",
            severity="error",
            description=(
                f"录入成品库存时引用了不存在的产品编码 {payload.product_code}。"
                f"请先到「产品总表」补登该产品，或检查编码是否拼错。"
            ),
            suggestion_action="create_or_correct_product",
            context={"warehouse": payload.warehouse, "sku": payload.sku},
        )

    inv = ProductInventory(
        warehouse=payload.warehouse,
        product_code=payload.product_code,
        sku=payload.sku,
        spec=payload.spec,
        unit=payload.unit,
        physical_qty=payload.physical_qty,
        locked_qty=payload.locked_qty,
        safety_stock=payload.safety_stock,
        lead_time_days=payload.lead_time_days,
        slow_moving_days=payload.slow_moving_days,
        reorder_point=payload.reorder_point,
        remark=payload.remark,
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


@router.patch("/{inventory_id}", response_model=ProductInventoryOut)
def update_product_inventory(
    inventory_id: int,
    payload: ProductInventoryPatch,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """盘库调整：修改成品库存数量及参数。(人工编辑 → 统一历史档案)"""
    inv = db.get(ProductInventory, inventory_id)
    if not inv:
        raise HTTPException(404, "inventory row not found")
    from app.services import field_change_service
    data = {
        "physical_qty": payload.qty, "locked_qty": payload.locked_qty,
        "safety_stock": payload.safety_stock, "lead_time_days": payload.lead_time_days,
        "slow_moving_days": payload.slow_moving_days, "reorder_point": payload.reorder_point,
        "remark": payload.remark,
    }
    data = {k: v for k, v in data.items() if v is not None}
    field_change_service.diff_and_apply(
        db, inv, data, table="product_inventory", pk=inv.id,
        actor=getattr(_, "username", None),
        row_label=f"{inv.sku or inv.product_code} @{inv.warehouse}",
        field_labels={"physical_qty": "现货数量", "locked_qty": "锁定数量",
                      "safety_stock": "安全库存", "lead_time_days": "提前期(天)",
                      "slow_moving_days": "滞销阈值(天)", "reorder_point": "预警线"},
    )
    db.commit()
    db.refresh(inv)
    return inv


@router.patch("/by-product/{product_code}", response_model=dict)
def update_product_inventory_params_by_product(
    product_code: str,
    payload: ProductInventoryPatch,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """同产品全部 SKU 一键同步参数 (安全库存/提前期/预警线/滞销阈值)。

    刻意不同步 qty/locked_qty/remark — 各 SKU 数量不同, 批量覆盖数量必然出错。
    """
    rows = db.execute(
        select(ProductInventory).where(ProductInventory.product_code == product_code)
    ).scalars().all()
    if not rows:
        raise HTTPException(404, f"产品 {product_code} 没有库存行")
    from app.services import field_change_service
    data = {
        "safety_stock": payload.safety_stock, "lead_time_days": payload.lead_time_days,
        "slow_moving_days": payload.slow_moving_days, "reorder_point": payload.reorder_point,
    }
    data = {k: v for k, v in data.items() if v is not None}
    updated = 0
    for inv in rows:
        field_change_service.diff_and_apply(
            db, inv, data, table="product_inventory", pk=inv.id,
            actor=getattr(_, "username", None),
            row_label=f"{inv.sku or inv.product_code} @{inv.warehouse} (批量同步)",
            field_labels={"safety_stock": "安全库存", "lead_time_days": "提前期(天)",
                          "slow_moving_days": "滞销阈值(天)", "reorder_point": "预警线"},
        )
        updated += 1
    db.commit()
    return {"product_code": product_code, "updated": updated,
            "message": f"已同步参数到 {updated} 个 SKU 库存行"}


@router.delete("/{inventory_id}", status_code=204)
def delete_product_inventory(inventory_id: int, db: Session = Depends(get_db)):
    inv = db.get(ProductInventory, inventory_id)
    if not inv:
        raise HTTPException(404, "inventory row not found")
    db.delete(inv)
    db.commit()


# ---------------- R5 半成品/白坯 库存 (功能开关打开后用) ---------------- #

@router.get("/semi-finished/list")
def list_semi_finished_inventory(db: Session = Depends(get_db)):
    """列出半成品/白坯库存 (现有/在产, 按 semi_group)。功能关时也可看, 通常为空。"""
    rows = db.execute(
        select(SemiFinishedInventory).order_by(SemiFinishedInventory.semi_group)
    ).scalars().all()
    return [{
        "id": r.id, "semi_group": r.semi_group, "name": r.name,
        "on_hand_qty": float(r.on_hand_qty or 0), "in_production_qty": float(r.in_production_qty or 0),
        "remark": r.remark,
    } for r in rows]


@router.put("/semi-finished/{semi_group}")
def upsert_semi_finished_inventory(
    semi_group: str,
    payload: SemiInventoryPatch,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """维护某白坯分组的 现有白坯 / 在产白坯 (R5)。没有则新建。"""
    row = db.execute(
        select(SemiFinishedInventory).where(SemiFinishedInventory.semi_group == semi_group)
    ).scalar_one_or_none()
    if row is None:
        row = SemiFinishedInventory(semi_group=semi_group, warehouse="default")
        db.add(row)
    if payload.on_hand_qty is not None:
        row.on_hand_qty = payload.on_hand_qty
    if payload.in_production_qty is not None:
        row.in_production_qty = payload.in_production_qty
    if payload.name is not None:
        row.name = payload.name
    if payload.remark is not None:
        row.remark = payload.remark
    db.commit()
    db.refresh(row)
    return {"semi_group": row.semi_group, "on_hand_qty": float(row.on_hand_qty or 0),
            "in_production_qty": float(row.in_production_qty or 0)}
