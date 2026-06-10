"""
apps/web_dashboard/ipc/state_reader.py
=======================================
从主程序读取实时状态数据，发送控制命令。

读取策略（双路，自动降级）：
  1. ZMQ 缓存（首选）— 主程序 PUB 广播，延迟 < 2s
  2. JSON 文件（降级）— 主程序 2s 写入一次，用于 ZMQ 不可用时

控制命令策略：
  1. ZMQ REQ/REP（首选）— 主程序即时确认，无需等待轮询周期
  2. control_signal.json（降级）— 主程序 1s 轮询（仅 ZMQ 失败时使用）

全部 try/except，不崩溃。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from apps.core.runtime_paths import mobile_state_dir as _mobile_state_dir
from apps.web_dashboard.ipc import zmq_bridge

# 通过 runtime_paths 解析，PyInstaller 打包和开发模式均正确指向项目根目录。
_STATE_DIR: Path = _mobile_state_dir()

# 启动 ZMQ 订阅线程（幂等，进程内只启动一次）
zmq_bridge.start_subscriber()


# ---------------------------------------------------------------------------
# 内部工具：JSON 文件降级读取
# ---------------------------------------------------------------------------

def _read_json(filename: str, default: Any) -> Any:
    f = _STATE_DIR / filename
    if not f.exists():
        return default
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 公开接口（状态读取）
# ---------------------------------------------------------------------------

def read_overview() -> dict:
    """读取总览数据（优先 ZMQ 缓存，降级读文件）。"""
    _default: dict = {
        "total_today": 0,
        "active_devices": 0,
        "error_devices": 0,
        "paused": False,
        "updated_at": "—",
    }
    if zmq_bridge.has_live_data():
        cached = zmq_bridge.get_cached("overview")
        if cached:
            return cached
    return _read_json("overview.json", _default)


def read_devices() -> list[dict]:
    """读取所有设备状态（优先 ZMQ 缓存，降级读文件）。"""
    if zmq_bridge.has_live_data():
        cached = zmq_bridge.get_cached("devices")
        if cached is not None:
            return cached
    return _read_json("devices.json", [])


def read_recent_msgs(limit: int = 50) -> list[dict]:
    """读取最近消息流（优先 ZMQ 缓存，降级读文件）。"""
    if zmq_bridge.has_live_data():
        cached = zmq_bridge.get_cached("recent_msgs")
        if cached is not None:
            data = cached
        else:
            data = _read_json("recent_msgs.json", [])
    else:
        data = _read_json("recent_msgs.json", [])
    return data[-limit:] if len(data) > limit else data


# ---------------------------------------------------------------------------
# 公开接口（控制命令）
# ---------------------------------------------------------------------------

def write_control_signal(action: str) -> None:
    """向主程序发送控制命令（pause_all | resume_all | none）。

    优先通过 ZMQ REQ/REP 发送（即时确认）；
    ZMQ 失败时写入 control_signal.json（主程序 1s 内轮询执行）。
    """
    # 优先 ZMQ
    ok = zmq_bridge.send_control(action)
    if ok:
        return
    # ZMQ 不可用或主程序未启动 → 文件降级
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        (_STATE_DIR / "control_signal.json").write_text(
            json.dumps({"action": action}), encoding="utf-8"
        )
    except Exception:
        pass
