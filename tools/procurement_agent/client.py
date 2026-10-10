"""ERP 采购机器接口客户端，仅使用 Python 标准库。"""
from __future__ import annotations

import json
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AgentApiError(RuntimeError):
    pass


class ProcurementApiClient:
    def __init__(self, base_url: str, token: str, *, timeout: int = 20,
                 executor_id: Optional[str] = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.executor_id = executor_id

    def _request(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={
                "X-API-Key": self.token,
                "Content-Type": "application/json",
                "User-Agent": "Panse-Procurement-Agent/0.1",
                **({"X-Procurement-Executor": self.executor_id} if self.executor_id else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            # Error bodies may echo request bodies, message text or permits.
            # Keep them out of terminal/heartbeat logs.
            raise AgentApiError(f"ERP HTTP {exc.code}；请核对服务端脱敏诊断") from None
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AgentApiError(f"ERP 请求失败: {type(exc).__name__}: {exc}") from exc

    def reserve(self, action: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/procurement/agent/dispatch/reserve", {
            **{k: action[k] for k in ("task_id", "inquiry_id", "account_id", "action_key")},
            "content": action["suggested_message"],
        })

    def _dispatch(self, intent_id: str, operation: str, payload=None):
        from uuid import UUID
        # Never interpolate untrusted path fragments from a driver.
        canonical = str(UUID(intent_id))
        return self._request("POST", f"/api/procurement/agent/dispatch/{canonical}/{operation}", payload)

    def permit(self, intent_id: str):
        return self._dispatch(intent_id, "permit")

    def unknown(self, intent_id: str):
        return self._dispatch(intent_id, "unknown")

    def begin_reconcile(self, intent_id: str):
        return self._dispatch(intent_id, "reconcile")

    def unresolved(self, intent_id: str):
        return self._dispatch(intent_id, "unresolved")

    def confirm(self, intent_id: str, receipt: dict, permit: str):
        return self._dispatch(intent_id, "receipt", {**receipt, "permit": permit})

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/procurement/agent/heartbeat", payload)

    def claim(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/procurement/agent/claim", payload)

    def claim_discovery(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST", "/api/procurement/agent/discovery/claim", payload
        )

    def report_candidate(
        self, inquiry_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/procurement/agent/inquiries/{inquiry_id}/candidate",
            payload,
        )

    def report_discovery_failure(
        self, inquiry_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/procurement/agent/inquiries/{inquiry_id}/discovery-failure",
            payload,
        )

    def confirm_sent(
        self, inquiry_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/procurement/agent/inquiries/{inquiry_id}/sent",
            payload,
        )

    def report_failure(
        self, inquiry_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/procurement/agent/inquiries/{inquiry_id}/failure",
            payload,
        )

    def manual_handoff(
        self, inquiry_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/procurement/agent/inquiries/{inquiry_id}/manual",
            payload,
        )

    def watch(self, capabilities: list[str], *, limit: int = 100) -> dict[str, Any]:
        query = urlencode({"limit": limit})
        return self._request(
            "POST",
            f"/api/procurement/agent/watch?{query}",
            {"capabilities": capabilities},
        )

    def report_reply(
        self, inquiry_id: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/procurement/agent/inquiries/{inquiry_id}/reply",
            payload,
        )

