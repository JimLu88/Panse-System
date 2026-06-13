# -*- coding: utf-8 -*-
"""产品图纸绘图子程序 (build123d, 独立部署) 的 HTTP 客户端 —— ERP 侧"留好的接口"。

集成形态 (见 docs/绘图子程序_交接方案.md §5): 绘图子程序是独立进程/端口、独立界面,
用 build123d 出 等距线框尺寸图 / 爆炸图 / DXF。ERP 经 HTTP 调它取图。

⚠️ 子程序本身在另一个会话单独搭建、放 D 盘 AI 文件夹, **不在本仓库**。本文件只是 ERP 侧客户端桩:
- 未配置 (DRAWING_SERVICE_URL 为空) 或子程序离线时, 所有调用返回 None / ok=False,
  **绝不抛异常**拖垮下单图/产品页等调用方 (优雅降级)。
- 调用方 (下单图嵌入 / 产品页"生成图纸" / 定制加图) 在子程序上线后由任务 I② 接入。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import requests
from sqlalchemy.orm import Session

from app.services import settings_service

_log = logging.getLogger("panse.drawing")

# 未配置 = 功能关闭 (no-op)。生产指向子程序, 如 http://<PC局域网IP>:8600
BASE_URL = os.environ.get("DRAWING_SERVICE_URL", "").rstrip("/")
_TIMEOUT = 30
TOKEN_KEY = "drawing_service_token"   # 可选 bearer, 存 system_settings (write-only)


def is_configured() -> bool:
    """是否已配置绘图子程序地址。未配置时上层应隐藏"生成图纸"入口、不调用。"""
    return bool(BASE_URL)


def _headers(db: Session) -> dict:
    token = settings_service.get(db, TOKEN_KEY) or ""
    return {"Authorization": f"Bearer {token}"} if token else {}


def health(db: Session) -> dict:
    """探活。未配置→{ok:False, configured:False};在线→{ok:True, configured:True}。"""
    if not BASE_URL:
        return {"ok": False, "configured": False, "error": "未配置 DRAWING_SERVICE_URL"}
    try:
        r = requests.get(f"{BASE_URL}/api/health", headers=_headers(db), timeout=8)
        return {"ok": r.ok, "configured": True, "status": r.status_code}
    except Exception as e:  # noqa: BLE001 - 离线是常态, 不抛
        return {"ok": False, "configured": True, "error": f"{type(e).__name__}: {e}"}


def _render(db: Session, path: str, payload: dict, *, timeout: int = _TIMEOUT) -> Optional[bytes]:
    """POST 到子程序渲染端点, 返回图字节;未配置/失败 → None (不抛)。"""
    if not BASE_URL:
        return None
    try:
        r = requests.post(f"{BASE_URL}{path}", headers=_headers(db), json=payload, timeout=timeout)
        if r.status_code == 200 and r.content:
            return r.content
        _log.warning("绘图子程序 %s 返回 %s", path, r.status_code)
        return None
    except Exception as e:  # noqa: BLE001
        _log.warning("绘图子程序调用失败 %s: %s", path, e)
        return None


def render_product_drawing(
    db: Session, product_code: str, *,
    fmt: str = "png",                        # png | svg | dxf
    dims_override: Optional[dict] = None,    # 定制: {w,d,h} 覆盖
    changed_material: Optional[str] = None,  # 定制: 变更并高亮的材质块
    custom_note: Optional[str] = None,
) -> Optional[bytes]:
    """按 product_code 取该产品图纸 (子程序按存档配方 + 覆盖项渲染)。

    返回图字节;子程序未配置/离线/无该产品配方 → None (上层据此降级, 不报错)。
    定制订单: 传 dims_override / changed_material 让子程序重算比例并高亮变更材质块。
    """
    if not product_code:
        return None
    payload: dict = {"product_code": product_code, "format": fmt}
    if dims_override:
        payload["dims_override"] = dims_override
    if changed_material:
        payload["changed_material"] = changed_material
    if custom_note:
        payload["custom_note"] = custom_note
    return _render(db, "/api/render-by-product", payload)


def render_recipe(db: Session, recipe: dict, *, fmt: str = "png") -> Optional[bytes]:
    """直接传结构配方渲染 (临时预览/调试用)。返回图字节或 None。"""
    if not recipe:
        return None
    return _render(db, "/api/render", {"recipe": recipe, "format": fmt})
