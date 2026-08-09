"""采购执行器主循环。

默认 dry_run，只预览 ERP 队列。要进入 review/live，必须在 Windows 本机配置；
ERP 页面或远程 API 不能把执行器从 dry_run 提权到 live。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .client import AgentApiError, ProcurementApiClient
from .drivers import DiscoveryResult, ExternalCommandDriver, PlatformDriver, SendResult

LIVE_ACK = "I_UNDERSTAND_MESSAGES_WILL_BE_SENT"


def _load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("配置文件必须是 JSON 对象")
    return data


def build_drivers(config: dict[str, Any]) -> dict[str, PlatformDriver]:
    result: dict[str, PlatformDriver] = {}
    for capability, item in (config.get("drivers") or {}).items():
        if not isinstance(item, dict) or not item.get("command"):
            continue
        result[capability] = ExternalCommandDriver(
            capability,
            item["command"],
            timeout_seconds=int(item.get("timeout_seconds") or 120),
        )
    return result


class ProcurementAgent:
    def __init__(
        self,
        *,
        client: ProcurementApiClient,
        agent_id: str,
        display_name: str,
        mode: str,
        drivers: dict[str, PlatformDriver],
        declared_capabilities: Optional[list[str]] = None,
        host_label: Optional[str] = None,
        max_actions: int = 1,
        lease_seconds: int = 180,
    ) -> None:
        if mode not in {"dry_run", "review", "live"}:
            raise ValueError("mode 必须是 dry_run、review 或 live")
        self.client = client
        self.agent_id = agent_id
        self.display_name = display_name
        self.mode = mode
        self.drivers = drivers
        self.declared_capabilities = set(declared_capabilities or [])
        self.host_label = host_label or socket.gethostname()
        self.max_actions = max(1, min(max_actions, 10))
        self.lease_seconds = max(60, min(lease_seconds, 900))
        self.counters = {
            "discovered": 0,
            "sent": 0,
            "manual": 0,
            "failed": 0,
            "replies": 0,
        }

    @property
    def capabilities(self) -> list[str]:
        return sorted(set(self.drivers) | self.declared_capabilities)

    def _claim_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "mode": self.mode,
            "capabilities": self.capabilities,
            "max_actions": self.max_actions,
            "lease_seconds": self.lease_seconds,
        }

    def heartbeat(
        self,
        *,
        status: str = "online",
        current_inquiry_id: Optional[int] = None,
        last_error: Optional[str] = None,
    ) -> dict[str, Any]:
        return self.client.heartbeat(
            {
                "agent_id": self.agent_id,
                "display_name": self.display_name,
                "host_label": self.host_label,
                "version": __version__,
                "mode": self.mode,
                "status": status,
                "capabilities": self.capabilities,
                "current_inquiry_id": current_inquiry_id,
                "last_error": last_error,
                "counters": self.counters,
            }
        )

    def _handle_result(
        self, action: dict[str, Any], result: SendResult
    ) -> None:
        inquiry_id = int(action["inquiry_id"])
        base = {
            "agent_id": self.agent_id,
            "lease_token": action["lease_token"],
        }
        if result.outcome == "sent":
            if not result.external_message_id:
                raise RuntimeError("驱动报告 sent 但缺少 external_message_id")
            self.client.confirm_sent(
                inquiry_id,
                {
                    **base,
                    "content": result.sent_content or action["suggested_message"],
                    "external_message_id": result.external_message_id,
                    "external_thread_id": result.external_thread_id,
                },
            )
            self.counters["sent"] += 1
        elif result.outcome == "manual":
            self.client.manual_handoff(
                inquiry_id,
                {**base, "reason": result.reason or "平台驱动请求人工接管"},
            )
            self.counters["manual"] += 1
        else:
            self.client.report_failure(
                inquiry_id,
                {
                    **base,
                    "error": result.reason or "平台驱动发送失败",
                    "retryable": result.retryable,
                },
            )
            self.counters["failed"] += 1

    def process_actions_once(self) -> list[dict[str, Any]]:
        response = self.client.claim(self._claim_payload())
        actions = response.get("actions") or []
        if self.mode == "dry_run":
            for action in actions:
                print(
                    json.dumps(
                        {
                            "preview": True,
                            "inquiry_id": action.get("inquiry_id"),
                            "channel": action.get("channel"),
                            "merchant": action.get("merchant_name"),
                            "message": action.get("suggested_message"),
                        },
                        ensure_ascii=False,
                    )
                )
            return actions
        for action in actions:
            capability = action["required_capability"]
            driver = self.drivers.get(capability)
            if driver is None:
                # 理论上 claim 已按能力过滤；仍兜底转人工，绝不让租约悬空。
                self.client.manual_handoff(
                    int(action["inquiry_id"]),
                    {
                        "agent_id": self.agent_id,
                        "lease_token": action["lease_token"],
                        "reason": f"本机缺少驱动 {capability}",
                    },
                )
                continue
            self.heartbeat(status="busy", current_inquiry_id=int(action["inquiry_id"]))
            try:
                self._handle_result(action, driver.send(action, mode=self.mode))
            except Exception as exc:  # 驱动异常需要释放租约并可审计
                self.client.report_failure(
                    int(action["inquiry_id"]),
                    {
                        "agent_id": self.agent_id,
                        "lease_token": action["lease_token"],
                        "error": f"{type(exc).__name__}: {exc}",
                        "retryable": True,
                    },
                )
                self.counters["failed"] += 1
        return actions

    def _handle_discovery_result(
        self, action: dict[str, Any], result: DiscoveryResult
    ) -> None:
        inquiry_id = int(action["inquiry_id"])
        base = {
            "agent_id": self.agent_id,
            "lease_token": action["lease_token"],
        }
        if result.outcome == "found":
            response = self.client.report_candidate(
                inquiry_id,
                {
                    **base,
                    "merchant_name": result.merchant_name,
                    "merchant_url": result.merchant_url,
                    "product_url": result.product_url,
                    "merchant_external_id": result.merchant_external_id,
                    "discovery_query": result.discovery_query,
                    "candidate_score": result.candidate_score,
                    "candidate_reason": result.candidate_reason,
                    "candidate_snapshot": result.candidate_snapshot,
                    "source_rank": result.source_rank,
                },
            )
            if not response.get("duplicate"):
                self.counters["discovered"] += 1
        elif result.outcome == "manual":
            self.client.report_discovery_failure(
                inquiry_id,
                {
                    **base,
                    "error": result.reason or "平台搜索需要人工接管",
                    "retryable": False,
                },
            )
            self.counters["manual"] += 1
        else:
            self.client.report_discovery_failure(
                inquiry_id,
                {
                    **base,
                    "error": result.reason or "平台候选搜索失败",
                    "retryable": result.retryable,
                },
            )
            self.counters["failed"] += 1

    def process_discoveries_once(self) -> list[dict[str, Any]]:
        response = self.client.claim_discovery(self._claim_payload())
        actions = response.get("actions") or []
        if self.mode == "dry_run":
            for action in actions:
                print(
                    json.dumps(
                        {
                            "preview": True,
                            "operation": "discover",
                            "inquiry_id": action.get("inquiry_id"),
                            "channel": action.get("channel"),
                            "search_query": action.get("search_query"),
                        },
                        ensure_ascii=False,
                    )
                )
            return actions
        for action in actions:
            capability = action["required_capability"]
            driver = self.drivers.get(capability)
            if driver is None:
                self.client.report_discovery_failure(
                    int(action["inquiry_id"]),
                    {
                        "agent_id": self.agent_id,
                        "lease_token": action["lease_token"],
                        "error": f"本机缺少搜索驱动 {capability}",
                        "retryable": False,
                    },
                )
                continue
            self.heartbeat(status="busy", current_inquiry_id=int(action["inquiry_id"]))
            try:
                self._handle_discovery_result(
                    action, driver.discover(action, mode=self.mode)
                )
            except Exception as exc:
                self.client.report_discovery_failure(
                    int(action["inquiry_id"]),
                    {
                        "agent_id": self.agent_id,
                        "lease_token": action["lease_token"],
                        "error": f"{type(exc).__name__}: {exc}",
                        "retryable": True,
                    },
                )
                self.counters["failed"] += 1
        return actions

    def poll_replies_once(self) -> int:
        response = self.client.watch(self.capabilities)
        conversations = response.get("conversations") or []
        count = 0
        for capability, driver in self.drivers.items():
            subset = [
                item
                for item in conversations
                if item.get("required_capability") == capability
            ]
            try:
                replies = driver.poll_replies(subset)
            except Exception as exc:
                self.heartbeat(status="error", last_error=f"{capability}: {exc}")
                continue
            for reply in replies:
                self.client.report_reply(
                    reply.inquiry_id,
                    {
                        "agent_id": self.agent_id,
                        "content": reply.content,
                        "external_message_id": reply.external_message_id,
                        "received_at": reply.received_at,
                        "quote_complete": reply.quote_complete,
                        "quote_amount": reply.quote_amount,
                        "normalized_unit_price": reply.normalized_unit_price,
                        "quote_payload": reply.quote_payload,
                        "response_quality": reply.response_quality,
                        "wechat_contact": reply.wechat_contact,
                    },
                )
                count += 1
        self.counters["replies"] += count
        return count

    def run_once(self) -> None:
        """单轮顺序固定为先收回复，再搜索候选，最后才领取待发消息。"""
        self.heartbeat()
        self.poll_replies_once()
        self.process_discoveries_once()
        self.process_actions_once()
        self.heartbeat()

    def run_forever(self, *, poll_seconds: int = 30) -> None:
        poll_seconds = max(10, min(poll_seconds, 300))
        while True:
            try:
                self.run_once()
            except AgentApiError as exc:
                print(str(exc), file=sys.stderr)
            time.sleep(poll_seconds)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Panse ERP 采购桌面执行器")
    parser.add_argument("--config", required=True, help="本机 JSON 配置路径")
    parser.add_argument("--once", action="store_true", help="只跑一轮后退出")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    config = _load_config(Path(args.config).resolve())
    token = os.environ.get("PROCUREMENT_AGENT_TOKEN", "").strip()
    if not token:
        raise SystemExit("请在本机环境变量 PROCUREMENT_AGENT_TOKEN 中配置令牌")
    mode = str(config.get("mode") or "dry_run")
    if mode == "live" and os.environ.get("PROCUREMENT_AGENT_LIVE_ACK") != LIVE_ACK:
        raise SystemExit(
            "live 模式未获本机确认；请显式设置 PROCUREMENT_AGENT_LIVE_ACK="
            f"{LIVE_ACK}"
        )
    drivers = build_drivers(config)
    if mode != "dry_run" and not drivers:
        raise SystemExit("review/live 模式至少要配置一个平台驱动")
    agent = ProcurementAgent(
        client=ProcurementApiClient(
            str(config.get("erp_base_url") or "http://127.0.0.1:8000"),
            token,
        ),
        agent_id=str(config.get("agent_id") or socket.gethostname()),
        display_name=str(config.get("display_name") or "采购执行器"),
        mode=mode,
        drivers=drivers,
        declared_capabilities=config.get("capabilities") or [],
        host_label=config.get("host_label"),
        max_actions=int(config.get("max_actions") or 1),
        lease_seconds=int(config.get("lease_seconds") or 180),
    )
    if args.once:
        agent.run_once()
        return 0
    agent.run_forever(poll_seconds=int(config.get("poll_seconds") or 30))
    return 0

