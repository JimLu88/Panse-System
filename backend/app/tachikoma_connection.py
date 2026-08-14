"""Tachikoma connection-only identity and business contract guard.

This module never opens a network connection by itself.  A caller must inject
an explicit transport, which keeps production ERP reads/writes, collection and
external messaging disabled until a separate project approval enables them.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Callable, Literal, Mapping, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict


SERVICE = "panse-system"
SOFTWARE_ID = "panse-system"
VERSION = "1.0.0"
CONTRACT_VERSION = "v1"
MODE = "connection_only"
CONNECTION_ONLY_ENV = "PANSE_TACHIKOMA_CONNECTION_ONLY"
ERP_READ_BASE_URL_ENV = "PANSE_ERP_READ_BASE_URL"
MAX_UPSTREAM_BYTES = 2 * 1024 * 1024
SAFE_CONNECTION_PATHS = frozenset({
    "/api/health",
    "/api/version",
    "/api/tachikoma/product-candidates",
    "/docs",
    "/openapi.json",
})

router = APIRouter(prefix="/api/tachikoma", tags=["tachikoma-connection"])


class ProductCandidate(BaseModel):
    """Least-privilege ERP facts exposed to the local Tachikoma connector."""

    model_config = ConfigDict(extra="forbid")

    product_code: str
    product_name: Optional[str] = None
    sku_code: str
    sku: Optional[str] = None
    daily_price: str


class ProductCandidateList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Literal["panse-system"] = SERVICE
    contract_version: Literal["v1"] = CONTRACT_VERSION
    source: Literal["erp.pricing_sku"] = "erp.pricing_sku"
    read_only: Literal[True] = True
    mutations: Literal[0] = 0
    selection_policy: list[str]
    returned: int
    candidates: list[ProductCandidate]


def _price_text(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def configured_erp_read_base_url(
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    source = os.environ if environment is None else environment
    value = str(source.get(ERP_READ_BASE_URL_ENV, "")).strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConnectionContractError(
            "erp_read_upstream_missing",
            f"{ERP_READ_BASE_URL_ENV} must be an http(s) ERP base URL",
        )
    return value


def _read_upstream_skus(*, product_code: Optional[str], limit: int) -> list[dict[str, Any]]:
    query: dict[str, str | int] = {"limit": limit}
    if product_code:
        query["product_code"] = product_code
    url = (
        configured_erp_read_base_url()
        + "/api/pricing-skus?"
        + urllib.parse.urlencode(query)
    )
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "Tachikoma-ERP-Read/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5.0) as response:
            raw = response.read(MAX_UPSTREAM_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 - return a sanitized local dependency error
        raise ConnectionContractError(
            "erp_read_upstream_unavailable",
            f"ERP read upstream failed: {type(exc).__name__}",
        ) from exc
    if len(raw) > MAX_UPSTREAM_BYTES:
        raise ConnectionContractError(
            "erp_read_upstream_too_large", "ERP read response exceeded the bounded limit"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConnectionContractError(
            "erp_read_upstream_invalid", "ERP read response was not valid UTF-8 JSON"
        ) from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ConnectionContractError(
            "erp_read_upstream_invalid", "ERP read response did not contain an items list"
        )
    return [item for item in items if isinstance(item, dict)]


@router.get("/product-candidates", response_model=ProductCandidateList)
def product_candidates(
    product_code: Optional[str] = Query(None, min_length=1, max_length=32),
    limit: int = Query(20, ge=1, le=50),
) -> ProductCandidateList:
    """Return bounded, non-placeholder SKU facts without costs or campaign data."""

    try:
        rows = _read_upstream_skus(product_code=product_code, limit=limit)
        candidates = [
            ProductCandidate(
                product_code=str(row["product_code"]),
                product_name=(str(row["product_name"]) if row.get("product_name") else None),
                sku_code=str(row["sku_code"]),
                sku=(str(row["sku"]) if row.get("sku") else None),
                daily_price=_price_text(Decimal(str(row["daily_price"]))),
            )
            for row in rows
            if row.get("daily_price") is not None
            and row.get("is_custom_placeholder") is not True
            and row.get("product_code")
            and row.get("sku_code")
        ]
    except (ConnectionContractError, ArithmeticError, ValueError, KeyError) as exc:
        code = exc.code if isinstance(exc, ConnectionContractError) else "erp_read_upstream_invalid"
        raise HTTPException(status_code=503, detail={"code": code, "service": SERVICE}) from exc
    return ProductCandidateList(
        selection_policy=[
            "daily_price_present",
            "custom_placeholder_excluded",
            "bounded_to_50",
            "no_cost_campaign_or_account_fields",
        ],
        returned=len(candidates),
        candidates=candidates,
    )


def connection_only(environment: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if environment is None else environment
    return str(source.get(CONNECTION_ONLY_ENV, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def connection_path_allowed(method: str, path: str) -> bool:
    actual_method = str(method or "").upper()
    if actual_method in {"HEAD", "OPTIONS"}:
        return True
    return actual_method == "GET" and path in SAFE_CONNECTION_PATHS


def configured_port(environment: Optional[Mapping[str, str]] = None) -> int:
    source = os.environ if environment is None else environment
    return int(source.get("PORT", "8000"))


DEFAULT_PORT = configured_port()


@dataclass(frozen=True)
class BusinessAction:
    chain_id: str
    step: str
    method: str
    path: str
    risk: str
    approval: str
    idempotency: str
    status: str
    execution_enabled: bool
    credentials_required: bool = False


ACTIONS: dict[tuple[str, str], BusinessAction] = {
    ("product-content-chain", "product-facts"): BusinessAction(
        chain_id="product-content-chain",
        step="product-facts",
        method="GET",
        path="/api/tachikoma/product-candidates",
        risk="read_only",
        approval="none",
        idempotency="safe_read_repeatable",
        status="read_only_adapter_ready",
        execution_enabled=True,
    ),
    ("commerce-intelligence-chain", "erp"): BusinessAction(
        chain_id="commerce-intelligence-chain",
        step="erp",
        method="GET",
        path="/api/web-agent/status",
        risk="read_only",
        approval="none",
        idempotency="safe_read_repeatable",
        status="read_only_discovered",
        execution_enabled=True,
        credentials_required=True,
    ),
    ("customer-conversation-chain", "rules"): BusinessAction(
        chain_id="customer-conversation-chain",
        step="rules",
        method="GET",
        path="/api/version",
        risk="read_only",
        approval="project_adapter_required",
        idempotency="safe_read_repeatable",
        status="business_query_contract_missing",
        execution_enabled=False,
    ),
}


class ConnectionContractError(RuntimeError):
    """Fail-closed adapter error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def identity_payload(*, dependencies_ready: bool = True) -> dict[str, Any]:
    """Fast, credential-free identity payload for ``GET /api/health``."""
    return {
        "ok": True,
        "service": SERVICE,
        "version": VERSION,
        "contract_version": CONTRACT_VERSION,
        "ready": True,
        "dependencies_ready": bool(dependencies_ready),
        "mode": MODE,
    }


def validate_identity(payload: Mapping[str, Any]) -> None:
    if payload.get("service") != SERVICE:
        raise ConnectionContractError(
            "identity_mismatch",
            f"expected service={SERVICE!r}, got {payload.get('service')!r}",
        )
    if payload.get("contract_version") != CONTRACT_VERSION:
        raise ConnectionContractError(
            "contract_version_mismatch",
            "connection contract version does not match v1",
        )
    if not payload.get("ready"):
        raise ConnectionContractError("service_not_ready", "service is not ready")


def action_contracts() -> list[dict[str, Any]]:
    return [asdict(action) for action in ACTIONS.values()]


def _idempotency_key(action: BusinessAction, payload: Any) -> str:
    material = json.dumps(
        {
            "chain_id": action.chain_id,
            "step": action.step,
            "method": action.method,
            "path": action.path,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


Transport = Callable[..., Any]


def invoke(
    chain_id: str,
    step: str,
    *,
    method: str,
    path: str,
    payload: Any = None,
    credentials: Optional[str] = None,
    approval: Optional[str] = None,
    timeout_seconds: float = 2.0,
    transport: Optional[Transport] = None,
) -> dict[str, Any]:
    """Validate and invoke one contract through an injected test transport.

    No default HTTP transport exists by design.  This makes production calls
    impossible merely by importing the connection layer.
    """
    action = ACTIONS.get((chain_id, step))
    if action is None:
        raise ConnectionContractError("unknown_action", "action is not allowlisted")
    actual_method = str(method or "").upper()
    if actual_method != action.method or path != action.path:
        if actual_method not in {"GET", "HEAD", "OPTIONS"} and not approval:
            raise ConnectionContractError(
                "approval_required", "write-like method requires fresh task approval",
            )
        raise ConnectionContractError("contract_mismatch", "method or path mismatch")
    if action.approval != "none" and approval != action.approval:
        raise ConnectionContractError(
            "approval_required", f"required approval: {action.approval}",
        )
    if not action.execution_enabled:
        raise ConnectionContractError(
            "action_disabled", f"action is disabled: {action.status}",
        )
    if action.credentials_required and not credentials:
        raise ConnectionContractError(
            "credentials_required", "protected read requires configured credentials",
        )
    if transport is None:
        raise ConnectionContractError(
            "production_execution_disabled",
            "no production transport is configured in the connection layer",
        )
    if timeout_seconds <= 0:
        raise ConnectionContractError("invalid_timeout", "timeout must be positive")

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tachikoma-contract")
    future = executor.submit(
        transport,
        method=action.method,
        path=action.path,
        payload=payload,
        credentials=credentials,
        timeout=timeout_seconds,
    )
    try:
        result = future.result(timeout=timeout_seconds)
    except FutureTimeout as exc:
        future.cancel()
        raise ConnectionContractError("timeout", "adapter call timed out") from exc
    except Exception as exc:  # noqa: BLE001 - convert all transport errors to fail-closed result
        raise ConnectionContractError(
            "dependency_failure", f"adapter dependency failed: {type(exc).__name__}",
        ) from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return {
        "service": SERVICE,
        "contract_version": CONTRACT_VERSION,
        "chain_id": action.chain_id,
        "step": action.step,
        "status": "ok",
        "idempotency_key": _idempotency_key(action, payload),
        "result": result,
    }
