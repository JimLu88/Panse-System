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
    def __init__(self, base_url: str, token: str, *, timeout: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

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
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AgentApiError(f"ERP HTTP {exc.code}: {detail[:500]}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise AgentApiError(f"ERP 请求失败: {type(exc).__name__}: {exc}") from exc

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

