"""中央物流追踪服务 — 把所有带快递单号的业务实体统一挂到 shipments 表。

实时查询复用 logistics_tracking_service 的多 provider (快递100 / 快递鸟);
把派生状态实时回写各业务表 (空字段靠快递推导):
    order              → 签收: status→signed + tracking_confirmed; 在途: paid→shipped
    after_sales_return → 签收: second_inbound_confirmed='是'
    其余实体           → 仅缓存实时状态 (前端展示), 不强改业务字段

入口:
    sync_all(db)            扫各业务表, ensure 每个 (entity, 单号) 一条 shipment
    refresh_active(db)      刷新所有 active shipment + 跑派生
    sync_and_refresh(db)    上面两步 (定时任务调用)
    refresh_entity(db,t,id) 即时刷新某实体的快递 (前端按钮)
    list_for_entity(db,t,id) 取某实体的物流行 (前端展示)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.shipment import Shipment
from app.services import logistics_tracking_service as lts

_logger = logging.getLogger("panse.shipment")


def _entity_sources() -> dict:
    """entity_type → (Model, 单号字段名)。延迟 import 避免循环依赖。

    不取业务表的 carrier 字段当承运商代码: 那些是中文显示名 (顺丰),
    不是 provider 的机器码; 一律让 provider 从单号自动识别。
    """
    from app.models.order import Order, FactoryOrder, PartPurchase
    from app.models.finance import RefillRecord
    from app.models.marketing import AfterSales
    return {
        "order": (Order, "tracking_no"),
        "factory_order": (FactoryOrder, "tracking_no"),
        "part_purchase": (PartPurchase, "tracking_no"),
        "refill_record": (RefillRecord, "tracking_no"),
        "after_sales_refill": (AfterSales, "refill_tracking_no"),
        "after_sales_return": (AfterSales, "return_tracking_no"),
    }


# 仅在途订单自动追踪; signed(已成功)/cancelled(已关闭)/pending_payment/aftersales 不自动查。
_ORDER_ACTIVE_STATUS = ("paid", "shipped")


def _should_auto_track(entity_type: str, ent) -> bool:
    """该实体当前是否应纳入自动(定时)物流轮询。已成功/已关闭/已作废等终态返回 False。

    终态实体不会被定时任务查询 (省额度), 但用户手动点「查快递」仍可强制查 (refresh_entity)。
    """
    if entity_type == "order":
        return getattr(ent, "status", None) in _ORDER_ACTIVE_STATUS
    if entity_type == "factory_order":
        return getattr(ent, "voided_at", None) is None
    # 售后/补单/配件采购: 默认自动追踪, 签收后由 refresh_shipment 自动停轮询
    return True


def upsert_shipment(db: Session, entity_type: str, entity_id: int, tracking_no: str) -> Optional[Shipment]:
    tracking_no = (tracking_no or "").strip()
    if not tracking_no:
        return None
    row = db.execute(select(Shipment).where(
        Shipment.entity_type == entity_type,
        Shipment.entity_id == entity_id,
        Shipment.tracking_no == tracking_no,
    )).scalar_one_or_none()
    if row is None:
        row = Shipment(entity_type=entity_type, entity_id=entity_id,
                       tracking_no=tracking_no, active=True)
        db.add(row)
    return row


def sync_all(db: Session) -> dict:
    """扫所有业务表, 为**应自动追踪**的记录 ensure 一条 shipment。

    已成功(已签收)/已关闭(已取消) 的订单不建、不轮询 — 这类只在用户手动点「查快递」时才查。
    顺带 reconcile: 已建但实体已转终态的, 置 active=False 停止轮询。
    返回 {created, deactivated}。
    """
    created = deactivated = 0
    for etype, (Model, no_field) in _entity_sources().items():
        col = getattr(Model, no_field)
        rows = db.execute(select(Model).where(col.isnot(None), col != "")).scalars().all()
        for ent in rows:
            no = (getattr(ent, no_field) or "").strip()
            if not no:
                continue
            should = _should_auto_track(etype, ent)
            row = db.execute(select(Shipment).where(
                Shipment.entity_type == etype,
                Shipment.entity_id == ent.id,
                Shipment.tracking_no == no,
            )).scalar_one_or_none()
            if row is None:
                if should:
                    db.add(Shipment(entity_type=etype, entity_id=ent.id, tracking_no=no, active=True))
                    created += 1
                # 终态实体不主动建追踪 (留给手动点击)
            elif row.active and not should:
                row.active = False   # 实体已转终态, 停止自动轮询
                deactivated += 1
    db.commit()
    return {"created": created, "deactivated": deactivated}


def _apply_derivation(db: Session, sh: Shipment) -> None:
    """把签收/在途状态回写业务表 (空字段靠快递推导)。失败不影响主流程。"""
    try:
        if sh.entity_type == "order":
            from app.models.order import Order
            o = db.get(Order, sh.entity_id)
            if not o:
                return
            if sh.is_signed:
                if o.status in ("paid", "shipped"):
                    o.status = "signed"
                o.tracking_confirmed = True
            elif sh.mapped_status == "运输中" and o.status == "paid":
                o.status = "shipped"
        elif sh.entity_type == "after_sales_return":
            from app.models.marketing import AfterSales
            a = db.get(AfterSales, sh.entity_id)
            if a and sh.is_signed and (a.second_inbound_confirmed or "") not in ("是", "Y", "yes"):
                a.second_inbound_confirmed = "是"
    except Exception as e:  # pragma: no cover - 防御性
        _logger.warning("派生回写失败 %s#%s: %s", sh.entity_type, sh.entity_id, e)


def refresh_shipment(db: Session, sh: Shipment) -> dict:
    """查一条 shipment 的实时物流, 回写缓存 + 跑派生。不 commit (由调用方批量提交)。"""
    try:
        res = lts.query(db, sh.tracking_no, sh.carrier_code)
    except lts.TrackingUnavailable as e:
        sh.last_error = str(e)[:255]
        return {"ok": False, "error": str(e)}

    sh.carrier_code = res.carrier_code or sh.carrier_code
    sh.carrier_name = res.carrier_name or sh.carrier_name
    sh.provider = res.provider or sh.provider
    sh.state = res.state
    sh.mapped_status = res.mapped_status
    sh.is_signed = res.is_signed
    sh.last_status = res.events[0].context if res.events else sh.last_status
    sh.events = [{"time": ev.time, "context": ev.context} for ev in res.events]
    sh.queried_at = datetime.now(timezone.utc)
    sh.last_error = None
    if res.is_signed:
        sh.active = False   # 签收后停止轮询, 省 API 额度
    _apply_derivation(db, sh)
    return {"ok": True, "status": sh.mapped_status, "signed": sh.is_signed}


def refresh_active(db: Session, limit: int = 500) -> dict:
    """刷新所有 active shipment。未配置物流时整体跳过。"""
    if not lts.is_configured(db):
        return {"checked": 0, "signed": 0, "errors": 0, "skipped": "物流未配置"}
    rows = list(db.execute(
        select(Shipment).where(Shipment.active.is_(True), Shipment.tracking_no.isnot(None)).limit(limit)
    ).scalars().all())
    signed = errors = 0
    for sh in rows:
        r = refresh_shipment(db, sh)
        if not r.get("ok"):
            errors += 1
        elif r.get("signed"):
            signed += 1
    db.commit()
    _logger.info("shipments 刷新: 检查 %d, 签收 %d, 失败 %d", len(rows), signed, errors)
    return {"checked": len(rows), "signed": signed, "errors": errors}


def sync_and_refresh(db: Session) -> dict:
    """定时任务入口: 先 ensure 再刷新。"""
    s = sync_all(db)
    r = refresh_active(db)
    return {**r, "synced": s["created"]}


def list_for_entity(db: Session, entity_type: str, entity_id: int) -> list[Shipment]:
    return list(db.execute(
        select(Shipment).where(
            Shipment.entity_type == entity_type,
            Shipment.entity_id == entity_id,
        ).order_by(Shipment.id.desc())
    ).scalars().all())


def refresh_entity(db: Session, entity_type: str, entity_id: int) -> dict:
    """前端「刷新物流」按钮: ensure + 刷新某实体的快递, 返回结果。"""
    srcs = _entity_sources()
    if entity_type not in srcs:
        raise ValueError(f"未知 entity_type: {entity_type}")
    Model, no_field = srcs[entity_type]
    ent = db.get(Model, entity_id)
    if ent is None:
        raise ValueError(f"{entity_type}#{entity_id} 不存在")
    no = (getattr(ent, no_field) or "").strip()
    if not no:
        return {"ok": False, "error": "该记录未填快递单号"}
    sh = upsert_shipment(db, entity_type, entity_id, no)
    db.flush()
    r = refresh_shipment(db, sh)
    # 手动可强制查任何状态; 但若实体已是终态(已成功/已关闭), 查完即停轮询, 不进自动队列
    if sh is not None and not _should_auto_track(entity_type, ent):
        sh.active = False
    db.commit()
    return r
