"""快递 provider 抽象 (快递100 + 快递鸟) 单元测试。

全程 mock 网络 (httpx) 与配置 (settings_service.get), 不连真接口、不碰真 key。
覆盖: 快递鸟签名算法、State 归一化、Traces 反转、provider 选择、快递100 不回归。
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from app.services import logistics_tracking_service as lts


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _FakeHttpx:
    """按 url 片段返回预设响应; 记录最后一次调用参数供断言。"""

    HTTPError = Exception  # 让 except (httpx.HTTPError, ...) 仍可捕获

    def __init__(self, get_map=None, post_map=None):
        self.get_map = get_map or {}
        self.post_map = post_map or {}
        self.last_post = None
        self.last_get = None

    def get(self, url, params=None, timeout=None):
        self.last_get = {"url": url, "params": params}
        for frag, resp in self.get_map.items():
            if frag in url:
                return _Resp(resp)
        return _Resp({})

    def post(self, url, data=None, timeout=None):
        self.last_post = {"url": url, "data": data}
        for frag, resp in self.post_map.items():
            if frag in url:
                return _Resp(resp)
        return _Resp({})


def _settings(values):
    def _get(db, key, env_fallback=True):
        return values.get(key)
    return _get


def test_kdniao_sign_is_base64_of_md5():
    sign = lts.KdniaoProvider()._sign("HELLO", "KEY123")
    expected = base64.b64encode(
        hashlib.md5("HELLOKEY123".encode("utf-8")).hexdigest().encode("utf-8")
    ).decode("utf-8")
    assert sign == expected


def test_kdniao_query_signed_and_traces_reversed(monkeypatch):
    monkeypatch.setattr(lts.settings_service, "get", _settings({
        "kdniao_ebusiness_id": "100", "kdniao_api_key": "secret",
        "tracking_provider": "kdniao",
    }))
    fake = _FakeHttpx(post_map={
        "kdniao.com": {
            "Success": True, "State": "3", "ShipperCode": "SF",
            "Traces": [
                {"AcceptTime": "2026-06-01 09:00", "AcceptStation": "已揽收"},
                {"AcceptTime": "2026-06-03 18:00", "AcceptStation": "已签收"},
            ],
        },
    })
    monkeypatch.setattr(lts, "httpx", fake)

    r = lts.query(None, "SF123", carrier_code="SF")
    assert r.provider == "kdniao"
    assert r.is_signed is True
    assert r.mapped_status == "已到货"
    # Traces 旧→新, events 应反转成 新→旧: events[0] = 最新 (已签收)
    assert r.events[0].context == "已签收"


def test_kdniao_in_transit_not_signed(monkeypatch):
    monkeypatch.setattr(lts.settings_service, "get", _settings({
        "kdniao_ebusiness_id": "100", "kdniao_api_key": "secret",
        "tracking_provider": "kdniao",
    }))
    fake = _FakeHttpx(post_map={
        "kdniao.com": {"Success": True, "State": "2", "ShipperCode": "SF",
                       "Traces": [{"AcceptTime": "2026-06-01 09:00", "AcceptStation": "运输中"}]},
    })
    monkeypatch.setattr(lts, "httpx", fake)
    r = lts.query(None, "SF123", carrier_code="SF")
    assert r.is_signed is False
    assert r.mapped_status == "运输中"


def test_provider_auto_prefers_kuaidi100(monkeypatch):
    monkeypatch.setattr(lts.settings_service, "get", _settings({
        "kuaidi100_customer": "c", "kuaidi100_key": "k",
        "kdniao_ebusiness_id": "100", "kdniao_api_key": "secret",
        "tracking_provider": "auto",
    }))
    assert lts._select_provider(None).name == "kuaidi100"


def test_provider_auto_falls_back_to_kdniao(monkeypatch):
    monkeypatch.setattr(lts.settings_service, "get", _settings({
        "kdniao_ebusiness_id": "100", "kdniao_api_key": "secret",
        "tracking_provider": "auto",
    }))
    assert lts._select_provider(None).name == "kdniao"


def test_provider_explicit_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(lts.settings_service, "get", _settings({
        "tracking_provider": "kdniao",  # 选了快递鸟但没填凭证
    }))
    with pytest.raises(lts.TrackingUnavailable):
        lts.query(None, "SF123")


def test_no_provider_configured_raises(monkeypatch):
    monkeypatch.setattr(lts.settings_service, "get", _settings({"tracking_provider": "auto"}))
    assert lts.is_configured(None) is False
    with pytest.raises(lts.TrackingUnavailable):
        lts.query(None, "SF123")


def test_kuaidi100_query_sign_unchanged(monkeypatch):
    monkeypatch.setattr(lts.settings_service, "get", _settings({
        "kuaidi100_customer": "CUST", "kuaidi100_key": "KEY",
        "tracking_provider": "kuaidi100",
    }))
    fake = _FakeHttpx(post_map={
        "poll.kuaidi100.com": {
            "status": "200", "state": "3", "com": "shunfeng",
            "data": [{"ftime": "2026-06-03 18:00", "context": "已签收"}],
        },
    })
    monkeypatch.setattr(lts, "httpx", fake)

    r = lts.query(None, "SF999", carrier_code="shunfeng")
    assert r.provider == "kuaidi100"
    assert r.is_signed is True
    # 快递100 sign = MD5(param + key + customer).upper()
    param = json.dumps({"com": "shunfeng", "num": "SF999"}, ensure_ascii=False)
    expected_sign = hashlib.md5((param + "KEY" + "CUST").encode("utf-8")).hexdigest().upper()
    assert fake.last_post["data"]["sign"] == expected_sign
