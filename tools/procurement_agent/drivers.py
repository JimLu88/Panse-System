"""平台驱动协议。

真正的窗口定位/点击由每个平台独立驱动进程实现。sidecar 通过 JSON stdin/stdout
调用它，ERP 和平台 UI 之间没有共享账号密码或 cookie。
"""
from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class SendResult:
    outcome: str
    # sent / manual / failed
    external_message_id: Optional[str] = None
    external_thread_id: Optional[str] = None
    sent_content: Optional[str] = None
    reason: Optional[str] = None
    retryable: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ObservedReply:
    inquiry_id: int
    external_message_id: str
    content: str
    received_at: Optional[str] = None
    quote_complete: bool = False
    quote_amount: Optional[float] = None
    normalized_unit_price: Optional[float] = None
    response_quality: Optional[int] = None
    wechat_contact: Optional[str] = None
    quote_payload: dict[str, Any] = field(default_factory=dict)


class PlatformDriver(Protocol):
    capability: str

    def send(self, action: dict[str, Any], *, mode: str) -> SendResult:
        ...

    def poll_replies(
        self, conversations: list[dict[str, Any]]
    ) -> list[ObservedReply]:
        ...


class ExternalCommandDriver:
    """调用一个用户明确配置的本地平台驱动。

    命令不经 shell，避免任务内容被解释成命令。驱动必须从 stdin 读取一行 JSON，
    并向 stdout 输出一行 JSON。review 模式应由驱动展示预览并让人工确认；
    live 模式才允许驱动直接发送。
    """

    def __init__(
        self,
        capability: str,
        command: list[str] | str,
        *,
        timeout_seconds: int = 120,
    ) -> None:
        self.capability = capability
        self.command = (
            shlex.split(command, posix=False)
            if isinstance(command, str)
            else list(command)
        )
        if not self.command:
            raise ValueError(f"{capability} 驱动命令为空")
        self.timeout_seconds = max(10, min(int(timeout_seconds), 600))

    def _invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                self.command,
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{self.capability} 驱动超时") from exc
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(
                f"{self.capability} 驱动退出码 {completed.returncode}: {error[:500]}"
            )
        try:
            data = json.loads((completed.stdout or "").strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"{self.capability} 驱动没有返回合法 JSON"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{self.capability} 驱动返回值必须是对象")
        return data

    def send(self, action: dict[str, Any], *, mode: str) -> SendResult:
        data = self._invoke(
            {
                "operation": "send",
                "mode": mode,
                "capability": self.capability,
                "action": action,
            }
        )
        return SendResult(
            outcome=str(data.get("outcome") or "failed"),
            external_message_id=data.get("external_message_id"),
            external_thread_id=data.get("external_thread_id"),
            sent_content=data.get("sent_content"),
            reason=data.get("reason"),
            retryable=bool(data.get("retryable", False)),
            meta=data.get("meta") or {},
        )

    def poll_replies(
        self, conversations: list[dict[str, Any]]
    ) -> list[ObservedReply]:
        if not conversations:
            return []
        data = self._invoke(
            {
                "operation": "poll_replies",
                "capability": self.capability,
                "conversations": conversations,
            }
        )
        replies = []
        for item in data.get("replies") or []:
            replies.append(
                ObservedReply(
                    inquiry_id=int(item["inquiry_id"]),
                    external_message_id=str(item["external_message_id"]),
                    content=str(item["content"]),
                    received_at=item.get("received_at"),
                    quote_complete=bool(item.get("quote_complete", False)),
                    quote_amount=item.get("quote_amount"),
                    normalized_unit_price=item.get("normalized_unit_price"),
                    response_quality=item.get("response_quality"),
                    wechat_contact=item.get("wechat_contact"),
                    quote_payload=item.get("quote_payload") or {},
                )
            )
        return replies


class MockDriver:
    """仅供测试；不会操作任何真实窗口。"""

    def __init__(self, capability: str = "taobao_desktop") -> None:
        self.capability = capability
        self.sent: list[dict[str, Any]] = []

    def send(self, action: dict[str, Any], *, mode: str) -> SendResult:
        self.sent.append(action)
        return SendResult(
            outcome="sent",
            external_message_id=f"mock-out-{action['inquiry_id']}",
            external_thread_id=f"mock-thread-{action['inquiry_id']}",
            sent_content=action["suggested_message"],
            meta={"mock": True},
        )

    def poll_replies(
        self, conversations: list[dict[str, Any]]
    ) -> list[ObservedReply]:
        return []

