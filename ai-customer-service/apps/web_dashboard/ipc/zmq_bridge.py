"""
apps/web_dashboard/ipc/zmq_bridge.py
======================================
ZeroMQ 桥接：订阅主程序的 PUB 状态广播，维护内存缓存；
通过 REQ/REP 发送控制命令（紧急暂停/恢复）。

如果 pyzmq 未安装或主程序未启动，自动退化到文件 JSON 模式（由 state_reader 处理）。

地址约定（与 mobile_tab.py 的 _ZmqIpcServer 保持一致）：
  PUB (主程序广播) : tcp://127.0.0.1:5556
  REP (主程序接令) : tcp://127.0.0.1:5557
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

_log = logging.getLogger("apps.web_dashboard.zmq")

ZMQ_PUB_ADDR = "tcp://127.0.0.1:5556"
ZMQ_REP_ADDR = "tcp://127.0.0.1:5557"

try:
    import zmq as _zmq  # type: ignore[import]
    _ZMQ_AVAILABLE = True
except ImportError:
    _zmq = None  # type: ignore[assignment]
    _ZMQ_AVAILABLE = False

# 内存缓存：键为状态名（"overview" / "devices" / "recent_msgs"）
_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()
_subscriber_started = False
_subscriber_lock    = threading.Lock()


# ---------------------------------------------------------------------------
# 后台订阅线程
# ---------------------------------------------------------------------------

def _subscribe_loop() -> None:
    """后台线程：不断接收 PUB 广播并更新 _cache。"""
    if not _ZMQ_AVAILABLE:
        return
    ctx = _zmq.Context.instance()
    sub = ctx.socket(_zmq.SUB)
    sub.setsockopt(_zmq.SUBSCRIBE, b"")      # 订阅所有 topic
    sub.setsockopt(_zmq.RCVTIMEO, 5_000)     # 5s 超时，避免永久阻塞
    sub.connect(ZMQ_PUB_ADDR)
    _log.info("ZMQ 订阅已连接: %s", ZMQ_PUB_ADDR)
    while True:
        try:
            raw  = sub.recv()
            data = json.loads(raw.decode("utf-8"))
            with _cache_lock:
                _cache.update(data)
        except _zmq.Again:
            pass   # recv 超时，继续等待
        except _zmq.ZMQError as exc:
            _log.warning("ZMQ 订阅错误，退出循环: %r", exc)
            break
        except Exception as exc:
            _log.debug("ZMQ 数据解析异常（跳过）: %r", exc)
    try:
        sub.close()
    except Exception:
        pass


def start_subscriber() -> None:
    """启动后台 ZMQ 订阅线程（幂等，多次调用安全）。"""
    global _subscriber_started
    with _subscriber_lock:
        if _subscriber_started or not _ZMQ_AVAILABLE:
            return
        _subscriber_started = True
    t = threading.Thread(
        target=_subscribe_loop,
        daemon=True,
        name="ZmqStateSubscriber",
    )
    t.start()


# ---------------------------------------------------------------------------
# 读取缓存
# ---------------------------------------------------------------------------

def get_cached(key: str, default: Any = None) -> Any:
    """获取最新缓存值，无缓存时返回 default。"""
    with _cache_lock:
        return _cache.get(key, default)


def has_live_data() -> bool:
    """判断是否已接收到至少一次 ZMQ 广播（用于降级判断）。"""
    with _cache_lock:
        return bool(_cache)


# ---------------------------------------------------------------------------
# 控制命令（REQ/REP）
# ---------------------------------------------------------------------------

def send_control(action: str, timeout_ms: int = 3_000) -> bool:
    """向主程序发送控制命令（pause_all / resume_all）。

    Returns:
        True  — 主程序已确认执行
        False — ZMQ 不可用 / 超时 / 连接失败（由调用方决定是否退化到文件模式）
    """
    if not _ZMQ_AVAILABLE:
        return False
    ctx = _zmq.Context.instance()
    req = ctx.socket(_zmq.REQ)
    req.setsockopt(_zmq.LINGER, 0)
    req.setsockopt(_zmq.RCVTIMEO, timeout_ms)
    req.setsockopt(_zmq.SNDTIMEO, timeout_ms)
    try:
        req.connect(ZMQ_REP_ADDR)
        req.send(json.dumps({"action": action}).encode("utf-8"))
        raw   = req.recv()
        reply = json.loads(raw.decode("utf-8"))
        return bool(reply.get("ok", False))
    except Exception as exc:
        _log.warning("send_control 失败 action=%r: %r", action, exc)
        return False
    finally:
        req.close()
