"""
将屏幕坐标写入店铺 YAML（由程序生成/更新，避免用户手改配置文件）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _write_shop_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )
    path.write_text(text, encoding="utf-8")


def apply_click_calibration(path: Path, target_id: str, x: int, y: int) -> None:
    """
    target_id:
      input_box_point | send_button_point | chat_scroll_point
      ocr_chat_tl | ocr_chat_br | ocr_right_tl | ocr_right_br
      session_list_tl | session_list_br | taskbar_icon_point | restore_title_tl | restore_title_br
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raw = {}

    q = raw.setdefault("qianniu", {})
    if not isinstance(q, dict):
        q = {}
        raw["qianniu"] = q

    match target_id:
        case "input_box_point":
            q["input_box_point"] = {"x": int(x), "y": int(y)}
        case "send_button_point":
            q["send_button_point"] = {"x": int(x), "y": int(y)}
        case "chat_scroll_point":
            q["chat_scroll_point"] = {"x": int(x), "y": int(y)}
        case "ocr_chat_tl":
            r = raw.setdefault("ocr_chat_rect", {})
            if not isinstance(r, dict):
                r = {}
                raw["ocr_chat_rect"] = r
            r["left"] = int(x)
            r["top"] = int(y)
            try:
                rr = int(r.get("right", 0))
                bb = int(r.get("bottom", 0))
            except Exception:
                rr, bb = 0, 0
            if rr <= int(x):
                r["right"] = int(x) + 400
            if bb <= int(y):
                r["bottom"] = int(y) + 300
        case "ocr_chat_br":
            r = raw.setdefault("ocr_chat_rect", {})
            if not isinstance(r, dict):
                r = {}
                raw["ocr_chat_rect"] = r
            r["right"] = int(x)
            r["bottom"] = int(y)
            try:
                lf = int(r.get("left", 0))
                tp = int(r.get("top", 0))
            except Exception:
                lf, tp = 0, 0
            if int(x) <= lf:
                r["right"] = lf + 1
            if int(y) <= tp:
                r["bottom"] = tp + 1
        case "ocr_right_tl":
            r = raw.setdefault("ocr_right_rect", {})
            if not isinstance(r, dict):
                r = {}
                raw["ocr_right_rect"] = r
            r["left"] = int(x)
            r["top"] = int(y)
            try:
                rr = int(r.get("right", 0))
                bb = int(r.get("bottom", 0))
            except Exception:
                rr, bb = 0, 0
            if rr <= int(x):
                r["right"] = int(x) + 400
            if bb <= int(y):
                r["bottom"] = int(y) + 300
        case "ocr_right_br":
            r = raw.setdefault("ocr_right_rect", {})
            if not isinstance(r, dict):
                r = {}
                raw["ocr_right_rect"] = r
            r["right"] = int(x)
            r["bottom"] = int(y)
            try:
                lf = int(r.get("left", 0))
                tp = int(r.get("top", 0))
            except Exception:
                lf, tp = 0, 0
            if int(x) <= lf:
                r["right"] = lf + 1
            if int(y) <= tp:
                r["bottom"] = tp + 1
        case "session_list_tl":
            r = q.setdefault("session_list_rect", {})
            if not isinstance(r, dict):
                r = {}
                q["session_list_rect"] = r
            r["left"] = int(x)
            r["top"] = int(y)
            try:
                rr = int(r.get("right", 0))
                bb = int(r.get("bottom", 0))
            except Exception:
                rr, bb = 0, 0
            if rr <= int(x):
                r["right"] = int(x) + 120
            if bb <= int(y):
                r["bottom"] = int(y) + 400
        case "session_list_br":
            r = q.setdefault("session_list_rect", {})
            if not isinstance(r, dict):
                r = {}
                q["session_list_rect"] = r
            r["right"] = int(x)
            r["bottom"] = int(y)
            try:
                lf = int(r.get("left", 0))
                tp = int(r.get("top", 0))
            except Exception:
                lf, tp = 0, 0
            if int(x) <= lf:
                r["right"] = lf + 1
            if int(y) <= tp:
                r["bottom"] = tp + 1
        case "taskbar_icon_point":
            q["taskbar_icon_point"] = {"x": int(x), "y": int(y)}
        case "restore_title_tl":
            r = q.setdefault("restore_title_ocr_rect", {})
            if not isinstance(r, dict):
                r = {}
                q["restore_title_ocr_rect"] = r
            r["left"] = int(x)
            r["top"] = int(y)
            try:
                rr = int(r.get("right", 0))
                bb = int(r.get("bottom", 0))
            except Exception:
                rr, bb = 0, 0
            if rr <= int(x):
                r["right"] = int(x) + 200
            if bb <= int(y):
                r["bottom"] = int(y) + 48
        case "restore_title_br":
            r = q.setdefault("restore_title_ocr_rect", {})
            if not isinstance(r, dict):
                r = {}
                q["restore_title_ocr_rect"] = r
            r["right"] = int(x)
            r["bottom"] = int(y)
            try:
                lf = int(r.get("left", 0))
                tp = int(r.get("top", 0))
            except Exception:
                lf, tp = 0, 0
            if int(x) <= lf:
                r["right"] = lf + 1
            if int(y) <= tp:
                r["bottom"] = tp + 1
        case "buyer_nick_tl":
            r = raw.setdefault("ocr_buyer_nick_rect", {})
            if not isinstance(r, dict):
                r = {}
                raw["ocr_buyer_nick_rect"] = r
            r["left"] = int(x)
            r["top"] = int(y)
            try:
                rr = int(r.get("right", 0))
                bb = int(r.get("bottom", 0))
            except Exception:
                rr, bb = 0, 0
            if rr <= int(x):
                r["right"] = int(x) + 250
            if bb <= int(y):
                r["bottom"] = int(y) + 30
        case "buyer_nick_br":
            r = raw.setdefault("ocr_buyer_nick_rect", {})
            if not isinstance(r, dict):
                r = {}
                raw["ocr_buyer_nick_rect"] = r
            r["right"] = int(x)
            r["bottom"] = int(y)
            try:
                lf = int(r.get("left", 0))
                tp = int(r.get("top", 0))
            except Exception:
                lf, tp = 0, 0
            if int(x) <= lf:
                r["right"] = lf + 1
            if int(y) <= tp:
                r["bottom"] = tp + 1
        case "service_btn_point":
            q["service_btn_point"] = {"x": int(x), "y": int(y)}
        case "right_panel_left_point":
            # 单值边界：x 存 left，y 仅作占位
            q["right_panel_left"] = int(x)
        case _:
            raise ValueError(f"未知校准项: {target_id}")

    _write_shop_yaml(path, raw)


# ── v1.6.3 锚点（窗口+偏移）读写：qianniu.calib_anchor ────────────────────
def write_calib_anchor(path: Path, anchor_dict: dict) -> None:
    """把 anchor（to_yaml_dict 产物）写入 qianniu.calib_anchor。"""
    if not path.is_file():
        raise FileNotFoundError(str(path))
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raw = {}
    q = raw.setdefault("qianniu", {})
    if not isinstance(q, dict):
        q = {}
        raw["qianniu"] = q
    q["calib_anchor"] = anchor_dict
    _write_shop_yaml(path, raw)


def read_calib_anchor(path: Path) -> dict | None:
    """读 qianniu.calib_anchor；不存在/不合法返回 None。"""
    if not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, dict):
        return None
    q = raw.get("qianniu")
    if not isinstance(q, dict):
        return None
    a = q.get("calib_anchor")
    return a if isinstance(a, dict) else None
