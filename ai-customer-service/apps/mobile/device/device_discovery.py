"""
apps/mobile/device/device_discovery.py
=======================================
局域网 mDNS 发现 Android APK 实例（QianniuAgent）。

工作流：
  1. APK 端 Phase 2 暂未广播 mDNS（属可选增强；首版用「手动扫码」即可）
  2. 本模块作为基础设施：未来 APK 启动 HttpServer 时同时 publish _qianniuagent._tcp
  3. PC 端 DeviceDiscovery.start() 监听该服务，发现设备 IP:Port
  4. PC 端 UI 提示用户「发现 N 台设备」，结合 pairing.py 的 token 自动连接

设计：
  - 静态备用方案：discover_by_probe(cidr_or_ip_list) 直接对子网每个 IP 探 /health
  - 主路径：zeroconf ServiceBrowser 监听 _qianniuagent._tcp.local
  - 异常吞噬：zeroconf 在某些受限网络环境（双 NAT、企业 VPN）会失败，
    失败时降级到「用户手动输入 IP」流程，不阻塞主功能

依赖：zeroconf>=0.131.0（已加到 requirements.txt by Task #25）
"""
from __future__ import annotations

import logging
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass

import httpx

log = logging.getLogger("apps.mobile.device.discovery")

_SERVICE_TYPE = "_qianniuagent._tcp.local."
_DEFAULT_PROBE_TIMEOUT_S = 1.0


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    ip: str
    port: int
    instance_name: str = ""
    accessibility_connected: bool = False

    @property
    def device_id(self) -> str:
        return f"{self.ip}:{self.port}"


# ── 主动 ping 子网（备用方案）─────────────────────────────────

def discover_by_probe(
    candidates: list[str],
    port: int = 8765,
    timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
) -> list[DiscoveredDevice]:
    """
    给定一组候选 IP，逐个 GET http://ip:port/health。
    超时/连接失败的跳过。

    用法：
      ips = ["192.168.1." + str(i) for i in range(1, 255)]
      found = discover_by_probe(ips, port=8765)
    """
    out: list[DiscoveredDevice] = []
    for ip in candidates:
        ip = (ip or "").strip()
        if not ip:
            continue
        try:
            with httpx.Client(timeout=timeout_s) as c:
                r = c.get(f"http://{ip}:{port}/health")
                if r.status_code != 200:
                    continue
                body = r.json()
                if not body.get("ok"):
                    continue
                out.append(
                    DiscoveredDevice(
                        ip=ip,
                        port=port,
                        instance_name=str(body.get("version") or ""),
                        accessibility_connected=bool(body.get("accessibilityConnected")),
                    )
                )
        except Exception:
            # 探测失败属正常情况（多数 IP 没装 APK），不污染日志
            pass
    return out


def list_local_subnet_ipv4_pool() -> list[str]:
    """
    用 socket 取本机 LAN IP，推 /24 子网所有候选 IP。
    单网卡环境足够；多网卡建议用 zeroconf 路径。
    """
    try:
        # 经典技巧：UDP 不真正连 8.8.8.8，但能让 OS 选出默认出口 IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            self_ip = s.getsockname()[0]
        finally:
            s.close()
        parts = self_ip.split(".")
        if len(parts) != 4:
            return []
        prefix = ".".join(parts[:3])
        # 不扫 .0 / .255，跳过自己
        return [f"{prefix}.{i}" for i in range(1, 255) if str(i) != parts[3]]
    except Exception as e:
        log.warning("list_local_subnet_ipv4_pool 失败: %r", e)
        return []


# ── zeroconf mDNS 监听（主路径）──────────────────────────────

class MdnsDiscovery:
    """
    用 zeroconf 监听 _qianniuagent._tcp 服务。

    使用模式：
      d = MdnsDiscovery(on_added=lambda dev: ..., on_removed=lambda did: ...)
      d.start()
      ...
      d.stop()

    APK 端未来加上 ServiceInfo publish 后即可被本类发现。
    当前版本中：本模块是 forward-looking 基础设施；首版主要用 discover_by_probe。
    """

    def __init__(
        self,
        on_added: Callable[[DiscoveredDevice], None] | None = None,
        on_removed: Callable[[str], None] | None = None,
    ) -> None:
        self._on_added = on_added
        self._on_removed = on_removed
        self._zc: object | None = None
        self._browser: object | None = None
        self._lock = threading.Lock()
        self._known: dict[str, DiscoveredDevice] = {}

    def start(self) -> bool:
        """
        启动监听。@return True 表示已启动；False 表示 zeroconf 库或网络不可用
        """
        try:
            from zeroconf import ServiceBrowser, Zeroconf  # 延迟 import 防硬依赖
        except Exception as e:
            log.warning("zeroconf 不可用（pip install zeroconf）: %r", e)
            return False
        try:
            self._zc = Zeroconf()
            listener = _MdnsListener(self)
            self._browser = ServiceBrowser(self._zc, _SERVICE_TYPE, listener)
            log.info("mDNS 监听已启动 (%s)", _SERVICE_TYPE)
            return True
        except Exception as e:
            log.exception("MdnsDiscovery.start 失败: %r", e)
            self._cleanup()
            return False

    def stop(self) -> None:
        self._cleanup()

    def list_known(self) -> list[DiscoveredDevice]:
        with self._lock:
            return list(self._known.values())

    def _cleanup(self) -> None:
        try:
            if self._browser is not None:
                try:
                    self._browser.cancel()  # type: ignore[attr-defined]
                except Exception:
                    pass
                self._browser = None
            if self._zc is not None:
                try:
                    self._zc.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
                self._zc = None
        finally:
            with self._lock:
                self._known.clear()

    # 由 listener 回调
    def _on_service_added(self, name: str, ip: str, port: int) -> None:
        dev = DiscoveredDevice(ip=ip, port=port, instance_name=name)
        with self._lock:
            self._known[dev.device_id] = dev
        if self._on_added:
            try:
                self._on_added(dev)
            except Exception:
                log.exception("on_added callback raised")

    def _on_service_removed(self, name: str, ip: str, port: int) -> None:
        did = f"{ip}:{port}"
        with self._lock:
            self._known.pop(did, None)
        if self._on_removed:
            try:
                self._on_removed(did)
            except Exception:
                log.exception("on_removed callback raised")


class _MdnsListener:
    """zeroconf.ServiceListener 协议实现（duck-typed）。"""

    def __init__(self, owner: MdnsDiscovery) -> None:
        self._owner = owner

    def add_service(self, zc, type_, name):  # noqa: ANN001
        info = zc.get_service_info(type_, name)
        if info is None:
            return
        try:
            addresses = info.parsed_addresses() if hasattr(info, "parsed_addresses") else []
            if not addresses:
                return
            ip = addresses[0]
            port = int(info.port or 8765)
            self._owner._on_service_added(name, ip, port)
        except Exception:
            log.exception("MdnsListener.add_service 解析失败 name=%s", name)

    def remove_service(self, zc, type_, name):  # noqa: ANN001
        try:
            # 从 known 表里反查（按 instance_name）
            for dev in self._owner.list_known():
                if dev.instance_name == name:
                    self._owner._on_service_removed(name, dev.ip, dev.port)
                    return
        except Exception:
            log.exception("MdnsListener.remove_service 失败 name=%s", name)

    def update_service(self, zc, type_, name):  # noqa: ANN001
        # 简化：当成 add 处理（IP 可能变了）
        self.add_service(zc, type_, name)
