"""物流实时追踪服务 — 配件快递单号 → 物流时间线 + 状态归一化。

集成快递100 实时查询 API (需在 系统设置 配置 kuaidi100_customer / kuaidi100_key)。
未配置时优雅降级: 抛 TrackingUnavailable, 上层提示用户改用手动状态, 不阻断主流程。

设计为可插拔: 后续可加菜鸟/快递鸟等 provider, 只需实现 query(carrier, no) -> TrackResult。
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.services import settings_service

_logger = logging.getLogger("panse.logistics_tracking")

_KUAIDI100_QUERY = "https://poll.kuaidi100.com/poll/query.do"
_KUAIDI100_AUTONUM = "https://www.kuaidi100.com/autonumber/autoComNum"
_TIMEOUT = 15.0

# 快递100 state → 系统配件状态
#  0 在途 / 1 揽收 / 2 疑难 / 3 签收 / 4 退签 / 5 派件 / 6 退回 / 10 待清关 ...
_STATE_TO_STATUS = {
    "3": "已到货",   # 签收
}


class TrackingUnavailable(RuntimeError):
    """物流查询不可用 (未配置 / 网络错 / 单号无效)。"""


@dataclass
class TrackEvent:
    time: Optional[str]      # 原始时间字符串
    context: str             # 轨迹描述


@dataclass
class TrackResult:
    carrier_code: Optional[str]
    carrier_name: Optional[str]
    tracking_no: str
    state: Optional[str]            # 快递100 state 码
    mapped_status: Optional[str]    # 归一化到系统配件状态 (已到货 / 运输中)
    is_signed: bool
    events: list[TrackEvent] = field(default_factory=list)
    queried_at: str = ""


def _get_credentials(db: Session) -> tuple[str, str]:
    customer = settings_service.get(db, "kuaidi100_customer", env_fallback=True)
    key = settings_service.get(db, "kuaidi100_key", env_fallback=True)
    if not customer or not key:
        raise TrackingUnavailable(
            "物流查询未配置: 请到 管理 → 集成配置 填 kuaidi100_customer / kuaidi100_key"
        )
    return customer, key


def detect_carrier(db: Session, tracking_no: str) -> Optional[str]:
    """根据单号自动识别承运商代码 (快递100 autonumber). 失败返回 None。"""
    try:
        r = httpx.get(_KUAIDI100_AUTONUM, params={"text": tracking_no}, timeout=_TIMEOUT)
        arr = r.json()
        if isinstance(arr, list) and arr:
            return arr[0].get("comCode")
    except (httpx.HTTPError, ValueError, KeyError) as e:
        _logger.info("自动识别承运商失败 (%s): %s", tracking_no, e)
    return None


def query(db: Session, tracking_no: str, carrier_code: Optional[str] = None) -> TrackResult:
    """查询单个快递单号的实时物流。未配置/失败抛 TrackingUnavailable。"""
    if not tracking_no:
        raise TrackingUnavailable("快递单号为空")
    customer, key = _get_credentials(db)

    com = carrier_code or detect_carrier(db, tracking_no)
    if not com:
        raise TrackingUnavailable(f"无法识别快递公司, 请手动指定承运商 (单号 {tracking_no})")

    param = json.dumps({"com": com, "num": tracking_no}, ensure_ascii=False)
    sign = hashlib.md5((param + key + customer).encode("utf-8")).hexdigest().upper()
    try:
        r = httpx.post(
            _KUAIDI100_QUERY,
            data={"customer": customer, "sign": sign, "param": param},
            timeout=_TIMEOUT,
        )
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise TrackingUnavailable(f"物流查询网络失败: {e}") from e

    # 快递100: returnCode != 200 或 status != 200 表示失败
    if str(data.get("status")) != "200" and data.get("returnCode"):
        raise TrackingUnavailable(
            f"物流查询失败: {data.get('message')} (code={data.get('returnCode')})"
        )

    state = str(data.get("state")) if data.get("state") is not None else None
    events = [
        TrackEvent(time=d.get("ftime") or d.get("time"), context=d.get("context", ""))
        for d in (data.get("data") or [])
    ]
    is_signed = state == "3"
    mapped = _STATE_TO_STATUS.get(state, "运输中" if events else None)

    return TrackResult(
        carrier_code=com,
        carrier_name=data.get("com") or com,
        tracking_no=tracking_no,
        state=state,
        mapped_status=mapped,
        is_signed=is_signed,
        events=events,
        queried_at=datetime.now(timezone.utc).isoformat(),
    )


def refresh_item(db: Session, item_id: int) -> dict:
    """刷新一个配件行的物流并回写缓存。返回 {ok, status, error?}。

    查询失败 (含未配置) 不抛, 返回 {ok: False, error}, 让前端提示用户手动维护。
    """
    from app.models.order import OrderAccessoryItem

    item = db.get(OrderAccessoryItem, item_id)
    if not item:
        raise ValueError(f"accessory item {item_id} not found")
    if not item.tracking_no:
        return {"ok": False, "error": "该配件未填快递单号"}

    try:
        result = query(db, item.tracking_no, item.carrier_code)
    except TrackingUnavailable as e:
        return {"ok": False, "error": str(e)}

    item.carrier_code = result.carrier_code or item.carrier_code
    item.carrier_name = result.carrier_name or item.carrier_name
    item.tracking_events = [{"time": ev.time, "context": ev.context} for ev in result.events]
    item.tracking_last_status = result.events[0].context if result.events else None
    item.tracking_updated_at = datetime.now(timezone.utc)
    # 已签收自动置「已到货」; 否则若还在前序状态则置「运输中」
    if result.is_signed:
        item.status = "已到货"
        item.alert_level = None
        item.alert_reason = None
    elif item.status in ("未采购", "已下单"):
        item.status = "运输中"
    db.commit()
    return {"ok": True, "status": item.status, "last": item.tracking_last_status}


def refresh_in_transit(db: Session, limit: int = 200) -> dict:
    """批量刷新所有「运输中/已下单」且有单号的配件 (定时任务调用)。

    返回 {checked, signed, errors}。未配置物流时整体跳过。
    """
    from sqlalchemy import select
    from app.models.order import OrderAccessoryItem

    try:
        _get_credentials(db)
    except TrackingUnavailable:
        return {"checked": 0, "signed": 0, "errors": 0, "skipped": "物流未配置"}

    items = list(db.execute(
        select(OrderAccessoryItem).where(
            OrderAccessoryItem.status.in_(["运输中", "已下单"]),
            OrderAccessoryItem.tracking_no.isnot(None),
        ).limit(limit)
    ).scalars().all())

    signed = errors = 0
    for item in items:
        res = refresh_item(db, item.id)
        if not res.get("ok"):
            errors += 1
        elif res.get("status") == "已到货":
            signed += 1
    _logger.info("物流批量刷新: 检查 %d, 签收 %d, 失败 %d", len(items), signed, errors)
    return {"checked": len(items), "signed": signed, "errors": errors}
