"""
畔色客户对话 CSV 日志：线程安全追加 + 会话内短期上下文（供路由 / 重写）。

列：时间戳, 客户ID/昵称, 发送方(客户/AI/人工), 原始消息, AI识别出的意图标签, 匹配到的话术节点
"""

from __future__ import annotations

import csv
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import ClassVar

from apps.core.runtime_paths import default_panse_customer_chat_log_csv

_CSV_HEADER = (
    "时间戳",
    "客户ID/昵称",
    "发送方(客户/AI/人工)",
    "原始消息",
    "AI识别出的意图标签",
    "匹配到的话术节点",
)


class PanseCustomerChatLog:
    """单进程内多线程安全；跨进程并发写 CSV 仍可能交错，桌面端主进程单写队列可接受。"""

    _instances: ClassVar[dict[str, "PanseCustomerChatLog"]] = {}
    _instances_lock = threading.Lock()

    def __init__(self, csv_path: Path) -> None:
        self._csv_path = Path(csv_path)
        self._write_lock = threading.Lock()
        self._buffers: dict[str, deque[tuple[str, str]]] = defaultdict(
            lambda: deque(maxlen=48)
        )

    @classmethod
    def for_path(cls, csv_path: Path) -> "PanseCustomerChatLog":
        key = str(Path(csv_path).resolve())
        with cls._instances_lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = cls(csv_path)
                cls._instances[key] = inst
            return inst

    def recent_context_text(self, session_id: str, *, max_lines: int = 14) -> str:
        buf = self._buffers.get(session_id)
        if not buf:
            return "（暂无近期上文）"
        lines: list[str] = []
        for role, text in list(buf)[-max_lines:]:
            t = (text or "").strip()
            if not t:
                continue
            lines.append(f"{role}：{t}")
        return "\n".join(lines) if lines else "（暂无近期上文）"

    def remember_turn(self, session_id: str, *, role: str, text: str) -> None:
        """仅更新内存环，不写盘（写盘走 append_row）。"""
        role = (role or "").strip() or "?"
        self._buffers[session_id].append((role, (text or "").strip()))

    def append_row(
        self,
        *,
        session_id: str,
        customer_label: str,
        sender: str,
        raw_message: str,
        intent_label: str,
        kb_node: str,
    ) -> None:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        path = self._csv_path
        path.parent.mkdir(parents=True, exist_ok=True)
        row = [ts, customer_label, sender, raw_message, intent_label, kb_node]
        sid = (session_id or "").strip() or "_unknown"
        with self._write_lock:
            new_file = not path.is_file() or path.stat().st_size == 0
            # utf-8-sig：Excel 直接双击打开时中文列正常
            enc = "utf-8-sig"
            with path.open("a", encoding=enc, newline="") as fp:
                w = csv.writer(fp)
                if new_file:
                    w.writerow(list(_CSV_HEADER))
                w.writerow(row)
            # 同步进上下文缓冲（按会话，而非展示昵称）
            if sender.strip() in ("客户",):
                self.remember_turn(sid, role="客户", text=raw_message)
            elif sender.strip() in ("AI", "人工"):
                self.remember_turn(sid, role=sender.strip(), text=raw_message)


def get_panse_customer_chat_log() -> PanseCustomerChatLog:
    return PanseCustomerChatLog.for_path(default_panse_customer_chat_log_csv())
