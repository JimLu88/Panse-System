"""物流电子面单 (Phase 9, Tier 2 #2, 借鉴 聚水潭).

接菜鸟 / 顺丰 / 京东物流 / 中通的电子面单 API.
当前用 Mock + 配置 hook, 生产替换 _REGISTRY 即可。

⚠️ 注意: 真电子面单需与快递公司签约 (月结账号 + 网点 + 面单模板), 不是免费 key 能开通。
未接真适配器前, Mock 返回的单号会被明确标记 is_mock=True + 时间轴打 [模拟], 严禁当真单号外发。

公开:
    print_label(db, order_id) -> {tracking_no, carrier, label_url, is_mock}
"""
from __future__ import annotations

import logging
import random
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.order import Order

_logger = logging.getLogger("panse.shipping_label")


@dataclass
class ShippingLabel:
    tracking_no: str
    carrier: str
    label_url: str         # 可下载 PDF/PNG 的 URL
    label_b64: Optional[str] = None   # 直接打印用 base64
    is_mock: bool = False             # True = 模拟单号 (未接真快递), 切勿当真单号外发


class ShippingAdapter(ABC):
    name: str

    @abstractmethod
    def is_configured(self, db: Session) -> bool: ...

    @abstractmethod
    def get_label(self, db: Session, order: Order) -> ShippingLabel: ...


class MockShippingAdapter(ShippingAdapter):
    """开发环境用. 随机生成单号 + 占位 URL."""
    def __init__(self, name="顺丰"):
        self.name = name

    def is_configured(self, db):
        return True   # mock 永远可用

    def get_label(self, db, order):
        prefix = {"顺丰": "SF", "中通": "ZT", "圆通": "YT", "京东": "JD"}.get(
            self.name, "EX",
        )
        rnd = "".join(random.choices(string.digits, k=10))
        _logger.warning(
            "电子面单未接入真实快递, 返回模拟单号 (order=%s, carrier=%s)。"
            "生产请把 _REGISTRY 替换为真适配器, 勿把此单号当真实快递号。",
            getattr(order, "id", "?"), self.name,
        )
        return ShippingLabel(
            tracking_no=f"{prefix}{rnd}",
            carrier=self.name,
            label_url=f"/api/shipping-labels/mock/{prefix}{rnd}.png",
            is_mock=True,
        )


_REGISTRY: dict[str, ShippingAdapter] = {
    "default": MockShippingAdapter("顺丰"),
    "顺丰": MockShippingAdapter("顺丰"),
    "中通": MockShippingAdapter("中通"),
    "京东": MockShippingAdapter("京东"),
}


def register_adapter(name: str, adapter: ShippingAdapter) -> None:
    _REGISTRY[name] = adapter


def print_label(
    db: Session, *, order_id: int, carrier: Optional[str] = None,
) -> ShippingLabel:
    """业务 API: 给一个订单一键拿电子面单 (单号 + 可下载 URL)."""
    order = db.get(Order, order_id)
    if order is None:
        raise ValueError(f"订单 {order_id} 不存在")
    if not order.customer_address or not order.customer_phone:
        raise ValueError("订单缺收件人地址/电话")
    adapter = _REGISTRY.get(carrier or order.carrier or "default")
    if adapter is None:
        adapter = _REGISTRY["default"]
    label = adapter.get_label(db, order)
    # 回填到订单
    order.tracking_no = label.tracking_no
    order.carrier = label.carrier
    db.flush()
    # 时间轴留痕 (模拟单号明确标 [模拟], 防止被当成真实快递号)
    try:
        from app.services import order_event_service
        order_event_service.record(
            db, order_id=order.id, kind="shipping_label_generated",
            summary=f"{'[模拟] ' if label.is_mock else ''}打印面单 {label.carrier} {label.tracking_no}",
        )
    except Exception:  # pragma: no cover
        pass
    return label
