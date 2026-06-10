"""
v1.6.0 切会话 L3 兜底：右下角千牛"迷你新消息小气泡"OCR + 点击。

场景：v1.5.x 切会话两层兜底（黄条 HSV + 红标兜底）都 miss 时，本模块作为 L3 兜底。
千牛在右下角会弹出"迷你新消息小气泡"，OCR 找到含买家昵称（tbXXXXXX）
的气泡 → 点击 → 1.2s 后自动跳转主窗口对应会话。

为什么这条路径靠谱：
  - 即使主窗口被遮挡 / 主窗口最小化 / 主窗口在错误位置，小气泡也会弹
  - 千牛系统级 toast 一定在屏幕右下角（无论分辨率）
  - 点击小气泡 = 千牛自身的"跳转到该会话"逻辑，比我们模拟点会话列表更可靠

默认采样区域（可校准）：
  - 1920×1080：(1500, 800) ~ (1920, 1080)
  - 2560×1440：(1700, 1100) ~ (2560, 1440)
  - 通用规则：右下角 35% 宽 × 30% 高

日志前缀：[minibubble]
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger("apps.core.channels.qianniu.minibubble_fallback")

# v1.6.1 修：原 \btb... 在 OCR 把 'tb' 误读为 'Lb/ib/fb' 或 tb 后跟中文/标点时失配。
# 现放宽为：
#   1. 主模式：(t|l|i|f|1)b + 3 位以上数字字母，无需 \b 单词边界
#   2. 兜底模式：纯数字串（>=8 位），千牛 ID 截断后只剩数字也能命中
_TB_NICK_PATTERN = re.compile(r"(?<![a-zA-Z])[tlif1][bB]\w{3,}", re.IGNORECASE)
_NUMERIC_NICK_PATTERN = re.compile(r"\b\d{8,}\b")

# v1.6.18：右下角气泡里属于「界面噪声」的文本（时间/系统提示/账号统计），
# 这些不是买家昵称，识别通用昵称气泡时要排除。命中其一即跳过该 span。
_BUBBLE_NOISE_RE = re.compile(
    r"^\d{1,2}:\d{2}"          # 14:16 / 02:16:33
    r"|^\d+\s*秒|^\d+\s*分钟|^\d+s$|^\d+秒$"   # 64秒 / 1s
    r"|个账号|个联系人"        # "1个账号-1个联系人"
    r"|PM$|AM$"                # 02:16PM
    r"|^\d{1,2}/\d{1,2}/\d{2,4}"      # 1/6/2026 (日/月/年)
    r"|^\d{4}/\d{1,2}/\d{1,2}"        # v1.6.20: 2026/6/1 (年/月/日，系统日期，之前漏网误点)
    r"|^\d{4}-\d{1,2}-\d{1,2}"        # 2026-6-1
    r"|当前用户来自|系统消息|已读|未读|对方正在输入",
)

# v1.6.20：通用昵称气泡兜底——文本必须「像人话」(含中文或≥2连续字母)，
# 排除纯数字/纯符号/纯日期，避免把系统时钟、日期、托盘数字当昵称误点。
_LOOKS_LIKE_NICK_RE = re.compile(r"[一-鿿]|[A-Za-z]{2,}")

# 千牛系统级 toast 容器自身的固定文案（不是某个买家的气泡），出现也跳过
_BUBBLE_SELF_WORDS = ("lightrat", "千牛", "消息提醒", "新消息")


@dataclass(frozen=True, slots=True)
class MinibubbleSamplingRect:
    """右下角采样矩形（屏幕绝对像素）。"""
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def _default_sampling_rect_for_screen() -> MinibubbleSamplingRect:
    """
    v1.6.1 调大：原 35%x30% 太小，右下角小气泡常带头像 + 昵称 + 消息预览 + 时间，
    一行可能宽 350px，得扩到 40% 宽 × 35% 高才能完整 OCR。
    """
    try:
        import ctypes
        u32 = ctypes.windll.user32
        sw = int(u32.GetSystemMetrics(0))
        sh = int(u32.GetSystemMetrics(1))
    except Exception:
        sw, sh = 2560, 1440
    left = int(sw * 0.60)
    top = int(sh * 0.65)
    # v1.6.20：底边排除任务栏区（约 60px），避免把任务栏时钟「16:08」/
    # 系统日期「2026/6/1」当成新消息气泡误点（曾导致点到桌面右下角→唤起千牛工作台）。
    bottom = max(top + 100, sh - 60)
    return MinibubbleSamplingRect(left=left, top=top, right=sw, bottom=bottom)


def _grab_rect_rgb(rect: MinibubbleSamplingRect):
    """截屏指定矩形，返回 numpy RGB array 或 None。"""
    try:
        from apps.core.capture.screen import Rect, ScreenCapture
        cap = ScreenCapture()
        return cap.grab_rgb(Rect(
            left=rect.left, top=rect.top, right=rect.right, bottom=rect.bottom,
        ))
    except Exception as e:
        _log.warning("[minibubble] 截屏失败 rect=%s: %r", rect, e)
        return None


def _ocr_rect(rect: MinibubbleSamplingRect):
    """OCR 指定区域返回 spans。"""
    img = _grab_rect_rgb(rect)
    if img is None:
        return []
    try:
        from apps.core.ocr.dual_engine import get_dual_ocr_engine
        ocr = get_dual_ocr_engine()
        res = ocr.recognize(img)
        return res.spans
    except Exception as e:
        _log.warning("[minibubble] OCR 异常：%r", e)
        return []


def _save_debug_screenshot(rect: MinibubbleSamplingRect, tag: str) -> None:
    """诊断用截图（保存 dist/data/sqlite/debug/）。"""
    try:
        from apps.core.runtime_paths import data_dir
        from apps.core.capture.screen import Rect, ScreenCapture
        import numpy as np
        from PIL import Image
        cap = ScreenCapture()
        img = cap.grab_rgb(Rect(
            left=rect.left, top=rect.top, right=rect.right, bottom=rect.bottom,
        ))
        if img is None:
            return
        out_dir = Path(data_dir()) / "sqlite" / "debug"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        out = out_dir / f"minibubble_{tag}_{ts}.png"
        Image.fromarray(np.asarray(img)).save(out)
    except Exception:
        pass


@dataclass(frozen=True, slots=True)
class MinibubbleClickResult:
    success: bool
    reason: str = ""
    clicked_at: tuple[int, int] | None = None
    matched_nick: str = ""


def _point_belongs_to_qianniu(cx: int, cy: int) -> bool:
    """
    v1.6.23：判断屏幕点 (cx,cy) 底下的窗口是否属于千牛进程（aliworkbench/qianniu）。

    用于「点击前最后一道闸门」：千牛真气泡是千牛进程画的窗口 → 返回 True 照点；
    若该点落在 Claude / 浏览器 / 本程序等其它进程窗口上 → 返回 False，绝不点击
    （实测：千牛没开/被遮挡时，OCR 把 Claude 底部「Opus 4.8 (1M context)」当昵称
    气泡，旧逻辑直接点 (1893,1370) → 把 Claude 点到前台）。
    """
    try:
        import ctypes
        import os
        from ctypes import wintypes

        u32 = ctypes.windll.user32
        k32 = ctypes.windll.kernel32

        u32.WindowFromPoint.argtypes = [wintypes.POINT]
        u32.WindowFromPoint.restype = wintypes.HWND

        hwnd = u32.WindowFromPoint(wintypes.POINT(int(cx), int(cy)))
        if not hwnd:
            return False

        # 上溯到顶层窗口（GA_ROOT=2），气泡控件的子句柄也能拿到所属进程
        try:
            top = u32.GetAncestor(hwnd, 2)
            if top:
                hwnd = top
        except Exception:
            pass

        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) == os.getpid():
            return False  # 本程序自己的窗口，绝不点

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        exe = ""
        if h:
            try:
                sz = wintypes.DWORD(260)
                pbuf = ctypes.create_unicode_buffer(260)
                if k32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(sz)):
                    exe = os.path.basename(pbuf.value).lower()
            finally:
                k32.CloseHandle(h)
        return ("aliworkbench" in exe) or ("qianniu" in exe)
    except Exception:
        # 读不到进程信息时保守放弃点击（宁可不点，也不点错窗口）
        return False


def try_click_minibubble(
    *,
    rect: MinibubbleSamplingRect | None = None,
    expected_nick: str | None = None,
    log: Callable[[str], None] | None = None,
) -> MinibubbleClickResult:
    """
    扫描右下角迷你气泡 → 找含 tbXXX 昵称的 span → 点击其中心。

    Args:
        rect: 采样区域，None=用 _default_sampling_rect_for_screen() 推断
        expected_nick: 若指定必须命中此昵称；None=点第一个匹配 tb pattern 的
        log: 可选日志函数（写到 brain 日志）
    """
    sampling = rect or _default_sampling_rect_for_screen()
    _save_debug_screenshot(sampling, "scan")

    spans = _ocr_rect(sampling)
    if not spans:
        msg = (
            f"[minibubble] 兜底失败：右下角采样区无 OCR 结果 "
            f"rect=({sampling.left},{sampling.top})-({sampling.right},{sampling.bottom})"
        )
        _log.info(msg)
        if log:
            log(msg)
        return MinibubbleClickResult(success=False, reason="ocr_empty")

    # v1.6.1：先扫主模式 (tb / Lb / ib / fb / 1b ...) 命中
    candidates: list[tuple] = []
    all_span_texts: list[str] = []  # 诊断用
    for s in spans:
        text = (getattr(s, "text", "") or "").strip()
        if not text:
            continue
        all_span_texts.append(text)
        m = _TB_NICK_PATTERN.search(text)
        if not m:
            continue
        nick = m.group(0)
        if expected_nick and expected_nick.lower() not in nick.lower():
            continue
        candidates.append((s, nick))

    # v1.6.1：主模式 miss 时用纯数字兜底（千牛 ID 截断后只剩数字也能命中）
    if not candidates:
        for s in spans:
            text = (getattr(s, "text", "") or "").strip()
            if not text:
                continue
            m = _NUMERIC_NICK_PATTERN.search(text)
            if not m:
                continue
            nick = "tb" + m.group(0)  # 补 tb 前缀方便后续日志区分
            candidates.append((s, nick))

    # v1.6.18：第三层兜底——非 tb/数字昵称（如 kid_betsy、中文昵称）也要能命中。
    # 右下角气泡一旦弹出本身就代表"有新消息"，点它必进对应会话。
    # 策略：排除时间/系统/账号统计等噪声词后，取「最靠下」的有效文本块点击
    # （气泡昵称行通常在内容行上方，最靠下的有效块多为消息预览，点它同样进会话）。
    if not candidates:
        for s in spans:
            text = (getattr(s, "text", "") or "").strip()
            if not text or len(text) < 2:
                continue
            if _BUBBLE_NOISE_RE.search(text):
                continue
            if any(w in text for w in _BUBBLE_SELF_WORDS):
                continue
            # v1.6.20：必须「像人话」(含中文或≥2连续字母)，排除纯数字/纯符号/纯日期，
            # 否则系统时钟「16:08」/日期「2026/6/1」会被当昵称→点到桌面右下角误唤工作台。
            if not _LOOKS_LIKE_NICK_RE.search(text):
                continue
            candidates.append((s, text[:16]))
        if candidates and log:
            log(f"[minibubble] 通用昵称气泡兜底：命中 {len(candidates)} 个候选(非tb昵称)")

    if not candidates:
        # v1.6.1：诊断输出所有 OCR 内容，方便后续看是 OCR 把 tb 读错了 还是采样区不对
        preview = " | ".join(all_span_texts[:8])
        msg = (
            f"[minibubble] 兜底失败：右下角无有效新消息气泡 "
            f"(共 {len(spans)} 个 span)；OCR 内容预览: {preview!r}"
        )
        _log.info(msg)
        if log:
            log(msg)
        return MinibubbleClickResult(success=False, reason="no_bubble_found")

    # 取最靠下的（最新弹出的）
    def _y_center(span):
        bbox = getattr(span, "bbox", None)
        if bbox is None or len(bbox) < 4:
            return 0
        return (bbox[1] + bbox[3]) / 2.0

    span, matched_nick = max(candidates, key=lambda t: _y_center(t[0]))
    bbox = getattr(span, "bbox", None)
    if not bbox or len(bbox) < 4:
        return MinibubbleClickResult(
            success=False, reason="bbox_invalid", matched_nick=matched_nick,
        )

    # bbox 是相对采样区的坐标，转回屏幕绝对坐标
    cx_local = int((bbox[0] + bbox[2]) / 2)
    cy_local = int((bbox[1] + bbox[3]) / 2)
    cx = sampling.left + cx_local
    cy = sampling.top + cy_local

    # v1.6.23：点击前最后一道闸门——(cx,cy) 底下的窗口必须属于千牛进程，否则绝不点。
    # 防右下角恰好是 Claude/浏览器等，OCR 把其 UI 文字（如「Opus 4.8 (1M context)」）
    # 误当昵称气泡 → 点它 → 唤起该程序（实测点到 Claude 模型选择条 (1893,1370)）。
    if not _point_belongs_to_qianniu(cx, cy):
        msg = (
            f"[minibubble] 放弃点击：屏幕({cx},{cy}) 底下的窗口不属于千牛进程"
            f"（疑为 Claude/浏览器/本程序等），nick={matched_nick!r}"
        )
        _log.info(msg)
        if log:
            log(msg)
        return MinibubbleClickResult(
            success=False, reason="point_not_qianniu", matched_nick=matched_nick,
        )

    msg = f"[minibubble] 兜底命中 nick={matched_nick!r} → 点击屏幕({cx},{cy})"
    _log.info(msg)
    if log:
        log(msg)

    # 点击
    try:
        import ctypes
        u32 = ctypes.windll.user32
        u32.SetCursorPos(cx, cy)
        time.sleep(0.08)
        u32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
        time.sleep(0.03)
        u32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
        time.sleep(1.2)
        return MinibubbleClickResult(
            success=True,
            reason="clicked",
            clicked_at=(cx, cy),
            matched_nick=matched_nick,
        )
    except Exception as e:
        return MinibubbleClickResult(
            success=False, reason=f"mouse_event_error:{e!r}",
            matched_nick=matched_nick,
        )
