"""
v1.6.0 视觉 LLM 兜底：UIA 找不到按钮时，用 Claude 视觉模型从屏幕截图里找按钮坐标并点击。

调用链：
  caller (popup_dismiss / risk_warning_revise)
    → locate_and_click_button("返回修改")
    → 1. 截屏 (mss / PIL)
    → 2. POST http://127.0.0.1:8006/vision/describe (Bearer)
         model=anthropic/claude-sonnet-4-5
         question="屏幕上有一个按钮叫'返回修改'，请返回它中心点的 (x, y) 屏幕绝对坐标。
                   如果找不到，回复 'NOT_FOUND'。"
    → 3. 解析 Claude 回复里的坐标
    → 4. 用 Win32 mouse_event 移动鼠标并点击

前置条件：
  - D:\\AI\\AI 视觉中心\\backend 服务在 127.0.0.1:8006 上启动
  - 环境变量 ANTHROPIC_API_KEY 配好（让视觉中心能调 Claude）

失败模式（都返回 False，不抛异常）：
  - 视觉中心 /healthz 不通 → 调用方进 L3
  - Claude 拒答 / 返回 "NOT_FOUND" / 坐标解析失败

日志前缀：[visloc]
"""
from __future__ import annotations

import base64
import ctypes
import io
import logging
import os
import re
import time
from typing import Final

_log = logging.getLogger("apps.core.automation.visual_button_locator")

VISION_CENTER_BASE: Final[str] = os.environ.get(
    "BEE_VISION_BASE", "http://127.0.0.1:8006"
)
VISION_CENTER_TOKEN: Final[str] = os.environ.get(
    "BEE_BEARER_TOKEN", "dev-token-change-me"
)
VISION_MODEL: Final[str] = os.environ.get(
    "BEE_VISION_MODEL", "anthropic/claude-sonnet-4-5"
)
HTTP_TIMEOUT_S: Final[float] = 30.0


def _is_vision_center_alive() -> bool:
    """ping /healthz 5s 超时；不通视为视觉中心未启动。"""
    try:
        import httpx
    except ImportError:
        _log.warning("[visloc] httpx 不可用，无法调用视觉中心")
        return False
    try:
        r = httpx.get(f"{VISION_CENTER_BASE}/healthz", timeout=5.0)
        return r.status_code == 200
    except Exception as e:
        _log.debug("[visloc] /healthz 不通：%r", e)
        return False


def _grab_full_screen_b64() -> str | None:
    """全屏截图返回 base64 PNG。优先 mss，失败用 PIL ImageGrab。"""
    # 路径 A：mss（项目已依赖）
    try:
        import mss
        import mss.tools
        with mss.mss() as sct:
            mon = sct.monitors[1]  # 主显示器
            img = sct.grab(mon)
            png = mss.tools.to_png(img.rgb, img.size)
        return base64.b64encode(png).decode("ascii")
    except Exception as e:
        _log.debug("[visloc] mss 截屏失败：%r", e)

    # 路径 B：PIL 兜底
    try:
        from PIL import ImageGrab
        im = ImageGrab.grab()
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        _log.warning("[visloc] PIL ImageGrab 截屏也失败：%r", e)
        return None


def _ask_claude_for_coords(button_text: str, screen_b64: str) -> tuple[int, int] | None:
    """调视觉中心 /vision/describe，让 Claude 返回按钮中心坐标 (x, y)。"""
    try:
        import httpx
    except ImportError:
        return None

    question = (
        f"屏幕截图中有一个按钮，文本是\"{button_text}\"。"
        "请返回这个按钮**中心点**的屏幕绝对像素坐标，格式严格为单行：(x, y)\n"
        "示例：(852, 638)\n"
        "如果你在截图中找不到这个按钮，请回复：NOT_FOUND\n"
        "不要写任何其它解释、Markdown、emoji。"
    )

    headers = {"Authorization": f"Bearer {VISION_CENTER_TOKEN}"}
    body = {
        "image_b64": screen_b64,
        "question": question,
        "model": VISION_MODEL,
    }
    try:
        r = httpx.post(
            f"{VISION_CENTER_BASE}/vision/describe",
            json=body,
            headers=headers,
            timeout=HTTP_TIMEOUT_S,
        )
        if r.status_code != 200:
            _log.warning(
                "[visloc] 视觉中心 /describe HTTP %d: %s",
                r.status_code, r.text[:200],
            )
            return None
        text = (r.json().get("description") or "").strip()
    except Exception as e:
        _log.warning("[visloc] /describe 调用异常：%r", e)
        return None

    if not text or "NOT_FOUND" in text.upper():
        _log.info("[visloc] Claude 表示找不到按钮 %r", button_text)
        return None

    match = re.search(r"\(?\s*(\d{1,5})\s*[,，]\s*(\d{1,5})\s*\)?", text)
    if not match:
        _log.warning("[visloc] 无法从 Claude 回复中解析坐标：%r", text[:120])
        return None
    try:
        x = int(match.group(1))
        y = int(match.group(2))
        if x <= 0 or y <= 0 or x > 8000 or y > 8000:
            _log.warning("[visloc] 坐标超界 (%d, %d)，丢弃", x, y)
            return None
        return (x, y)
    except (ValueError, IndexError):
        return None


def _move_and_click(x: int, y: int) -> bool:
    """Win32 mouse_event 在 (x, y) 左键点击。"""
    try:
        u32 = ctypes.windll.user32
        u32.SetCursorPos(int(x), int(y))
        time.sleep(0.06)
        u32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.03)
        u32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        return True
    except Exception as e:
        _log.warning("[visloc] mouse_event 点击异常 (%d,%d): %r", x, y, e)
        return False


# ============================================================
# 公共 API
# ============================================================

def locate_button_by_vision(button_text: str) -> tuple[int, int] | None:
    """
    单纯定位：返回按钮中心坐标 (x, y) 或 None。
    None = 视觉中心未启动 / Claude 拒答 / 解析失败 / 任何异常。
    """
    if not _is_vision_center_alive():
        return None
    screen_b64 = _grab_full_screen_b64()
    if not screen_b64:
        return None
    return _ask_claude_for_coords(button_text, screen_b64)


def locate_and_click_button(button_text: str) -> bool:
    """
    定位 + 点击。给 popup_dismiss / risk_warning_revise 用的高层接口。

    返回 True=已调用 mouse_event；False=未点击（失败原因已写日志）。
    返回 True 不代表按钮真的生效——上层应用其它方式做后置验证。
    """
    coords = locate_button_by_vision(button_text)
    if coords is None:
        _log.info("[visloc] 未能定位按钮 %r，调用方应进 L3 兜底", button_text)
        return False
    x, y = coords
    _log.info("[visloc] Claude 定位 %r 中心 (%d, %d)，准备点击", button_text, x, y)
    return _move_and_click(x, y)


def diagnose_vision_center() -> dict:
    """诊断用：返回视觉中心可达性 + 配置。UI 启动时可调。"""
    info: dict = {
        "vision_base": VISION_CENTER_BASE,
        "vision_model": VISION_MODEL,
        "alive": False,
        "anthropic_api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
    }
    info["alive"] = _is_vision_center_alive()
    return info
