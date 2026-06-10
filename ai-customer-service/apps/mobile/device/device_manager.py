"""
apps/mobile/device/device_manager.py
======================================
设备管理：扫描 / 连接 / 重连 / 多设备并发。

支持三种连接类型：
  - emulator : 雷电等本地模拟器（127.0.0.1:5555）
  - usb       : USB 数据线（serial 号，从 adb devices 读）
  - wifi      : 无线 ADB（192.168.x.x:5555）

设备状态枚举：disconnected / connecting / connected / running / paused / error
每个设备独立 threading.Thread + threading.Event 控制生命周期。
状态字典使用 threading.Lock 保护（plan 硬性要求）。
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

_log = logging.getLogger("apps.mobile.device")

_DEVICES_CFG = (
    Path(__file__).parent.parent.parent.parent
    / "configs" / "mobile" / "mobile_devices.json"
)


class DeviceState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING   = "connecting"
    CONNECTED    = "connected"
    RUNNING      = "running"
    PAUSED       = "paused"
    ERROR        = "error"


@dataclass
class DeviceRecord:
    device_id: str
    device_type: str        # emulator | usb | wifi
    shop_cfg_path: str
    state: DeviceState = DeviceState.DISCONNECTED
    error_msg: str = ""
    today_count: int = 0
    today_error_count: int = 0          # 今日异常次数（UI 显示用）
    last_trigger_at: float = 0.0
    adapter: Any = field(default=None, repr=False)
    bridge: Any = field(default=None, repr=False)
    _thread: Any = field(default=None, repr=False)
    _stop_event: threading.Event = field(
        default_factory=threading.Event, repr=False
    )


class DeviceManager:
    """
    管理所有手机端设备。
    _devices 字典由 _lock 保护，多设备并发互不干扰。
    """

    def __init__(self) -> None:
        self._devices: dict[str, DeviceRecord] = {}
        self._lock = threading.Lock()

    # --- 枚举可用设备 ---

    def list_available_devices(self) -> list[dict[str, str]]:
        """通过 adb devices 枚举当前可用设备。"""
        result: list[dict[str, str]] = []
        try:
            out = subprocess.check_output(
                ["adb", "devices"], text=True, timeout=5
            )
        except Exception as exc:
            _log.warning("adb devices 失败: %r", exc)
            return result

        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or "\tdevice" not in line:
                continue
            dev_id = line.split("\t")[0].strip()
            if dev_id.startswith("emulator") or dev_id.startswith("127.0.0.1"):
                dtype = "emulator"
            elif ":" in dev_id:
                dtype = "wifi"
            else:
                dtype = "usb"
            result.append({"device_id": dev_id, "type": dtype})
        return result

    # --- 注册 / 移除 ---

    def add_device(self, device_id: str, device_type: str, shop_cfg_path: str) -> DeviceRecord:
        record = DeviceRecord(
            device_id=device_id,
            device_type=device_type,
            shop_cfg_path=shop_cfg_path,
        )
        with self._lock:
            self._devices[device_id] = record
        _log.info("注册设备: %s (%s) → %s", device_id, device_type, shop_cfg_path)
        return record

    def remove_device(self, device_id: str) -> None:
        with self._lock:
            record = self._devices.pop(device_id, None)
        if record:
            record._stop_event.set()

    def get_record(self, device_id: str) -> DeviceRecord | None:
        with self._lock:
            return self._devices.get(device_id)

    def all_records(self) -> list[DeviceRecord]:
        with self._lock:
            return list(self._devices.values())

    # --- 连接 / 断开 ---

    def connect_device(self, device_id: str) -> bool:
        """创建 Adapter，尝试连接设备。重连时先 disconnect 旧 adapter 防止资源泄漏。

        按 device_type 分支：
          - apk_http : v1.5.x Android APK + Ktor HTTP，通过 pairing.get_pairing 反查 token
          - 其他    : v1.4.x uiautomator2（emulator/wifi/usb）
        """
        record = self.get_record(device_id)
        if record is None:
            _log.error("connect_device: 未知设备 %s", device_id)
            return False

        # 重连防泄漏：旧 adapter 的 notif_thread 必须先停掉
        if record.adapter is not None:
            try:
                record.adapter.disconnect()
            except Exception as exc:
                _log.warning("旧 adapter disconnect 异常（忽略）: %r", exc)
            record.adapter = None

        self._set_state(device_id, DeviceState.CONNECTING)

        # ── 分支 A：v1.5.x APK + HTTP ──────────────────────────────
        if record.device_type == "apk_http":
            try:
                from apps.mobile.adapter.http_mobile_adapter import HttpMobileAdapter
                from apps.mobile.device.pairing import get_pairing
            except Exception as exc:
                _log.error("HttpMobileAdapter import 失败: %r", exc)
                self._set_state(device_id, DeviceState.ERROR, f"import failed: {exc}")
                return False

            rec_pairing = get_pairing(device_id)
            if rec_pairing is None:
                _log.error("apk_http 设备无配对记录: %s", device_id)
                self._set_state(device_id, DeviceState.ERROR, "missing pairing token")
                return False

            adapter = HttpMobileAdapter(
                ip=rec_pairing.ip,
                port=rec_pairing.port,
                token=rec_pairing.token,
                device_label=rec_pairing.label or device_id,
            )
            ok = adapter.connect()
            if ok:
                record.adapter = adapter
                self._set_state(device_id, DeviceState.CONNECTED)
            else:
                self._set_state(device_id, DeviceState.ERROR, "HTTP /health 失败")
            return ok

        # ── 分支 B：v1.4.x uiautomator2 ─────────────────────────────
        from apps.mobile.adapter.mobile_adapter import MobileQianniuAdapter
        from apps.mobile.behavior.human_behavior import HumanBehavior

        hb = HumanBehavior()
        adapter = MobileQianniuAdapter(device_id, human_behavior=hb)
        ok = adapter.connect()
        if ok:
            record.adapter = adapter
            self._set_state(device_id, DeviceState.CONNECTED)
        else:
            self._set_state(device_id, DeviceState.ERROR, "connect() 失败")
        return ok

    def disconnect_device(self, device_id: str) -> None:
        record = self.get_record(device_id)
        if record and record.adapter:
            record.adapter.disconnect()
        self._set_state(device_id, DeviceState.DISCONNECTED)

    # --- 启动 / 暂停 / 停止 ---

    def start_device(self, device_id: str, bridge: Any) -> bool:
        """启动接待循环线程（daemon）。"""
        record = self.get_record(device_id)
        if record is None:
            return False
        if record.state not in (DeviceState.CONNECTED, DeviceState.PAUSED):
            _log.warning("设备 %s 状态 %s 不可启动", device_id, record.state)
            return False

        record.bridge = bridge
        record._stop_event.clear()
        record._thread = threading.Thread(
            target=self._device_loop,
            args=(record,),
            name=f"MobileDevice-{device_id}",
            daemon=True,
        )
        record._thread.start()
        self._set_state(device_id, DeviceState.RUNNING)
        _log.info("接待循环已启动: %s", device_id)
        return True

    def pause_device(self, device_id: str) -> None:
        self._set_state(device_id, DeviceState.PAUSED)

    def stop_device(self, device_id: str, join_timeout_s: float = 5.0) -> None:
        record = self.get_record(device_id)
        if record:
            record._stop_event.set()
            if record._thread and record._thread.is_alive():
                record._thread.join(timeout=join_timeout_s)
        self._set_state(device_id, DeviceState.DISCONNECTED)

    # --- 接待循环 ---

    def _device_loop(self, record: DeviceRecord) -> None:
        """每台设备独立接待循环：轮询未读 → 切换 → 读消息 → 送 brain。

        异常恢复使用渐进退避：5s → 10s → 20s（封顶），避免单次失败即等 15s。
        """
        msg_count = 0
        err_backoff_s = 5.0   # 渐进退避起点

        while not record._stop_event.is_set():
            try:
                if record.state == DeviceState.PAUSED:
                    time.sleep(2.0)
                    continue

                adapter = record.adapter
                bridge = record.bridge
                if adapter is None or bridge is None:
                    time.sleep(5.0)
                    continue

                if not adapter.is_alive():
                    _log.warning("设备断连，尝试重连: %s", record.device_id)
                    self._set_state(record.device_id, DeviceState.ERROR, "连接丢失")
                    if self.auto_reconnect(record.device_id):
                        # 重连成功 → 恢复 RUNNING 状态，UI 不再显示 ERROR
                        self._set_state(record.device_id, DeviceState.RUNNING)
                        # 重连后 record.adapter 已被替换为新实例，刷新本地引用
                        adapter = record.adapter
                    time.sleep(10.0)
                    continue

                unread = adapter.list_unread_sessions()
                if not unread:
                    time.sleep(10.0)
                    continue

                hb = getattr(adapter, "_hb", None)

                for session in unread:
                    if record._stop_event.is_set() or record.state == DeviceState.PAUSED:
                        break
                    if not adapter.switch_to_session(session):
                        continue

                    msgs = adapter.read_latest_messages(limit=5)
                    # 过滤出买家发送的消息（气泡侧别检测后 is_from_buyer=True）
                    buyer_msgs = [m for m in msgs if m.is_from_buyer]
                    buyer_text = buyer_msgs[-1].text if buyer_msgs else ""
                    if buyer_text:
                        bridge.handle_mobile_message(session, buyer_text)
                        record.today_count += 1
                        record.last_trigger_at = time.time()
                        msg_count += 1
                        if hb:
                            hb.breathing_pause(msg_count)

                    # 返回会话列表
                    if adapter._d:
                        adapter._d.press("back")
                    time.sleep(1.0)

                if hb and adapter._d:
                    hb.random_idle_action(adapter._d)

                time.sleep(10.0)

            except Exception as exc:
                _log.error("设备循环异常 %s: %r", record.device_id, exc)
                record.today_error_count += 1
                self._set_state(record.device_id, DeviceState.ERROR, str(exc))
                time.sleep(err_backoff_s)
                err_backoff_s = min(err_backoff_s * 2, 20.0)   # 渐进退避到 20s 封顶
            else:
                err_backoff_s = 5.0   # 一轮循环正常结束，重置退避

        _log.info("接待循环已退出: %s", record.device_id)

    # --- 自动重连 ---

    def auto_reconnect(self, device_id: str, max_retries: int = 3) -> bool:
        for attempt in range(1, max_retries + 1):
            _log.info("重连尝试 %d/%d: %s", attempt, max_retries, device_id)
            if self.connect_device(device_id):
                _log.info("重连成功: %s", device_id)
                return True
            time.sleep(10.0)
        _log.error("重连失败 %d 次: %s", max_retries, device_id)
        self._set_state(device_id, DeviceState.ERROR, f"重连失败 {max_retries} 次")
        return False

    # --- 配置持久化 ---

    def save_devices_config(self) -> None:
        with self._lock:
            records = list(self._devices.values())
        data = [
            {"device_id": r.device_id, "type": r.device_type, "shop_cfg_path": r.shop_cfg_path}
            for r in records
        ]
        _DEVICES_CFG.parent.mkdir(parents=True, exist_ok=True)
        _DEVICES_CFG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_devices_config(self) -> None:
        if not _DEVICES_CFG.exists():
            return
        try:
            for item in json.loads(_DEVICES_CFG.read_text(encoding="utf-8")):
                self.add_device(item["device_id"], item.get("type", "wifi"), item.get("shop_cfg_path", ""))
        except Exception as exc:
            _log.error("加载设备配置失败: %r", exc)

    def _set_state(self, device_id: str, state: DeviceState, error_msg: str = "") -> None:
        with self._lock:
            r = self._devices.get(device_id)
            if r:
                r.state = state
                r.error_msg = error_msg
