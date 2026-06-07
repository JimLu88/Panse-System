"""物流实时追踪服务 — 快递单号 → 物流时间线 + 状态归一化。

可插拔多 provider:
  - 快递100 (kuaidi100): 配置 kuaidi100_customer / kuaidi100_key
  - 快递鸟 (kdniao):     配置 kdniao_ebusiness_id / kdniao_api_key

provider 选择由设置 `tracking_provider` 决定 (kuaidi100 | kdniao | auto)。
默认 auto: 谁配了用谁, 优先快递100。两家都未配置时优雅降级抛 TrackingUnavailable,
上层提示用户改用手动状态, 不阻断主流程。

新增 provider 只需实现 TrackProvider 并登记到 _PROVIDERS。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.services import settings_service

_logger = logging.getLogger("panse.logistics_tracking")

_TIMEOUT = 15.0

# 快递100 endpoints
_KUAIDI100_QUERY = "https://poll.kuaidi100.com/poll/query.do"
_KUAIDI100_AUTONUM = "https://www.kuaidi100.com/autonumber/autoComNum"
# 快递鸟 gateway (即时查询 1002 / 单号识别 2002, 均为免费档)
_KDNIAO_GATEWAY = "https://api.kdniao.com/Ebusiness/EbusinessOrderHandle.aspx"

# 归一化: provider 的「签收」state 码。快递100 与 快递鸟 均以 state=3 表示已签收。
_SIGNED_STATE = "3"


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
    state: Optional[str]            # provider state 码
    mapped_status: Optional[str]    # 归一化到系统配件状态 (已到货 / 运输中)
    is_signed: bool
    provider: str = ""
    events: list[TrackEvent] = field(default_factory=list)
    queried_at: str = ""


def _finalize(
    provider: str, carrier_code: Optional[str], carrier_name: Optional[str],
    tracking_no: str, state: Optional[str], events: list[TrackEvent],
) -> TrackResult:
    is_signed = state == _SIGNED_STATE
    mapped = "已到货" if is_signed else ("运输中" if events else None)
    return TrackResult(
        carrier_code=carrier_code, carrier_name=carrier_name, tracking_no=tracking_no,
        state=state, mapped_status=mapped, is_signed=is_signed, provider=provider,
        events=events, queried_at=datetime.now(timezone.utc).isoformat(),
    )


# --------------------------------------------------------------------------- #
#  Provider 抽象
# --------------------------------------------------------------------------- #
class TrackProvider(ABC):
    name: str

    @abstractmethod
    def is_configured(self, db: Session) -> bool: ...

    @abstractmethod
    def detect_carrier(self, db: Session, tracking_no: str) -> Optional[str]: ...

    @abstractmethod
    def query(self, db: Session, tracking_no: str, carrier_code: Optional[str]) -> TrackResult: ...


class Kuaidi100Provider(TrackProvider):
    name = "kuaidi100"

    def _creds(self, db: Session) -> tuple[Optional[str], Optional[str]]:
        return (
            settings_service.get(db, "kuaidi100_customer", env_fallback=True),
            settings_service.get(db, "kuaidi100_key", env_fallback=True),
        )

    def is_configured(self, db: Session) -> bool:
        customer, key = self._creds(db)
        return bool(customer and key)

    def detect_carrier(self, db: Session, tracking_no: str) -> Optional[str]:
        try:
            r = httpx.get(_KUAIDI100_AUTONUM, params={"text": tracking_no}, timeout=_TIMEOUT)
            arr = r.json()
            if isinstance(arr, list) and arr:
                return arr[0].get("comCode")
        except (httpx.HTTPError, ValueError, KeyError) as e:
            _logger.info("快递100 识别承运商失败 (%s): %s", tracking_no, e)
        return None

    def query(self, db: Session, tracking_no: str, carrier_code: Optional[str]) -> TrackResult:
        customer, key = self._creds(db)
        if not (customer and key):
            raise TrackingUnavailable("快递100 未配置")
        com = carrier_code or self.detect_carrier(db, tracking_no)
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
            raise TrackingUnavailable(f"快递100 查询网络失败: {e}") from e

        if str(data.get("status")) != "200" and data.get("returnCode"):
            raise TrackingUnavailable(
                f"快递100 查询失败: {data.get('message')} (code={data.get('returnCode')})"
            )

        state = str(data.get("state")) if data.get("state") is not None else None
        events = [
            TrackEvent(time=d.get("ftime") or d.get("time"), context=d.get("context", ""))
            for d in (data.get("data") or [])
        ]
        return _finalize(self.name, com, data.get("com") or com, tracking_no, state, events)


class KdniaoProvider(TrackProvider):
    name = "kdniao"

    def _creds(self, db: Session) -> tuple[Optional[str], Optional[str]]:
        return (
            settings_service.get(db, "kdniao_ebusiness_id", env_fallback=True),
            settings_service.get(db, "kdniao_api_key", env_fallback=True),
        )

    def is_configured(self, db: Session) -> bool:
        eid, key = self._creds(db)
        return bool(eid and key)

    def _sign(self, request_data: str, key: str) -> str:
        # 快递鸟: Base64(MD5(RequestData + ApiKey))。表单提交时 httpx 会再做一次
        # x-www-form-urlencoded 编码 (= 文档要求的 URL 编码), 故此处不手动 urlencode,
        # 否则会双重编码导致验签失败。
        md5 = hashlib.md5((request_data + key).encode("utf-8")).hexdigest()
        return base64.b64encode(md5.encode("utf-8")).decode("utf-8")

    def _post(self, db: Session, request_type: str, payload: dict) -> dict:
        eid, key = self._creds(db)
        if not (eid and key):
            raise TrackingUnavailable("快递鸟未配置: 请填 kdniao_ebusiness_id / kdniao_api_key")
        request_data = json.dumps(payload, ensure_ascii=False)
        try:
            r = httpx.post(
                _KDNIAO_GATEWAY,
                data={
                    "RequestData": request_data,
                    "EBusinessID": eid,
                    "RequestType": request_type,
                    "DataSign": self._sign(request_data, key),
                    "DataType": "2",
                },
                timeout=_TIMEOUT,
            )
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise TrackingUnavailable(f"快递鸟查询网络失败: {e}") from e

    def detect_carrier(self, db: Session, tracking_no: str) -> Optional[str]:
        # 单号识别 (免费) RequestType=2002
        try:
            data = self._post(db, "2002", {"LogisticCode": tracking_no})
        except TrackingUnavailable:
            return None
        shippers = data.get("Shippers") or []
        if shippers:
            return shippers[0].get("ShipperCode")
        return None

    def query(self, db: Session, tracking_no: str, carrier_code: Optional[str]) -> TrackResult:
        code = carrier_code or self.detect_carrier(db, tracking_no)
        if not code:
            raise TrackingUnavailable(f"快递鸟无法识别快递公司, 请手动指定承运商 (单号 {tracking_no})")
        # 即时查询 RequestType=1002
        data = self._post(db, "1002", {"ShipperCode": code, "LogisticCode": tracking_no, "OrderCode": ""})
        if not data.get("Success", False):
            raise TrackingUnavailable(f"快递鸟查询失败: {data.get('Reason') or data}")

        state = str(data.get("State")) if data.get("State") is not None else None
        # 快递鸟 Traces 为旧→新, 反转成 新→旧 与快递100 对齐 (events[0] = 最新)
        traces = list(reversed(data.get("Traces") or []))
        events = [
            TrackEvent(time=t.get("AcceptTime"), context=t.get("AcceptStation") or t.get("Action") or "")
            for t in traces
        ]
        return _finalize(self.name, code, data.get("ShipperCode") or code, tracking_no, state, events)


_PROVIDERS: dict[str, TrackProvider] = {
    "kuaidi100": Kuaidi100Provider(),
    "kdniao": KdniaoProvider(),
}
# auto 模式下的尝试顺序 (优先快递100)
_PROVIDER_PREFERENCE = ("kuaidi100", "kdniao")


def _select_provider(db: Session) -> TrackProvider:
    """按设置选 provider。explicit 选了但没配 → 报错; auto → 谁配了用谁。都没配 → 抛。"""
    pref = (settings_service.get(db, "tracking_provider", env_fallback=True) or "auto").strip().lower()
    if pref in _PROVIDERS:
        provider = _PROVIDERS[pref]
        if provider.is_configured(db):
            return provider
        raise TrackingUnavailable(
            f"已选物流 provider「{pref}」但未配置凭证, 请到 管理 → 集成配置 填写。"
        )
    for name in _PROVIDER_PREFERENCE:
        if _PROVIDERS[name].is_configured(db):
            return _PROVIDERS[name]
    raise TrackingUnavailable(
        "物流查询未配置: 请到 管理 → 集成配置 填 快递100 (kuaidi100_customer/key) "
        "或 快递鸟 (kdniao_ebusiness_id/api_key)"
    )


def is_configured(db: Session) -> bool:
    """是否至少有一个可用 provider。"""
    try:
        _select_provider(db)
        return True
    except TrackingUnavailable:
        return False


def detect_carrier(db: Session, tracking_no: str) -> Optional[str]:
    """用当前 provider 识别承运商代码。失败返回 None。"""
    if not tracking_no:
        return None
    try:
        return _select_provider(db).detect_carrier(db, tracking_no)
    except TrackingUnavailable:
        return None


def query(db: Session, tracking_no: str, carrier_code: Optional[str] = None) -> TrackResult:
    """查询单个快递单号的实时物流。未配置/失败抛 TrackingUnavailable。"""
    if not tracking_no:
        raise TrackingUnavailable("快递单号为空")
    return _select_provider(db).query(db, tracking_no, carrier_code)


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

    if not is_configured(db):
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
