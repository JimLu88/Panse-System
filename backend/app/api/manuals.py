# -*- coding: utf-8 -*-
"""产品安装说明书 (用户需求 2026-07-10)。

存放: storage/manuals/{产品编码}/*.pdf — storage 卷在群晖上, 可直接在群晖里增改文件,
      改完刷新页面即生效 (本接口每次实时读目录, 无缓存无建档)。
命名: 文件名含「中文」或「_cn」判为中文版, 含「英文」或「_en」判为英文版, 其余归 other;
      前端「说明书」按钮(产品总表操作列, 图库右边)优先打开中文版。
安全: 编码目录与文件名 resolve 后必须仍在 MANUALS_ROOT 内, 否则 403 (防 ../ 穿越),
      且只允许 .pdf。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/manuals", tags=["manuals"])

_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{2,40}$")   # 产品编码目录名白名单


def _root() -> Path:
    return Path(os.environ.get("MANUALS_ROOT", "/app/storage/manuals"))


def _safe_dir(code: str) -> Path:
    if not _CODE_RE.match(code or ""):
        raise HTTPException(400, "产品编码不合法")
    root = _root().resolve()
    p = (root / code).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise HTTPException(403, "路径越界")
    return p


def _lang_of(filename: str) -> str:
    low = filename.lower()
    if "中文" in filename or "_cn" in low or "-cn" in low:
        return "cn"
    if "英文" in filename or "_en" in low or "-en" in low:
        return "en"
    return "other"


@router.get("/{code}")
def list_manuals(code: str):
    """某产品的说明书文件列表 (实时读 storage/manuals/{code}, 群晖改完即生效)。"""
    d = _safe_dir(code)
    if not d.is_dir():
        return {"files": []}
    files = []
    for f in sorted(d.iterdir()):
        if not f.is_file() or f.suffix.lower() != ".pdf":
            continue
        files.append({
            "name": f.name,
            "lang": _lang_of(f.name),
            "size": f.stat().st_size,
            "url": f"/api/manuals/{code}/file?name={quote(f.name)}",
        })
    return {"files": files}


@router.get("/{code}/file")
def get_manual(code: str, name: str = Query(...)):
    """取说明书 PDF (inline, 浏览器新标签直接预览)。"""
    d = _safe_dir(code)
    p = (d / name).resolve()
    try:
        p.relative_to(d)
    except ValueError:
        raise HTTPException(403, "路径越界")
    if p.suffix.lower() != ".pdf" or not p.is_file():
        raise HTTPException(404, "说明书不存在")
    return FileResponse(
        p, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(p.name)}"},
    )
