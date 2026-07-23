"""产品尺寸 SVG 的群晖持久化、校验、版本备份与结构化标签提取。"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import HTTPException


MAX_SVG_BYTES = 8 * 1024 * 1024
_SAFE_REL_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
_BLOCKED_TAGS = {"script", "foreignObject", "iframe", "object", "embed"}


def get_root() -> Path:
    return Path(os.environ.get("PRODUCT_DIMENSION_ROOT", "/app/storage/product_dimensions")).resolve()


def safe_path(relpath: str) -> Path:
    if not relpath or not _SAFE_REL_RE.match(relpath) or relpath.startswith(("/", "\\")):
        raise HTTPException(400, "尺寸资产路径不合法")
    root = get_root()
    path = (root / relpath).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(403, "尺寸资产路径越界") from exc
    return path


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_and_parse_svg(svg: str) -> ET.Element:
    raw = svg.encode("utf-8")
    if not raw or len(raw) > MAX_SVG_BYTES:
        raise HTTPException(413, "SVG 为空或超过 8MB")
    upper_head = raw[:4096].upper()
    if b"<!DOCTYPE" in upper_head or b"<!ENTITY" in upper_head:
        raise HTTPException(400, "SVG 不允许 DOCTYPE/ENTITY")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise HTTPException(400, f"SVG 解析失败: {exc}") from exc
    if _local_name(root.tag) != "svg":
        raise HTTPException(400, "文件根节点不是 SVG")

    has_product = False
    has_dimensions = False
    for node in root.iter():
        tag = _local_name(node.tag)
        if tag in _BLOCKED_TAGS:
            raise HTTPException(400, f"SVG 含不允许的节点: {tag}")
        node_id = node.attrib.get("id")
        has_product = has_product or node_id == "product-body"
        has_dimensions = has_dimensions or node_id == "dimensions-editable"
        for key, value in node.attrib.items():
            local_key = _local_name(key)
            if local_key.lower().startswith("on"):
                raise HTTPException(400, "SVG 不允许事件脚本属性")
            if local_key in {"href", "src"} and value and not value.startswith(("#", "data:image/")):
                raise HTTPException(400, "SVG 不允许外部资源链接")
    if not has_product or not has_dimensions:
        raise HTTPException(400, "SVG 必须包含 product-body 与 dimensions-editable 两个图层")
    return root


def extract_dimension_labels(root: ET.Element, previous: dict | None = None) -> list[dict[str, Any]]:
    previous_by_id = {
        str(item.get("id")): item
        for item in (previous or {}).get("labels", [])
        if item.get("id")
    }
    dimension_group = next(
        (node for node in root.iter() if node.attrib.get("id") == "dimensions-editable"), None
    )
    if dimension_group is None:
        return []
    labels: list[dict[str, Any]] = []
    for index, node in enumerate(dimension_group.iter()):
        if _local_name(node.tag) != "text" or node.attrib.get("data-panel-static") == "true":
            continue
        node_id = node.attrib.get("id") or f"dim-text-erp-{index + 1:03d}"
        value = "".join(node.itertext()).strip()
        old = previous_by_id.get(node_id, {})
        was_edited = bool(old) and value != str(old.get("value") or "")
        transform = node.attrib.get("transform", "")
        angle_match = re.search(r"rotate\(\s*([-\d.]+)", transform)
        labels.append({
            **{k: v for k, v in old.items() if k not in {"id", "value", "x", "y", "angle"}},
            "id": node_id,
            "value": value,
            "x": _float_or_none(node.attrib.get("x")),
            "y": _float_or_none(node.attrib.get("y")),
            "angle": _float_or_none(angle_match.group(1)) if angle_match else 0,
            "source": "erp.user_edit" if was_edited else (
                node.attrib.get("data-source") or old.get("source") or "erp.manual_dimension"
            ),
            "confidence": "user_confirmed" if was_edited else (
                old.get("confidence") or (
                    "user_confirmed" if node.attrib.get("data-editor-dimension") else "visual_label"
                )
            ),
        })
    return labels


def _float_or_none(value: str | None) -> float | None:
    try:
        return round(float(value), 3) if value is not None else None
    except (TypeError, ValueError):
        return None


def merge_dimension_data(previous: dict | None, root: ET.Element) -> dict:
    data = copy.deepcopy(previous or {})
    data["labels"] = extract_dimension_labels(root, previous)
    data["last_saved_at"] = datetime.now().astimezone().isoformat()
    data["schema_version"] = max(int(data.get("schema_version") or 1), 2)
    data["manual_editing"] = {
        "svg_group_id": "dimensions-editable",
        "note": "通过畔色 ERP 产品总表的细节尺寸编辑器维护。",
    }
    return data


def read_svg(relpath: str) -> str:
    path = safe_path(relpath)
    if not path.is_file():
        raise HTTPException(404, "尺寸 SVG 不存在")
    return path.read_text(encoding="utf-8")


def read_binary(relpath: str) -> Path:
    path = safe_path(relpath)
    if not path.is_file():
        raise HTTPException(404, "尺寸预览图不存在")
    return path


def save_versioned_svg(relpath: str, svg: str, *, version: int, metadata: dict) -> str | None:
    """原子覆盖 current.svg，并在同目录 versions 保留最近 30 份。返回备份相对路径。"""
    path = safe_path(relpath)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_rel: str | None = None
    if path.is_file():
        versions = path.parent / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = versions / f"v{version:04d}-{stamp}.svg"
        shutil.copy2(path, backup)
        backup_rel = backup.relative_to(get_root()).as_posix()
        backups = sorted(versions.glob("*.svg"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in backups[30:]:
            stale.unlink(missing_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix="dimension-", suffix=".svg", dir=path.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        temp.write_text(svg, encoding="utf-8")
        os.replace(temp, path)
        metadata_path = path.parent / "metadata.json"
        meta_temp = metadata_path.with_suffix(".json.tmp")
        meta_temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(meta_temp, metadata_path)
    finally:
        temp.unlink(missing_ok=True)
    return backup_rel


def restore_backup(relpath: str, backup_relpath: str | None) -> None:
    path = safe_path(relpath)
    if backup_relpath:
        shutil.copy2(safe_path(backup_relpath), path)
    else:
        path.unlink(missing_ok=True)
