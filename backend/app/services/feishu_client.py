"""飞书开放平台 HTTP 客户端 (多维表格 Bitable).

封装:
    - tenant_access_token 获取 + 进程内缓存 (~2h 有效)
    - Bitable 记录 CRUD (list / create / update / delete)

凭证 (app_id / app_secret) 存 system_settings, 后台可改。
失败统一抛 FeishuError, 上层 (feishu_sync_service) 捕获并写冲突/告警。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests
from sqlalchemy.orm import Session

from app.services import settings_service

_logger = logging.getLogger("panse.feishu")

_BASE = "https://open.feishu.cn/open-apis"
_TIMEOUT = 15

# 进程内 token 缓存: app_id -> (token, expire_ts)
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class FeishuError(RuntimeError):
    """飞书 API 调用失败 (凭证错 / 网络错 / 业务码非 0)."""


def get_credentials(db: Session) -> tuple[str, str]:
    app_id = settings_service.get(db, "feishu_app_id", env_fallback=False)
    app_secret = settings_service.get(db, "feishu_app_secret", env_fallback=False)
    if not app_id or not app_secret:
        raise FeishuError("飞书未配置: 请到 管理 → 飞书 填 app_id / app_secret")
    return app_id, app_secret


def get_tenant_access_token(db: Session, *, force: bool = False) -> str:
    app_id, app_secret = get_credentials(db)
    now = time.time()
    cached = _TOKEN_CACHE.get(app_id)
    if cached and not force and cached[1] > now + 60:
        return cached[0]
    try:
        r = requests.post(
            f"{_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as e:
        raise FeishuError(f"获取飞书 token 网络失败: {e}") from e
    data = _json(r)
    if data.get("code") != 0:
        raise FeishuError(f"获取飞书 token 失败: {data.get('msg')} (code={data.get('code')})")
    token = data["tenant_access_token"]
    _TOKEN_CACHE[app_id] = (token, now + int(data.get("expire", 7200)))
    return token


def _json(r: requests.Response) -> dict:
    try:
        return r.json()
    except ValueError as e:
        raise FeishuError(f"飞书返回非 JSON (HTTP {r.status_code}): {r.text[:200]}") from e


def _headers(db: Session) -> dict:
    return {"Authorization": f"Bearer {get_tenant_access_token(db)}",
            "Content-Type": "application/json; charset=utf-8"}


def _req(db: Session, method: str, url: str, **kwargs) -> dict:
    try:
        r = requests.request(method, url, headers=_headers(db), timeout=_TIMEOUT, **kwargs)
    except requests.RequestException as e:
        raise FeishuError(f"飞书请求网络失败: {e}") from e
    data = _json(r)
    if data.get("code") != 0:
        raise FeishuError(f"飞书 API 错误: {data.get('msg')} (code={data.get('code')})")
    return data.get("data", {})


def list_records(db: Session, app_token: str, table_id: str,
                 *, page_size: int = 500) -> list[dict]:
    """拉取一张 Bitable 表的全部记录 (自动翻页).

    返回 [{record_id, fields, last_modified_time?}, ...]。
    """
    out: list[dict] = []
    page_token: Optional[str] = None
    base = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    while True:
        params: dict[str, Any] = {"page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        data = _req(db, "GET", base, params=params)
        for item in data.get("items", []) or []:
            out.append({
                "record_id": item.get("record_id"),
                "fields": item.get("fields", {}),
                "last_modified_time": item.get("last_modified_time"),
            })
        if not data.get("has_more"):
            break
        page_token = data.get("page_token")
        if not page_token:
            break
    return out


def create_record(db: Session, app_token: str, table_id: str, fields: dict) -> str:
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    data = _req(db, "POST", url, json={"fields": fields})
    return (data.get("record") or {}).get("record_id", "")


def update_record(db: Session, app_token: str, table_id: str,
                  record_id: str, fields: dict) -> None:
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    _req(db, "PUT", url, json={"fields": fields})


def delete_record(db: Session, app_token: str, table_id: str, record_id: str) -> None:
    url = f"{_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    _req(db, "DELETE", url)


def test_connection(db: Session) -> dict:
    """后台"测试连接"按钮用: 拿一次 token 即视为通。"""
    try:
        get_tenant_access_token(db, force=True)
        return {"ok": True}
    except FeishuError as e:
        return {"ok": False, "error": str(e)}
