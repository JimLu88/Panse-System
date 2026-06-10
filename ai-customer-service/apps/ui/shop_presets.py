"""
店铺选项（界面显示名 → 配置文件路径）。

工作台「当前店铺」**仅以 SQLite shops 表为准**（与话术库「店铺管理」一致）；
对每个已登记店铺解析或生成对应的 `configs/shops/*.yaml`。
磁盘上若残留已删店铺的 yaml，不会出现在下拉框中（可自行删除多余 yaml 文件）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from apps.core.configs.loader import load_shop_config
from apps.core.runtime_paths import configs_dir


def _label_for_yaml(path: Path) -> str:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        name = str(raw.get("shop_display_name") or "").strip()
        return name or path.stem
    except Exception:
        return path.stem


def list_shop_presets() -> list[tuple[str, Path]]:
    root = configs_dir() / "shops"
    out: list[tuple[str, Path]] = []
    if root.is_dir():
        for p in sorted(root.glob("*.yaml")):
            out.append((_label_for_yaml(p), p))
    if out:
        return out
    fallback = configs_dir() / "shops" / "demo_shop.yaml"
    return [("示例店铺（请添加 configs/shops/*.yaml）", fallback)]


def list_workbench_shop_picks(
    conn: sqlite3.Connection,
) -> list[tuple[str, Path | None, str]]:
    """
    工作台「当前店铺」下拉项：(显示名, yaml路径或None, 内部 shop_id)。
    仅包含数据库 `shops` 中仍存在的店铺；yaml 缺失时会自动生成（从 demo 模板复制）。
    """
    root = configs_dir() / "shops"
    yaml_files = sorted(root.glob("*.yaml")) if root.is_dir() else []

    try:
        rows = conn.execute(
            "SELECT shop_id, brand_id, shop_code, display_name FROM shops ORDER BY created_at DESC"
        ).fetchall()
    except Exception:
        rows = []

    out: list[tuple[str, Path | None, str]] = []
    for shop_id, brand_id, shop_code, display_name in rows:
        sid = str(shop_id or "").strip()
        if not sid:
            continue
        bid = str(brand_id or "").strip()
        code = str(shop_code or "").strip()
        dn = (str(display_name).strip() if display_name else "") or code or sid
        cand = root / f"{code}.yaml"
        if cand.is_file():
            out.append((dn, cand, sid))
            continue
        found: Path | None = None
        for p in yaml_files:
            try:
                sh = load_shop_config(p)
                if str(sh.brand_id) == bid and str(sh.shop_code) == code:
                    found = p
                    break
            except Exception:
                continue
        if found is not None:
            out.append((dn, found, sid))
            continue
        try:
            from apps.core.configs.shop_yaml_bootstrap import ensure_shop_config_yaml

            p = ensure_shop_config_yaml(
                brand_id=bid,
                shop_code=code,
                display_name=dn,
                shop_id=sid,
            )
            out.append((dn, p, sid))
        except Exception:
            out.append((f"{dn}（配置生成失败）", None, sid))

    out.sort(key=lambda x: x[0].lower())
    return out
