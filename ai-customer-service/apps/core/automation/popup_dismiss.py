"""
v1.6.0 千牛弹窗自动处理（分类器 + 分发）。

旧版（v1.5.x）：所有弹窗一律按"找返回修改 / 找通用关闭"两层去试。
新版（v1.6.0）：先 classify 出弹窗类型，再分发到不同 handler：

  - risk_warning   : 风控弹窗（含"请勿重复提问""消费者已描述过问题"等）
                     → 本模块识别但**不点击**；交给 risk_warning_revise.py 走 LLM 重新生成
  - coupon_promo   : 优惠券推荐 → 直接点"不感兴趣"或关闭
  - new_order      : 新订单提示 → 点"知道了"
  - join_notice    : 客户接入提示 → 点关闭
  - system_notify  : 系统通知 → 点关闭/Close
  - common_notice  : 其它已知通用弹窗 → 点 _DISMISS_NAMES 中的任一按钮
  - unknown        : 都不是 → L2 视觉LLM找按钮 → L3 点右上角 X 兜底

向后兼容：
  - 旧公共函数 dismiss_known_popups_near_foreground() 保留，内部走新分类器
  - _DISMISS_NAMES / _RETURN_TO_EDIT_NAMES / _POPUP_TITLE_KEYWORDS 等旧常量仍存在
  - risk_warning 由 risk_warning_revise.py 单独处理，本模块仅 classify

日志前缀：所有本模块日志以 [popup] 起头，便于在主日志里区分。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

_log = logging.getLogger("apps.core.automation.popup_dismiss")

# ============================================================
# 已知按钮名 / 标题关键词
# ============================================================

# 前台窗口上的通用关闭按钮（按 ControlName 严格匹配）
_DISMISS_NAMES = frozenset({
    "关闭", "Close", "我知道了", "知道了", "不感兴趣", "稍后再说",
    "取消", "Cancel", "OK", "确定", "好的",
})

# 千牛"返回修改"按钮（风控弹窗专用）——本模块识别但不点；交给 risk_warning_revise
_RETURN_TO_EDIT_NAMES = frozenset({
    "返回修改",
})

# 风控弹窗特征关键词（标题或正文出现任一即判定）
_RISK_WARNING_KEYWORDS = (
    "服务态度提醒",
    "重复消息",
    "请勿重复提问",
    "消费者已描述过问题",
    "如需确认消费者诉求",
    "重复无意义话术",
)

# 优惠券 / 推广弹窗特征
_COUPON_KEYWORDS = (
    "向您推荐", "优惠券", "立减券", "特价券", "新人专享",
)

# 新订单 / 拍下提示
_NEW_ORDER_KEYWORDS = (
    "新订单", "您有新订单", "买家已下单", "买家拍下",
)

# 客户接入 / 转接提示
_JOIN_NOTICE_KEYWORDS = (
    "已加入接待", "客户已加入", "客户分流", "客服接入",
)

# 系统通知
_SYSTEM_NOTIFY_KEYWORDS = (
    "网络异常", "网络已恢复", "网络连接断开", "系统消息", "系统通知",
)

# 老的旧关键词列表（向后兼容）：v1.5.x 调用方读这个
_POPUP_TITLE_KEYWORDS: tuple[str, ...] = _RISK_WARNING_KEYWORDS


PopupKind = Literal[
    "risk_warning",
    "coupon_promo",
    "new_order",
    "join_notice",
    "system_notify",
    "common_notice",
    "unknown",
]


@dataclass(slots=True)
class PopupInfo:
    """分类器输出。"""
    kind: PopupKind
    title: str
    hwnd: int = 0
    window_ctrl: Any = None
    button_candidates: list[tuple[Any, str]] = field(default_factory=list)
    fulltext_hint: str = ""


# ============================================================
# 内部 helper：按钮树扫描
# ============================================================

def _enumerate_buttons(parent) -> list[tuple[Any, str]]:
    """BFS 扫描 2 层 ButtonControl，返回 (ctrl, name) 列表（name 已 strip）。"""
    out: list[tuple[Any, str]] = []
    try:
        for c in parent.GetChildren():
            try:
                if getattr(c, "ControlTypeName", "") == "ButtonControl":
                    n = (getattr(c, "Name", "") or "").strip()
                    if n:
                        out.append((c, n))
                for c2 in c.GetChildren():
                    try:
                        if getattr(c2, "ControlTypeName", "") == "ButtonControl":
                            n2 = (getattr(c2, "Name", "") or "").strip()
                            if n2:
                                out.append((c2, n2))
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    return out


def _gather_text_fragments(parent, depth: int = 0, max_depth: int = 3) -> str:
    """收集弹窗子控件的 Name 文本，用于关键词匹配（含正文）。"""
    if depth > max_depth:
        return ""
    fragments: list[str] = []
    try:
        n = (getattr(parent, "Name", "") or "").strip()
        if n:
            fragments.append(n)
        for c in parent.GetChildren():
            fragments.append(_gather_text_fragments(c, depth + 1, max_depth))
    except Exception:
        pass
    return " ".join(s for s in fragments if s)


# ============================================================
# 分类器
# ============================================================

def classify_popup(win) -> PopupInfo | None:
    """对一个候选顶层窗口分类。返回 PopupInfo 或 None（不是弹窗 / 主程序自身）。"""
    try:
        title = (getattr(win, "Name", "") or "").strip()
    except Exception:
        return None
    if not title:
        return None

    # 排除自身窗口
    if any(k in title for k in ("智能客服中控台", "lightrat-接待中心", "千牛工作台")):
        return None

    hwnd = 0
    try:
        hwnd = int(getattr(win, "NativeWindowHandle", 0) or 0)
    except Exception:
        hwnd = 0

    fulltext = _gather_text_fragments(win, max_depth=3)
    button_candidates = _enumerate_buttons(win)

    # 优先级 1：风控弹窗（最危险，必须精准识别）
    title_or_text = title + " " + fulltext
    if any(kw in title_or_text for kw in _RISK_WARNING_KEYWORDS):
        return PopupInfo(
            kind="risk_warning",
            title=title,
            hwnd=hwnd,
            window_ctrl=win,
            button_candidates=button_candidates,
            fulltext_hint=fulltext[:600],
        )

    # 优先级 2：优惠券
    if any(kw in title_or_text for kw in _COUPON_KEYWORDS):
        return PopupInfo(
            kind="coupon_promo", title=title, hwnd=hwnd,
            window_ctrl=win, button_candidates=button_candidates,
            fulltext_hint=fulltext[:300],
        )

    # 优先级 3：新订单
    if any(kw in title_or_text for kw in _NEW_ORDER_KEYWORDS):
        return PopupInfo(
            kind="new_order", title=title, hwnd=hwnd,
            window_ctrl=win, button_candidates=button_candidates,
            fulltext_hint=fulltext[:300],
        )

    # 优先级 4：客户接入提示
    if any(kw in title_or_text for kw in _JOIN_NOTICE_KEYWORDS):
        return PopupInfo(
            kind="join_notice", title=title, hwnd=hwnd,
            window_ctrl=win, button_candidates=button_candidates,
            fulltext_hint=fulltext[:300],
        )

    # 优先级 5：系统通知
    if any(kw in title_or_text for kw in _SYSTEM_NOTIFY_KEYWORDS):
        return PopupInfo(
            kind="system_notify", title=title, hwnd=hwnd,
            window_ctrl=win, button_candidates=button_candidates,
            fulltext_hint=fulltext[:300],
        )

    # 优先级 6：标题/正文含通用关闭按钮 → common_notice
    btn_names_in_window = {n for _, n in button_candidates}
    if btn_names_in_window & _DISMISS_NAMES:
        return PopupInfo(
            kind="common_notice", title=title, hwnd=hwnd,
            window_ctrl=win, button_candidates=button_candidates,
            fulltext_hint=fulltext[:300],
        )

    # 都不是 → unknown
    return PopupInfo(
        kind="unknown", title=title, hwnd=hwnd,
        window_ctrl=win, button_candidates=button_candidates,
        fulltext_hint=fulltext[:600],
    )


# ============================================================
# 分发处理器
# ============================================================

def _click_button_by_names(
    candidates: list[tuple[Any, str]],
    preferred_names: frozenset[str] | tuple[str, ...],
) -> str | None:
    """从按钮候选里点第一个匹配的；返回点中的按钮 Name 或 None。"""
    pref = set(preferred_names)
    for ctrl, name in candidates:
        if name in pref:
            try:
                ctrl.Click(simulateMove=False)
                return name
            except Exception:
                continue
    return None


def _try_visual_locator_and_click(button_text: str, info: PopupInfo) -> bool:
    """L2 兜底：调 visual_button_locator 找按钮坐标并点击。失败返回 False。"""
    try:
        from apps.core.automation.visual_button_locator import (
            locate_and_click_button,
        )
    except ImportError:
        _log.debug("[popup] L2 跳过：visual_button_locator 未提供")
        return False
    try:
        return bool(locate_and_click_button(button_text))
    except Exception as e:
        _log.warning("[popup] L2 视觉兜底异常：%r", e)
        return False


def _click_top_right_x(info: PopupInfo) -> bool:
    """L3 兜底：直接点弹窗右上角的 X 关闭图标。"""
    try:
        import ctypes
        from ctypes import wintypes
        if not info.hwnd:
            return False

        class _RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                        ("right", wintypes.LONG), ("bottom", wintypes.LONG)]
        rc = _RECT()
        ok = ctypes.windll.user32.GetWindowRect(info.hwnd, ctypes.byref(rc))
        if not ok:
            return False
        cx = int(rc.right) - 16
        cy = int(rc.top) + 16
        ctypes.windll.user32.SetCursorPos(cx, cy)
        time.sleep(0.05)
        ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.03)
        ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        return True
    except Exception as e:
        _log.warning("[popup] L3 点右上角 X 异常：%r", e)
        return False


def dispatch_popup(info: PopupInfo) -> bool:
    """
    根据 info.kind 分发到不同 handler。

    返回 True=成功；False=未处理。
    risk_warning 路径返回 False，由上层转交给 risk_warning_revise。
    """
    if info.kind == "risk_warning":
        _log.info(
            "[popup] 识别到风控弹窗（title=%r），交给 risk_warning_revise 处理",
            info.title[:60],
        )
        return False

    if info.kind == "coupon_promo":
        prefs = ("不感兴趣", "稍后再说", "关闭", "Close")
        name = _click_button_by_names(info.button_candidates, prefs)
        if name:
            _log.info("[popup] coupon_promo → 已点 %r", name)
            return True

    elif info.kind == "new_order":
        prefs = ("知道了", "我知道了", "确定", "OK", "关闭")
        name = _click_button_by_names(info.button_candidates, prefs)
        if name:
            _log.info("[popup] new_order → 已点 %r", name)
            return True

    elif info.kind == "join_notice":
        prefs = ("关闭", "知道了", "我知道了", "Close")
        name = _click_button_by_names(info.button_candidates, prefs)
        if name:
            _log.info("[popup] join_notice → 已点 %r", name)
            return True

    elif info.kind == "system_notify":
        prefs = ("关闭", "Close", "我知道了", "确定")
        name = _click_button_by_names(info.button_candidates, prefs)
        if name:
            _log.info("[popup] system_notify → 已点 %r", name)
            return True

    elif info.kind == "common_notice":
        name = _click_button_by_names(info.button_candidates, _DISMISS_NAMES)
        if name:
            _log.info("[popup] common_notice → 已点 %r", name)
            return True

    elif info.kind == "unknown":
        # L2: 视觉 LLM 找"关闭"按钮
        if _try_visual_locator_and_click("关闭", info):
            _log.info("[popup] unknown 视觉LLM→已点关闭")
            return True
        # L3: 直接点弹窗右上角 X
        if _click_top_right_x(info):
            _log.warning(
                "[popup] unknown 兜底：已点右上角 X 区域 title=%r；请截图给开发者",
                info.title[:60],
            )
            return True
        _log.warning(
            "[popup] unknown 处理失败 title=%r：请人工关闭",
            info.title[:60],
        )
        return False

    # 已知类型但没找到合适按钮 → L2 视觉 + L3 右上角
    if _try_visual_locator_and_click("关闭", info):
        _log.info("[popup] %s 视觉LLM→已点关闭", info.kind)
        return True
    if _click_top_right_x(info):
        _log.warning("[popup] %s L3 兜底点右上角 X", info.kind)
        return True
    return False


# ============================================================
# 旧公共 API（向后兼容，内部走新分类器）
# ============================================================

_unknown_fail_count = 0
_UNKNOWN_FAIL_NOTIFY_THRESHOLD = 5


def dismiss_known_popups_near_foreground() -> int:
    """
    旧 API（popup_worker.py 每 5s 调用）。

    新版行为：
      1. 扫描桌面所有顶层窗口
      2. 对每个候选 classify_popup
      3. risk_warning 单独标记（不在这里点；交给 risk_warning_revise loop）
      4. 其它类型 dispatch_popup 处理
      5. unknown 连续累计 5 次 → 写日志提示人工

    返回成功处理的弹窗数（不含 risk_warning）。
    """
    global _unknown_fail_count
    try:
        import uiautomation as auto
    except ImportError:
        return 0
    closed = 0
    try:
        from apps.core.automation.uia_guard import uia_lock
        with uia_lock():  # v1.6.17：串行化 UIA 遍历，防多线程 COM 并发崩溃(0x80040155)
            desktop = auto.GetRootControl()
            _wins = list(desktop.GetChildren())
        for win in _wins:
            try:
                with uia_lock():
                    info = classify_popup(win)
                if info is None:
                    continue
                if info.kind == "risk_warning":
                    continue  # 交给 risk_warning_revise
                ok = dispatch_popup(info)
                if ok:
                    closed += 1
                    if info.kind == "unknown":
                        _unknown_fail_count = 0
                else:
                    if info.kind == "unknown":
                        _unknown_fail_count += 1
                        if _unknown_fail_count >= _UNKNOWN_FAIL_NOTIFY_THRESHOLD:
                            _log.error(
                                "[popup] 连续 %d 次出现未知弹窗未关：title=%r；"
                                "请把截图发给开发者补规则",
                                _unknown_fail_count, info.title[:80],
                            )
                            _unknown_fail_count = 0
            except Exception:
                continue
    except Exception as e:
        _log.warning("[popup] 顶层窗口扫描异常：%r", e)
    return closed


def find_risk_warning_popups() -> list[PopupInfo]:
    """v1.6.0 新公共 API：仅返回风控弹窗。由 risk_warning_revise loop 调用。"""
    out: list[PopupInfo] = []
    try:
        import uiautomation as auto
    except ImportError:
        return out
    try:
        from apps.core.automation.uia_guard import uia_lock
        with uia_lock():  # v1.6.17：串行化 UIA 遍历，防多线程 COM 并发崩溃(0x80040155)
            desktop = auto.GetRootControl()
            _wins = list(desktop.GetChildren())
        for win in _wins:
            try:
                with uia_lock():
                    info = classify_popup(win)
                if info is not None and info.kind == "risk_warning":
                    out.append(info)
            except Exception:
                continue
    except Exception:
        pass
    return out


# ============================================================
# 旧"返回修改 + Ctrl+A 重发"逻辑（仅保留供 risk_warning_revise L3 引用）
# ============================================================

def _resend_input_box_text() -> None:
    """Ctrl+A 全选输入框 → Ctrl+C → Ctrl+V → Enter。"""
    try:
        from apps.core.automation.win_input import press_ctrl_combo, press_vk, VK_RETURN
        time.sleep(0.5)
        press_ctrl_combo("a")
        time.sleep(0.15)
        press_ctrl_combo("c")
        time.sleep(0.1)
        press_ctrl_combo("v")
        time.sleep(0.15)
        press_vk(VK_RETURN)
    except Exception:
        pass


def click_return_to_edit_and_resend_original() -> bool:
    """
    v1.5.x 老逻辑：找"返回修改" → 点 → Ctrl+A+C+V+Enter 重发原文。

    v1.6.0 不推荐——重发原文会撞同一风控。仅供 risk_warning_revise L3 兜底使用。
    """
    try:
        import uiautomation as auto
    except ImportError:
        return False
    try:
        desktop = auto.GetRootControl()
        for win in desktop.GetChildren():
            try:
                title = (getattr(win, "Name", "") or "").strip()
                if not title:
                    continue
                if not any(kw in title for kw in _RISK_WARNING_KEYWORDS):
                    continue
                buttons = _enumerate_buttons(win)
                clicked = _click_button_by_names(buttons, _RETURN_TO_EDIT_NAMES)
                if clicked:
                    _resend_input_box_text()
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _find_and_click_button(parent) -> bool:
    """v1.5.x 老接口。新代码改用 classify_popup + dispatch_popup。"""
    buttons = _enumerate_buttons(parent)
    if _click_button_by_names(buttons, _RETURN_TO_EDIT_NAMES):
        _resend_input_box_text()
        return True
    if _click_button_by_names(buttons, _DISMISS_NAMES):
        return True
    return False
