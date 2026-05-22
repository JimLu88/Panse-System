"""多平台订单自动抓取 (Phase 9, Tier 2 #1, 借鉴 聚水潭/旺店通).

抽象适配器: 淘宝 (TOP) / 抖店 / 京东 / 拼多多 / 自定义.
每个 platform 实现一个 PlatformAdapter, 提供:
    list_new_orders(since: datetime) -> list[PlatformOrder]
    push_tracking(order_no, tracking_no, carrier) -> bool

配置存在 system_settings: platform_<name>_app_key, platform_<name>_app_secret, ...
不配置时, 该平台用 MockAdapter (跳过, 不报错).

定时任务每 10 分钟调一次, 拉新订单 → 入 Order 表 → 走标准的 order_service.transition 流程.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.services import settings_service

_logger = logging.getLogger("panse.platform_sync")


@dataclass
class PlatformOrder:
    """平台原生订单 → 我们的 Order 字段映射后的 DTO."""
    platform: str
    order_no: str
    order_date: Optional[datetime] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    product_name: Optional[str] = None
    sku: Optional[str] = None
    qty: int = 1
    paid_amount: Optional[Decimal] = None
    remark: Optional[str] = None
    raw: dict = field(default_factory=dict)


class PlatformAdapter(ABC):
    name: str

    @abstractmethod
    def is_configured(self, db: Session) -> bool:
        ...

    @abstractmethod
    def list_new_orders(self, db: Session, since: datetime) -> list[PlatformOrder]:
        ...

    @abstractmethod
    def push_tracking(self, db: Session, order_no: str, tracking_no: str,
                      carrier: str) -> bool:
        """发货后回填快递单号给平台. 失败返 False (不抛)."""
        ...


class MockAdapter(PlatformAdapter):
    """未接入真平台时用. 返回空列表; push 永远成功."""
    def __init__(self, name: str):
        self.name = name

    def is_configured(self, db):
        return False

    def list_new_orders(self, db, since):
        return []

    def push_tracking(self, db, order_no, tracking_no, carrier):
        _logger.info("[mock] %s push_tracking %s -> %s/%s", self.name, order_no,
                     tracking_no, carrier)
        return True


# 注册的平台. 接入真适配器时, 在这里替换.
_REGISTRY: dict[str, PlatformAdapter] = {
    "taobao": MockAdapter("taobao"),
    "douyin": MockAdapter("douyin"),
    "jd": MockAdapter("jd"),
    "pdd": MockAdapter("pdd"),
}


def register_adapter(name: str, adapter: PlatformAdapter) -> None:
    """生产环境用真实现替换 mock."""
    _REGISTRY[name] = adapter


def get_adapter(name: str) -> PlatformAdapter:
    return _REGISTRY.get(name, MockAdapter(name))


# ----------------------------- 同步主流程 ----------------------- #


def sync_all_platforms(db: Session, *, since_hours: int = 1) -> dict:
    """定时任务调. 拉所有平台最近 N 小时新订单."""
    from datetime import timedelta
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    total_inserted = 0
    per_platform = {}
    for name, adapter in _REGISTRY.items():
        if not adapter.is_configured(db):
            per_platform[name] = {"configured": False, "inserted": 0}
            continue
        try:
            orders = adapter.list_new_orders(db, since)
            n = _ingest(db, orders, platform=name)
            per_platform[name] = {"configured": True, "fetched": len(orders), "inserted": n}
            total_inserted += n
        except Exception as e:  # pragma: no cover
            _logger.warning("平台 %s 同步失败: %s", name, e)
            per_platform[name] = {"configured": True, "error": str(e), "inserted": 0}
    return {"total_inserted": total_inserted, "per_platform": per_platform}


def _ingest(db: Session, orders: list[PlatformOrder], *, platform: str) -> int:
    """把平台订单批量入 Order 表 (去重: order_no 唯一)."""
    inserted = 0
    for po in orders:
        existing = db.execute(
            select(Order).where(Order.order_no == po.order_no)
        ).scalar_one_or_none()
        if existing is not None:
            continue
        o = Order(
            platform=po.platform or platform,
            order_no=po.order_no,
            order_date=po.order_date.date() if po.order_date else None,
            customer_name=po.customer_name,
            customer_phone=po.customer_phone,
            customer_address=po.customer_address,
            product_name=po.product_name,
            sku=po.sku, qty=po.qty,
            paid_amount=po.paid_amount,
            remark=po.remark,
            status="pending_payment",
        )
        db.add(o)
        inserted += 1
    db.flush()
    return inserted


def push_tracking_for_order(db: Session, order: Order) -> bool:
    """订单发货时调. 自动找对应 platform 的 adapter 推单号."""
    if not order.tracking_no or not order.platform:
        return False
    adapter = get_adapter(order.platform)
    return adapter.push_tracking(
        db, order.order_no, order.tracking_no, order.carrier or "",
    )
