"""
新建店铺时自动生成 configs/shops/*.yaml（从 demo_shop.yaml 复制并写入 brand_id / shop_code / shop_display_name）。
用户无需手拷配置文件。
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import yaml

from apps.core.configs.loader import load_shop_config
from apps.core.runtime_paths import configs_dir


def _sanitize_filename_part(s: str) -> str:
    t = (s or "").strip()
    for ch in '<>:"/\\|?*\n\r\t':
        t = t.replace(ch, "_")
    return t or "shop"


def _sid_from_loaded(shop: object) -> str:
    sid = str(getattr(shop, "shop_id", "") or "").strip()
    if sid:
        return sid
    bid = str(getattr(shop, "brand_id", "") or "").strip()
    code = str(getattr(shop, "shop_code", "") or "").strip()
    return f"{bid}:{code}" if bid and code else ""


def _resolve_yaml_path(
    shops_dir: Path,
    *,
    brand_id: str,
    shop_code: str,
    shop_id: str,
) -> Path:
    """同一店铺固定落在同一文件；若 shop_code.yaml 已被别的店占用则换名。"""
    b = _sanitize_filename_part(brand_id)
    c = _sanitize_filename_part(shop_code)
    sid_fn = _sanitize_filename_part(shop_id.replace(":", "_"))

    seq = [
        shops_dir / f"{c}.yaml",
        shops_dir / f"{b}__{c}.yaml",
        shops_dir / f"shop_{sid_fn}.yaml",
    ]
    for p in seq:
        if not p.is_file():
            return p
        try:
            if _sid_from_loaded(load_shop_config(p)) == shop_id:
                return p
        except Exception:
            continue
    return shops_dir / f"shop_{sid_fn}_{uuid.uuid4().hex[:8]}.yaml"


def _apply_identity(data: dict, *, brand_id: str, shop_code: str, display_name: str, shop_id: str) -> None:
    data["brand_id"] = brand_id
    data["shop_code"] = shop_code
    data["shop_display_name"] = (display_name or "").strip() or shop_code
    data["shop_id"] = shop_id
    if "qianniu" not in data or not isinstance(data["qianniu"], dict):
        data["qianniu"] = {
            "main_window_name_contains": "千牛",
            "input_box_point": {"x": 0, "y": 0},
            "send_button_point": {"x": 0, "y": 0},
            "chat_scroll_point": {"x": 0, "y": 0},
            "session_list_rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
            "unread_session_switch": False,
            "idle_auto_minimize_seconds": 120,
            "taskbar_icon_point": {"x": 0, "y": 0},
            "restore_title_ocr_rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        }
    else:
        qn = data["qianniu"]
        if "session_list_rect" not in qn or not isinstance(qn.get("session_list_rect"), dict):
            qn["session_list_rect"] = {"left": 0, "top": 0, "right": 0, "bottom": 0}
        if "unread_session_switch" not in qn:
            qn["unread_session_switch"] = False
        if "idle_auto_minimize_seconds" not in qn:
            qn["idle_auto_minimize_seconds"] = 120
        if "taskbar_icon_point" not in qn or not isinstance(qn.get("taskbar_icon_point"), dict):
            qn["taskbar_icon_point"] = {"x": 0, "y": 0}
        if "restore_title_ocr_rect" not in qn or not isinstance(qn.get("restore_title_ocr_rect"), dict):
            qn["restore_title_ocr_rect"] = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    if "ocr_chat_rect" not in data or not isinstance(data["ocr_chat_rect"], dict):
        data["ocr_chat_rect"] = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    if "ocr_right_rect" not in data or not isinstance(data["ocr_right_rect"], dict):
        data["ocr_right_rect"] = {"left": 0, "top": 0, "right": 0, "bottom": 0}


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    path.write_text(text, encoding="utf-8")


def _minimal_template_dict() -> dict:
    return {
        "brand_id": "",
        "shop_code": "",
        "shop_display_name": "",
        "shop_id": "",
        "qianniu": {
            "main_window_name_contains": "千牛",
            "input_box_point": {"x": 0, "y": 0},
            "send_button_point": {"x": 0, "y": 0},
            "chat_scroll_point": {"x": 0, "y": 0},
            "session_list_rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
            "unread_session_switch": False,
            "idle_auto_minimize_seconds": 120,
            "taskbar_icon_point": {"x": 0, "y": 0},
            "restore_title_ocr_rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        },
        "ocr_chat_rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "ocr_right_rect": {"left": 0, "top": 0, "right": 0, "bottom": 0},
    }


def ensure_shop_config_yaml(
    *,
    brand_id: str,
    shop_code: str,
    display_name: str,
    shop_id: str,
) -> Path:
    """
    确保存在与本店铺对应的 yaml；不存在则从 demo_shop.yaml 复制并写入身份字段。
    若文件已存在且属于本 shop_id，则同步 shop_display_name 等字段。
    """
    brand_id = brand_id.strip()
    shop_code = shop_code.strip()
    shop_id = shop_id.strip()
    if not brand_id or not shop_code or not shop_id:
        raise ValueError("brand_id / shop_code / shop_id 不能为空")

    shops_dir = configs_dir() / "shops"
    shops_dir.mkdir(parents=True, exist_ok=True)

    target = _resolve_yaml_path(shops_dir, brand_id=brand_id, shop_code=shop_code, shop_id=shop_id)
    template = shops_dir / "demo_shop.yaml"

    if target.is_file():
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raw = {}
        _apply_identity(
            raw,
            brand_id=brand_id,
            shop_code=shop_code,
            display_name=display_name,
            shop_id=shop_id,
        )
        _write_yaml(target, raw)
        return target

    if template.is_file():
        shutil.copy2(template, target)
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    else:
        raw = _minimal_template_dict()

    if not isinstance(raw, dict):
        raw = _minimal_template_dict()

    _apply_identity(
        raw,
        brand_id=brand_id,
        shop_code=shop_code,
        display_name=display_name,
        shop_id=shop_id,
    )
    _write_yaml(target, raw)
    return target


def sync_shop_display_name_in_yaml(*, shop_id: str, display_name: str) -> Path | None:
    """在店铺管理里改显示名后，尽量同步到对应 yaml 的 shop_display_name。"""
    dn = (display_name or "").strip()
    if not dn:
        return None
    shops_dir = configs_dir() / "shops"
    if not shops_dir.is_dir():
        return None
    for p in sorted(shops_dir.glob("*.yaml")):
        try:
            sh = load_shop_config(p)
            if _sid_from_loaded(sh) == shop_id:
                raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    raw["shop_display_name"] = dn
                    _write_yaml(p, raw)
                return p
        except Exception:
            continue
    return None
