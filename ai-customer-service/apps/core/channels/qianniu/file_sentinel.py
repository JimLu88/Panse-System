"""
文件哨兵：轮询千牛 UnReplyedConversation.json 文件的修改时间。
文件变为非空（有未回复会话）时触发 OCR 流水线。

与音频触发、视觉哨兵并列工作，互为兜底：
- 音频触发：依赖系统音量，静音/耳机拔出可能漏检
- 视觉哨兵：依赖屏幕黄条像素，千牛最小化时失效
- 文件哨兵：纯文件 IO，任何情况下均有效（只要千牛在运行）
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

OnTrigger = Callable[[], None]
LogFn = Callable[[str], None]

_DEFAULT_DATA_ROOT = Path(r"D:\AliWorkbenchData")


class QianniuFileSentinel:
    """
    监听 ``<data_root>/NewAppData/*#3/recept/UnReplyedConversation.json``。
    文件 mtime 变化 + 内容非空时触发 ``on_trigger``，冷却内不重复触发。
    """

    def __init__(
        self,
        *,
        data_root: Path | str | None = None,
        on_trigger: OnTrigger,
        log: LogFn,
        cooldown_s: float = 8.0,
        poll_s: float = 0.5,
    ) -> None:
        self._data_root = Path(data_root) if data_root else _DEFAULT_DATA_ROOT
        self._on_trigger = on_trigger
        self._log = log
        self._cooldown = max(1.0, float(cooldown_s))
        self._poll = max(0.2, float(poll_s))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._file_mtimes: dict[str, float] = {}
        self._last_fire = 0.0

    def _discover_files(self) -> list[Path]:
        """自动发现所有账号的 UnReplyedConversation.json。"""
        base = self._data_root / "NewAppData"
        if not base.is_dir():
            return []
        return [
            p for p in base.glob("*#*/recept/UnReplyedConversation.json")
            if p.is_file()
        ]

    @staticmethod
    def _is_nonempty(path: Path) -> bool:
        """内容非空（含有 key）则返回 True；读取失败视为空。"""
        try:
            return bool(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return False

    def _loop(self) -> None:
        warned_no_file = False
        while not self._stop.wait(self._poll):
            files = self._discover_files()
            if not files:
                if not warned_no_file:
                    self._log(
                        f"[文件哨兵] 未在 {self._data_root} 找到监听文件，"
                        "将持续重试（确认千牛已运行且 AliWorkbenchData 路径正确）"
                    )
                    warned_no_file = True
                continue
            warned_no_file = False

            now = time.monotonic()
            for f in files:
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue
                prev = self._file_mtimes.get(str(f), 0.0)
                if mtime <= prev:
                    continue
                self._file_mtimes[str(f)] = mtime
                if self._is_nonempty(f) and (now - self._last_fire) >= self._cooldown:
                    self._last_fire = now
                    acct = f.parts[-3]  # e.g. "27219251#3"
                    self._log(f"[文件哨兵] 账号 {acct} 有未回复买家消息，触发接待流程")
                    try:
                        self._on_trigger()
                    except Exception as exc:
                        self._log(f"[文件哨兵] 触发回调异常：{exc}")

    def start(self) -> None:
        """启动哨兵线程，初始化 mtime 快照避免启动误触发。"""
        for f in self._discover_files():
            try:
                self._file_mtimes[str(f)] = f.stat().st_mtime
            except OSError:
                pass
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="QianniuFileSentinel"
        )
        self._thread.start()
        self._log(
            f"[文件哨兵] 已启动，监控：{self._data_root / 'NewAppData'}，"
            f"轮询 {self._poll}s，冷却 {self._cooldown}s"
        )

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
