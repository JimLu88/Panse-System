"""
apps/mobile/orchestrator/shadow_mode.py
=========================================
影子模式适配器。

包装真实 QianniuAdapter，拦截 send_text 为 no-op：
  - 照常采集消息、走 brain / LLM / 话术路由
  - 但不真实发送，仅将"假如发了会发什么"写入
    data/mobile_state/shadow_replies.jsonl

用法：
    real_adapter = MobileQianniuAdapter(...)
    shadow       = ShadowAdapter(real_adapter)
    bridge       = MobileBrainBridge(adapter=shadow, ...)
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from apps.core.runtime_paths import shadow_replies_path as _shadow_replies_path
from apps.mobile.adapter.base import Message, QianniuAdapter, Session

_log = logging.getLogger("apps.mobile.shadow")

# 通过 runtime_paths 解析，PyInstaller 打包和开发模式均正确指向项目根目录。
# 测试时可用 patch.object(sm, "_SHADOW_FILE", fake_path) 覆盖。
_SHADOW_FILE: Path = _shadow_replies_path()

# 单文件最大体积（字节）— 达到后 rotate 到 .1，避免无限增长。
_MAX_BYTES = 10 * 1024 * 1024   # 10 MB


def _rotate_if_too_large(path: Path) -> None:
    """文件大小超过 _MAX_BYTES 时，rename → path.1（覆盖旧的 .1）。失败静默。"""
    try:
        if not path.exists() or path.stat().st_size < _MAX_BYTES:
            return
        backup = path.with_suffix(path.suffix + ".1")
        if backup.exists():
            backup.unlink()
        path.rename(backup)
        _log.info("影子记录已 rotate: %s → %s", path.name, backup.name)
    except Exception as exc:
        _log.debug("rotate 失败（忽略）: %r", exc)


class ShadowAdapter(QianniuAdapter):
    """
    代理真实 Adapter，拦截 send_text：

    - 所有读取操作（list_sessions / read_messages / get_anchor / screenshot）
      原样转发给真实 Adapter，保证采集链路完整。
    - send_text 替换为 no-op + 写 JSONL 日志。
    - 返回 True 让调用方认为发送成功，brain 不会重试。
    """

    def __init__(self, real_adapter: QianniuAdapter) -> None:
        self._real = real_adapter

    @property
    def device_id(self) -> str:
        return self._real.device_id

    def connect(self) -> bool:
        return self._real.connect()

    def disconnect(self) -> None:
        self._real.disconnect()

    def is_alive(self) -> bool:
        return self._real.is_alive()

    def list_unread_sessions(self) -> list[Session]:
        return self._real.list_unread_sessions()

    def list_all_sessions(self, limit: int = 10) -> list[Session]:
        return self._real.list_all_sessions(limit)

    def switch_to_session(self, session: Session) -> bool:
        return self._real.switch_to_session(session)

    def read_latest_messages(self, limit: int = 10) -> list[Message]:
        return self._real.read_latest_messages(limit)

    def get_current_buyer_anchor(self) -> str:
        return self._real.get_current_buyer_anchor()

    def start_notification_listener(self, callback: Any) -> None:
        self._real.start_notification_listener(callback)

    def screenshot_bytes(self) -> bytes | None:
        fn = getattr(self._real, "screenshot_bytes", None)
        return fn() if fn else None

    # ── 核心拦截 ──────────────────────────────────────────────────────────

    def send_text(self, text: str) -> bool:
        """拦截发送：记录到 JSONL，不真实发送。

        长期运行时 JSONL 文件会无限增长，因此达到 10MB 时自动 rotate
        （shadow_replies.jsonl → shadow_replies.jsonl.1），保留 1 份历史。
        """
        record = {
            "ts":        time.strftime("%Y-%m-%dT%H:%M:%S"),
            "device_id": self.device_id,
            "text":      text,
            "sent":      False,
            "shadow":    True,
        }
        try:
            _SHADOW_FILE.parent.mkdir(parents=True, exist_ok=True)
            _rotate_if_too_large(_SHADOW_FILE)
            with _SHADOW_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            _log.warning("影子记录写入失败: %r", exc)

        _log.info("[SHADOW] 拦截 send_text device=%s text=%.60r", self.device_id, text)
        return True   # 让调用方认为发送成功
