from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from apps.core.channels.qianniu.driver import Point, QianniuConfig
from apps.core.capture.screen import Rect


@dataclass(frozen=True, slots=True)
class ShopConfig:
    brand_id: str
    shop_code: str
    shop_display_name: str
    qianniu: QianniuConfig | None = None
    ocr_chat_rect: Rect | None = None
    ocr_right_rect: Rect | None = None
    # 买家昵称区域（聊天窗口顶部的一条窄条），用于发送前校验当前会话买家身份
    ocr_buyer_nick_rect: Rect | None = None
    # v1.6.14 咨询宝贝读编码（默认关，需标定后用）：
    #   consult_tab_point   - 右侧「咨询宝贝」标签点击点
    #   consult_hover_point - 标签展开后商品缩略图的悬停点（弹出编码浮层）
    #   consult_popup_rect  - 悬停浮层 OCR 区域（含 编码 PFGxxx）
    consult_tab_point: Point | None = None
    consult_hover_point: Point | None = None
    consult_popup_rect: Rect | None = None
    # For db tables, we still need stable ids; MVP uses shop_code as shop_id placeholder.
    shop_id: str | None = None


def load_shop_config(path: Path) -> ShopConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    brand_id = str(raw.get("brand_id") or "").strip()
    shop_code = str(raw.get("shop_code") or "").strip()
    shop_display_name = str(raw.get("shop_display_name") or "").strip() or shop_code

    qianniu_cfg = None
    if isinstance(raw.get("qianniu"), dict):
        q = raw["qianniu"]
        main_contains = str(q.get("main_window_name_contains") or "千牛")
        ip = q.get("input_box_point") or {}
        sp = q.get("send_button_point")
        cp = q.get("chat_scroll_point")
        input_point = Point(x=int(ip.get("x", 0)), y=int(ip.get("y", 0)))
        send_point = None
        if isinstance(sp, dict):
            send_point = Point(x=int(sp.get("x", 0)), y=int(sp.get("y", 0)))
        chat_point = None
        if isinstance(cp, dict):
            chat_point = Point(x=int(cp.get("x", 0)), y=int(cp.get("y", 0)))
        sl = q.get("session_list_rect")
        session_list_rect = None
        if isinstance(sl, dict):
            try:
                session_list_rect = Rect(
                    left=int(sl["left"]),
                    top=int(sl["top"]),
                    right=int(sl["right"]),
                    bottom=int(sl["bottom"]),
                )
            except Exception:
                session_list_rect = None
        if "unread_session_switch" in q:
            unread_switch = bool(q.get("unread_session_switch"))
        else:
            # 已录 session_list_rect 时默认开启黄条/未读自动点选（可在 YAML 显式写 false 关闭）
            unread_switch = bool(
                session_list_rect is not None
                and session_list_rect.width() >= 8
                and session_list_rect.height() >= 8
            )
        idle_sec = int(q.get("idle_auto_minimize_seconds") or 0)
        tip = q.get("taskbar_icon_point") or {}
        tb_point = None
        if isinstance(tip, dict) and (int(tip.get("x", 0)) != 0 or int(tip.get("y", 0)) != 0):
            tb_point = Point(x=int(tip.get("x", 0)), y=int(tip.get("y", 0)))
        rt = q.get("restore_title_ocr_rect")
        title_rect = None
        if isinstance(rt, dict):
            try:
                title_rect = Rect(
                    left=int(rt["left"]),
                    top=int(rt["top"]),
                    right=int(rt["right"]),
                    bottom=int(rt["bottom"]),
                )
            except Exception:
                title_rect = None
        qianniu_cfg = QianniuConfig(
            main_window_name_contains=main_contains,
            input_box_point=input_point,
            send_button_point=send_point,
            chat_scroll_point=chat_point,
            session_list_rect=session_list_rect,
            unread_session_switch=unread_switch,
            idle_auto_minimize_seconds=idle_sec,
            taskbar_icon_point=tb_point,
            restore_title_ocr_rect=title_rect,
        )

    if not brand_id or not shop_code:
        raise RuntimeError("shop config 缺少 brand_id/shop_code")

    return ShopConfig(
        brand_id=brand_id,
        shop_code=shop_code,
        shop_display_name=shop_display_name,
        qianniu=qianniu_cfg,
        ocr_chat_rect=_read_rect(raw, "ocr_chat_rect"),
        ocr_right_rect=_read_rect(raw, "ocr_right_rect"),
        ocr_buyer_nick_rect=_read_rect(raw, "ocr_buyer_nick_rect"),
        consult_tab_point=_read_point(raw, "consult_tab_point"),
        consult_hover_point=_read_point(raw, "consult_hover_point"),
        consult_popup_rect=_read_rect(raw, "consult_popup_rect"),
        shop_id=str(raw.get("shop_id") or "") or None,
    )


def _read_point(raw: dict[str, Any], key: str) -> "Point | None":
    v = raw.get(key)
    if not isinstance(v, dict):
        return None
    try:
        return Point(x=int(v["x"]), y=int(v["y"]))
    except Exception:
        return None


def _read_rect(raw: dict[str, Any], key: str) -> Rect | None:
    v = raw.get(key)
    if not isinstance(v, dict):
        return None
    try:
        return Rect(
            left=int(v["left"]),
            top=int(v["top"]),
            right=int(v["right"]),
            bottom=int(v["bottom"]),
        )
    except Exception:
        return None

