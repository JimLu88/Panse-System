"""
一键自动识别千牛坐标：
  优先用本地 OCR 在全屏中找已知关键词（方案D），
  OCR 锚点不足时自动降级到 Vision AI（方案A）。
返回 AutoCalibrateResult，调用方决定是否写入 YAML。
"""
from __future__ import annotations

import base64
import io
import json
import re
from dataclasses import dataclass, field
from typing import Callable

import numpy as np


# ── 结果数据结构 ───────────────────────────────────────────────────────────
@dataclass
class CalibrateCoords:
    """成功识别到的坐标字段（None 表示未识别到）。"""
    input_box_x: int | None = None
    input_box_y: int | None = None
    send_button_x: int | None = None
    send_button_y: int | None = None
    chat_scroll_x: int | None = None
    chat_scroll_y: int | None = None
    ocr_chat_left: int | None = None
    ocr_chat_top: int | None = None
    ocr_chat_right: int | None = None
    ocr_chat_bottom: int | None = None
    session_list_left: int | None = None
    session_list_top: int | None = None
    session_list_right: int | None = None
    session_list_bottom: int | None = None
    # 任务栏千牛图标（叮咚后用于把窗口拉回前台）
    taskbar_icon_x: int | None = None
    taskbar_icon_y: int | None = None
    # 千牛主窗口边界（仅诊断用，可选）
    qianniu_window_left: int | None = None
    qianniu_window_top: int | None = None
    qianniu_window_right: int | None = None
    qianniu_window_bottom: int | None = None
    # 右侧信息面板（客服/商品/推荐 Tab 区域）
    service_btn_x: int | None = None   # "客服"蓝色按钮中心
    service_btn_y: int | None = None
    right_panel_left: int | None = None  # 右侧面板左边界
    #: v1.6.3：anchor 预测复检被否决、退回满屏搜覆盖的字段名（供预览标红）
    calib_fallback_fields: list = field(default_factory=list)

    def has_critical(self) -> bool:
        """关键字段（聊天区 OCR + 输入框）是否都有。"""
        return all(v is not None for v in [
            self.ocr_chat_left, self.ocr_chat_top,
            self.ocr_chat_right, self.ocr_chat_bottom,
            self.input_box_x, self.input_box_y,
        ])


@dataclass
class AutoCalibrateResult:
    coords: CalibrateCoords
    method: str          # "ocr" | "vision" | "ocr+vision"
    confidence: str      # "high" | "medium" | "low"
    needs_manual_send: bool = False   # True = 多窗口，需用户手动点「发送」确认
    multi_window_count: int = 0       # 检测到的窗口数
    notes: list[str] = field(default_factory=list)
    screenshot_png: bytes = field(default=b"", repr=False)


# ── 全屏截图 ───────────────────────────────────────────────────────────────
def _grab_fullscreen() -> tuple[np.ndarray, int, int]:
    """返回 (rgb_array, screen_w, screen_h)。"""
    from mss import mss
    with mss() as sct:
        mon = sct.monitors[0]  # 全屏（含多显示器）
        img = sct.grab(mon)
        arr = np.array(img)[:, :, :3][:, :, ::-1]  # BGRA→RGB
        return arr, int(mon["width"]), int(mon["height"])


def _rgb_to_png_bytes(arr: np.ndarray) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


# ── UIA 直接定位千牛主窗口与任务栏图标（最准的方式） ──────────────────────
def _locate_qianniu_window_uia() -> tuple[int, int, int, int] | None:
    """
    用 Windows UIA 找到千牛**真实主窗口**的物理像素矩形。
    策略：
      1. 通过 EnumWindows 拿到所有可见顶层窗口
      2. 找进程名包含 'AliWorkbench.exe' 或 'qianniu' 的窗口
      3. 排除我们自己的进程
      4. 在多个候选里挑选面积最大的（聊天主窗口）
    找不到返回 None。
    """
    try:
        import ctypes
        import os
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 拿到当前进程 PID 用于排除
        my_pid = os.getpid()

        candidates: list[tuple[int, tuple[int, int, int, int], str]] = []  # (area, rect, exe_name)

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            # 拿窗口标题
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""

            # 拿进程 ID
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == my_pid:
                return True   # 排除自己

            # 拿进程可执行名（用 OpenProcess + QueryFullProcessImageNameW）
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            exe_name = ""
            if h:
                try:
                    sz = wintypes.DWORD(260)
                    pbuf = ctypes.create_unicode_buffer(260)
                    if kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(sz)):
                        exe_name = os.path.basename(pbuf.value)
                finally:
                    kernel32.CloseHandle(h)

            # 匹配条件：进程名是 AliWorkbench.exe，或窗口标题含千牛
            exe_low = exe_name.lower()
            is_qianniu = (
                "aliworkbench" in exe_low
                or "qianniu" in exe_low
                or ("千牛" in title and "校准" not in title and "AIWorkbench" not in title)
            )
            if not is_qianniu:
                return True

            # 拿矩形
            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            w = rect.right - rect.left
            h_ = rect.bottom - rect.top
            if w < 400 or h_ < 300:   # 太小的不要（系统通知、托盘等）
                return True

            candidates.append((w * h_, (rect.left, rect.top, rect.right, rect.bottom), exe_name or title))
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)

        if not candidates:
            return None
        # 取面积最大的
        candidates.sort(key=lambda c: c[0], reverse=True)
        return candidates[0][1]
    except Exception:
        return None


def _locate_taskbar_icon_uia() -> tuple[int, int] | None:
    """用 UIA 在任务栏中找到千牛图标的中心坐标。失败返回 None。"""
    from apps.core.channels.qianniu.taskbar_ops import locate_taskbar_icon_uia

    return locate_taskbar_icon_uia()


# ── CV+OCR 混合校准引擎（主力方案） ─────────────────────────────────────

def _run_rapid_ocr(rgb_crop: np.ndarray) -> list:
    """在给定的 RGB 裁剪图上跑 RapidOCR，返回 list[OCRSpan]（bbox 为裁剪图本地坐标）。"""
    try:
        from apps.core.ocr.engine_rapid import RapidOCREngine
        engine = RapidOCREngine()
        return engine.recognize(rgb_crop)
    except Exception:
        return []


def _find_send_button_cv_ocr(
    crop: np.ndarray,
    ox: int, oy: int,         # crop 左上角在全屏中的偏移
    win_h: int,               # 主窗口高度（用于限制搜索范围）
    notes: list[str],
) -> tuple[int, int] | None:
    """
    在 crop（主窗口 RGB 图）里找发送按钮。
    策略：OpenCV 蓝色矩形检测 → 按位置过滤（下半区、大小合适）→ OCR 验证"发送"。
    返回全屏绝对坐标 (cx, cy)，失败返回 None。
    """
    import cv2

    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    # 千牛发送键蓝色：OpenCV HSV H≈100-125, S≈120-255, V≈120-255
    mask = cv2.inRange(hsv,
                       np.array([95, 100, 100], dtype=np.uint8),
                       np.array([130, 255, 255], dtype=np.uint8))
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates: list[tuple[int, int, int, int]] = []   # (x, y, w, h) in crop coords
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        # 尺寸过滤：宽 50~200px、高 18~55px、宽高比 ≥ 1.5
        if not (50 <= w <= 200 and 18 <= h <= 55 and w / max(h, 1) >= 1.5):
            continue
        # 位置过滤：在窗口下半部分（y > 窗口高度 55%）
        if y < int(crop.shape[0] * 0.55):
            continue
        candidates.append((x, y, w, h))

    if not candidates:
        notes.append("CV：窗口内未找到符合条件的蓝色矩形候选（发送按钮）")
        return None

    # OCR 验证：裁出每个候选，跑 OCR 找"发送"
    for x, y, w, h in candidates:
        pad = 4
        region = crop[max(0, y - pad): y + h + pad, max(0, x - pad): x + w + pad]
        spans = _run_rapid_ocr(region)
        for sp in spans:
            if "发送" in sp.text:
                cx, cy = ox + x + w // 2, oy + y + h // 2
                notes.append(f"CV+OCR 确认发送按钮：({cx}, {cy})，OCR=「{sp.text}」")
                return cx, cy

    # OCR 未命中，取最靠右下角的候选（千牛发送键在右下角）
    best = max(candidates, key=lambda c: c[0] + c[1])
    x, y, w, h = best
    cx, cy = ox + x + w // 2, oy + y + h // 2
    notes.append(f"CV 发送按钮（OCR未命中，取右下候选）：({cx}, {cy})")
    return cx, cy


def _find_vertical_separator(
    crop: np.ndarray,
    ox: int, oy: int,
    notes: list[str],
) -> int | None:
    """
    在 crop 左侧 45% 区域内找会话列表右侧分隔线。

    千牛布局：窗口左边 → 图标边栏（~60-80px）→ 会话列表（~220-280px）→ 聊天区
    策略：
      ① 降低 Hough 门槛（分隔线常被 UI 元素打断，无法达到 0.35×h 的连续长度）
      ② 跳过距窗口左边 < min_width 的线（图标边栏边缘）
      ③ 取满足条件中最靠右的线 = 会话列表/聊天区分隔线
    """
    import cv2

    ch, cw = crop.shape[:2]
    search_right = int(cw * 0.45)
    left_region = crop[:, :search_right]

    gray = cv2.cvtColor(left_region, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 25, 80)

    # 降低门槛：threshold 0.15×h，minLineLength 0.18×h，maxLineGap 放宽到 25px
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180,
        threshold=int(ch * 0.15),
        minLineLength=int(ch * 0.18),
        maxLineGap=25,
    )
    if lines is None:
        notes.append("CV：未检测到竖向分隔线（将使用默认会话列表宽度）")
        return None

    # 会话列表宽度下限：跳过图标边栏（通常 60-100px）
    # 最小宽度 = max(120px, 13% 窗口宽)，确保跳过任何小边栏
    min_list_width = max(120, int(cw * 0.13))

    vertical_xs: list[int] = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        mx = (x1 + x2) // 2
        # 近乎垂直（|dx| ≤ 8px）且距左边 ≥ min_list_width
        if abs(x1 - x2) <= 8 and mx >= min_list_width:
            vertical_xs.append(mx)

    if not vertical_xs:
        notes.append(f"CV：所有竖线均在 {min_list_width}px 以内（图标边栏），跳过")
        return None

    # 最靠右的满足条件的竖线 = 会话列表右边界
    sep_x = ox + max(vertical_xs)
    notes.append(f"CV 找到会话列表分隔线：x={sep_x}（跳过 <{min_list_width}px 的图标边栏）")
    return sep_x


def _chat_roi_x_bounds(cw: int, chat_left_local: int, send_local_x: int) -> tuple[int, int]:
    """聊天主列水平采样范围（气泡区）：底边识别与备用方差法共用。"""
    x1 = max(0, min(chat_left_local + 5, cw - 80))
    x2 = min(cw - 5, max(send_local_x + 100, x1 + min(420, int(cw * 0.55))))
    return int(x1), int(x2)


def _score_message_toolbar_separator(
    gray_full: np.ndarray,
    y_crop: int,
    x1: int,
    x2: int,
    band: int = 36,
) -> float:
    """
    为「消息区底 / 工具栏顶」水平分割线打分。

    在「上行方差 − 下行方差」基础上，用分割线**下方整块**的亮度区分：
      - 浅灰工具条（均值约 168～238）→ 加分
      - 输入框纯白（均值常 ≥246）→ 强惩罚，避免选中「工具栏|输入」的那条底边

    分数越大越可信；≤ -500 视为无效采样行。
    """
    h = gray_full.shape[0]
    if y_crop < band + 3 or y_crop >= h - band - 3:
        return -999.0
    above = gray_full[y_crop - band: y_crop - 2, x1:x2]
    below = gray_full[y_crop + 2: y_crop + band, x1:x2]
    if above.size == 0 or below.size == 0:
        return -999.0
    sa, sb = float(np.std(above)), float(np.std(below))
    _, mb = float(np.mean(above)), float(np.mean(below))
    score = sa - sb

    if mb >= 246:
        score -= 38.0
    elif mb >= 242:
        score -= 18.0
    elif 168 <= mb <= 238:
        score += min(30.0, (236 - mb) * 0.35 + max(0.0, 28.0 - sb) * 0.35)
    if sa < 12.0:
        score -= 15.0
    elif sa > 18.0:
        score += 4.0

    return score


def _find_toolbar_top(
    crop: np.ndarray,
    ox: int, oy: int,
    send_local_x: int,
    send_local_y: int,
    chat_left_local: int,
    notes: list[str],
) -> int | None:
    """
    聊天区底边 = 消息区与工具栏之间的横线。

    关键修复：必须在「整段聊天列」上算水平梯度。仅在发送键旁取窄竖条时，
    Sobel 经常只抓到「工具栏|输入」那条边，结果每次框到底都同一错误像素。

    再在若干 Sobel 峰上用「上行方差 − 下行方差」打分，锁定「消息|工具栏」。
    """
    import cv2

    ch, cw = crop.shape[:2]
    gray_full = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).astype(np.float32)

    x1, x2 = _chat_roi_x_bounds(cw, chat_left_local, send_local_x)

    # 垂直范围：发送键上方约 4%～50% 窗高
    scan_bottom = max(0, send_local_y - int(ch * 0.04))
    scan_top    = max(0, send_local_y - int(ch * 0.50))
    if scan_bottom <= scan_top + 30 or x2 <= x1 + 30:
        notes.append("CV：工具栏扫描带无效（将估算位置）")
        return None

    sub = gray_full[scan_top:scan_bottom, int(x1): int(x2)]
    rh = sub.shape[0]

    sobelY = cv2.Sobel(sub, cv2.CV_32F, 0, 1, ksize=3)
    row_grad = np.mean(np.abs(sobelY), axis=1)
    mean_g = float(np.mean(row_grad))
    thr = max(mean_g * 1.45, 2.5)

    # 局部峰 + 每 4 行取较大梯度，避免漏线
    peak_idx: list[int] = []
    for i in range(2, len(row_grad) - 2):
        if row_grad[i] >= thr and row_grad[i] >= row_grad[i - 1] and row_grad[i] >= row_grad[i + 1]:
            peak_idx.append(i)
    # 补：整段里梯度最高的几行
    order = np.argsort(-row_grad)[: min(12, len(row_grad))]
    for i in order:
        if i >= 2 and i < len(row_grad) - 2 and row_grad[i] >= thr * 0.85:
            peak_idx.append(int(i))

    candidates = sorted(set(peak_idx))
    # 无局部峰时：沿梯度最强的行也试一下
    if not candidates and len(row_grad) > 10:
        order = np.argsort(-row_grad)[:8]
        candidates = sorted({int(i) for i in order})

    def _pick_by_separator_score(idx_list: list[int]) -> tuple[int | None, float]:
        best_i_, best_s_ = None, -1e9
        for i in idx_list:
            y_crop = scan_top + i
            sc = _score_message_toolbar_separator(
                gray_full, y_crop, int(x1), int(x2)
            )
            if sc > best_s_:
                best_s_, best_i_ = sc, i
        return best_i_, best_s_

    if candidates:
        best_i, best_sc = _pick_by_separator_score(candidates)
        if best_i is not None and best_sc >= 3.0:
            abs_y = oy + scan_top + best_i
            notes.append(
                f"CV 分割线打分→消息区底边：y={abs_y}"
                f"（分数={best_sc:.1f}，候选{len(candidates)}处）"
            )
            return abs_y
        if best_i is not None:
            notes.append(
                f"CV：Sobel 候选方差差偏低 ({best_sc:.1f})，稠密扫描…"
            )

    # ── 稠密扫描：不依赖 Sobel 峰，直接找「上行方差 − 下行方差」最大处 ───
    # 底部约 12% 条带里多是「输入框/发送」横边，不参与稠密方差极值
    imax_dense = rh - max(20, int(rh * 0.12))
    best_i2, best_sc2 = None, -1e9
    for i in range(12, imax_dense, 2):
        sc = _score_message_toolbar_separator(
            gray_full, scan_top + i, int(x1), int(x2)
        )
        if sc > best_sc2:
            best_sc2, best_i2 = sc, i
    if best_i2 is not None and best_sc2 >= 3.5:
        abs_y = oy + scan_top + best_i2
        notes.append(
            f"CV 稠密扫描分割线打分最大 → 消息区底：y={abs_y}（分数={best_sc2:.1f}）"
        )
        return abs_y
    row_std = np.std(sub, axis=1)
    kernel = np.ones(5, dtype=np.float32) / 5.0
    smooth = np.convolve(row_std, kernel, mode="same")
    median_s = float(np.median(smooth))

    # 从下往上找：连续若干行「偏灰低方差」之后，首次明显升高
    low = smooth < max(median_s * 0.72, 6.0)
    run = 0
    for i in range(rh - 1, 15, -1):
        if low[i]:
            run += 1
        else:
            if run >= 14 and smooth[i] > median_s * 1.08:
                y_crop = scan_top + i
                abs_y = oy + y_crop
                notes.append(f"CV 行方差谷底回升 → 消息区底：y={abs_y}")
                return abs_y
            run = 0

    notes.append("CV：未找到工具栏顶部（将估算位置）")
    return None


def _find_chat_header_bottom(
    spans: list,
    list_right_local: int,
    win_top: int,
    notes: list[str],
) -> int | None:
    """
    在聊天列（x > list_right_local）里用 OCR 找「今日接待」等标题行，
    返回其底部 y（全屏）+ 8px 作为聊天消息区真实顶部。
    找不到返回 None。
    """
    keywords = {"今日接待", "接待人数", "全部接待", "接待中"}
    for sp in spans:
        t = sp.text.strip()
        if sp.bbox[0] > list_right_local + 30 and any(kw in t for kw in keywords):
            top_y = win_top + sp.bbox[3] + 8
            notes.append(f"OCR 找到聊天标题「{t}」→ 消息区顶部：y={top_y}")
            return top_y
    return None


def _find_toolbar_by_variance(
    crop: np.ndarray,
    wt_c: int,
    wb_c: int,
    send_button_y: int,
    chat_left_local: int,
    send_local_x: int,
    notes: list[str],
) -> int | None:
    """
    像素方差分析法识别工具栏顶部。

    原理：
      - 消息区（文字/气泡/头像）→ 行内像素方差 **高**
      - 工具栏区（均匀灰色背景 + 少量小图标）→ 行内像素方差 **低**
    策略：从发送键向上扫描，找到"低方差带"（工具栏）的顶端。
    """
    ch, cw = crop.shape[:2]
    win_h = wb_c - wt_c
    send_local_y = send_button_y - wt_c

    # 水平范围与 `_find_toolbar_top` 统一（整块气泡列）
    s_left, s_right = _chat_roi_x_bounds(cw, chat_left_local, send_local_x)

    # 垂直搜索：send_y 上方 3%~55% 窗口高度
    scan_top    = max(0, send_local_y - int(win_h * 0.55))
    scan_bottom = max(0, send_local_y - int(win_h * 0.03))

    if s_right <= s_left + 20 or scan_bottom <= scan_top + 20:
        notes.append("方差法：采样区域过小，跳过")
        return None

    region = crop[scan_top:scan_bottom, s_left:s_right].astype(np.float32)
    # 每行像素灰度均值（三通道取平均）
    gray = np.mean(region, axis=2)          # shape (h, w)
    row_var  = np.var(gray, axis=1)         # 每行方差
    row_mean = np.mean(gray, axis=1)        # 每行均值

    # 5-row 移动平均平滑，减少单行噪声影响
    kernel = np.ones(5, dtype=np.float32) / 5.0
    smooth_var = np.convolve(row_var, kernel, mode="same")

    h = smooth_var.shape[0]
    median_var = float(np.median(smooth_var))
    # 工具栏行：方差 < 中位值×0.45，且亮度较高（背景色 > 170）
    bg_var_thresh = max(median_var * 0.45, 80.0)

    # 从下往上：找连续 ≥12 行的"低方差带"（工具栏），再找它顶部
    bg_run  = 0
    in_bg   = False

    for i in range(h - 1, -1, -1):
        is_bg = (smooth_var[i] < bg_var_thresh) and (row_mean[i] > 170)
        if is_bg:
            bg_run += 1
            if bg_run >= 12:
                in_bg = True
        else:
            if in_bg:
                # 低方差带的顶部（即工具栏顶）
                top_local = i + 1
                abs_y = wt_c + scan_top + top_local
                notes.append(
                    f"方差分析找到工具栏顶：y={abs_y}"
                    f"（低方差带 {bg_run} 行，阈值={bg_var_thresh:.0f}）"
                )
                return abs_y
            bg_run = 0
            in_bg  = False

    notes.append(
        f"方差分析：未找到低方差带（中位方差={median_var:.0f}，阈值={bg_var_thresh:.0f}）"
    )
    return None


def _find_right_panel_boundary(
    spans: list,          # list[OCRSpan] 全窗口 OCR 结果（本地坐标）
    crop: np.ndarray,
    ox: int, oy: int,
    notes: list[str],
) -> dict:
    """
    通过 OCR 结果识别右侧信息面板边界和"客服"蓝色按钮。
    返回 dict with keys: panel_left (全屏 x), service_btn_x, service_btn_y。
    """
    import cv2

    result: dict = {}
    ch, cw = crop.shape[:2]

    # 找"客服"/"接待"等蓝色标签按钮（在窗口右侧 40% 范围内）
    right_region_x = int(cw * 0.6)
    blue_keywords = {"客服", "接待", "商品", "推荐", "订单"}
    for sp in spans:
        if sp.text.strip() in blue_keywords and sp.bbox[0] > right_region_x:
            # 该区域蓝色验证
            x1, y1, x2, y2 = sp.bbox
            pad = 4
            btn_crop = crop[max(0, y1 - pad):y2 + pad, max(0, x1 - pad):x2 + pad]
            if btn_crop.size > 0:
                hsv = cv2.cvtColor(btn_crop, cv2.COLOR_RGB2HSV)
                # v1.6.1 修：千牛新版"客服"按钮从蓝色改成橙色 → 蓝 OR 橙双色识别
                blue_mask = cv2.inRange(hsv,
                                        np.array([95, 80, 80]),
                                        np.array([130, 255, 255]))
                orange_mask = cv2.inRange(hsv,
                                          np.array([5, 100, 100]),
                                          np.array([25, 255, 255]))
                color_mask = cv2.bitwise_or(blue_mask, orange_mask)
                color_ratio = np.count_nonzero(color_mask) / color_mask.size
                if color_ratio > 0.3:   # 30% 以上是蓝/橙 = 确认是按钮
                    btn_cx = ox + (x1 + x2) // 2
                    btn_cy = oy + (y1 + y2) // 2
                    result["service_btn_x"] = btn_cx
                    result["service_btn_y"] = btn_cy
                    # 区分蓝橙便于日志诊断
                    blue_ratio = np.count_nonzero(blue_mask) / blue_mask.size
                    orange_ratio = np.count_nonzero(orange_mask) / orange_mask.size
                    color_tag = "蓝" if blue_ratio >= orange_ratio else "橙"
                    notes.append(
                        f"CV+OCR 找到「{sp.text}」{color_tag}色按钮：({btn_cx}, {btn_cy}) "
                        f"[blue={blue_ratio:.2f} orange={orange_ratio:.2f}]"
                    )
                    # 右侧面板左边界 = 这个按钮所在列的左侧
                    panel_left = ox + x1 - 10
                    result["panel_left"] = panel_left
                    notes.append(f"右侧面板左边界估算：x={panel_left}")
                    break

    return result


def _cv_ocr_calibrate(
    img_arr: np.ndarray,
    sw: int, sh: int,
    win_rect: tuple[int, int, int, int] | None,
    notes: list[str],
) -> CalibrateCoords:
    """
    CV+OCR 混合校准主函数。
    1. 蓝色矩形 + OCR"发送" → 发送按钮
    2. OCR 全窗口 → 找"正在接待"确定会话列表锚点
    3. HoughLines 竖线 → 会话列表右边界
    4. 向上扫灰线 → 工具栏顶部
    5. 几何推算：输入框、聊天 OCR 区、会话列表矩形
    6. 蓝色 + OCR"客服" → 右侧面板边界（可选）
    """
    coords = CalibrateCoords()

    # ── 确定搜索区域 ──────────────────────────────────────────────────────
    if win_rect:
        wl, wt, wr, wb = win_rect
    else:
        wl, wt, wr, wb = 0, 0, sw, sh

    h_arr, w_arr = img_arr.shape[:2]
    wl_c = max(0, wl); wt_c = max(0, wt)
    wr_c = min(w_arr, wr); wb_c = min(h_arr, wb)
    crop = img_arr[wt_c:wb_c, wl_c:wr_c]
    win_w = wr_c - wl_c
    win_h = wb_c - wt_c
    notes.append(f"CV+OCR 搜索窗口：[{wl_c},{wt_c}→{wr_c},{wb_c}] 宽={win_w} 高={win_h}")

    # ── 1. 找发送按钮 ─────────────────────────────────────────────────────
    notes.append("CV：正在检测蓝色发送按钮…")
    send_result = _find_send_button_cv_ocr(crop, wl_c, wt_c, win_h, notes)
    if send_result:
        coords.send_button_x, coords.send_button_y = send_result
        send_lx = coords.send_button_x - wl_c   # 发送键在 crop 内的 x
        send_ly = coords.send_button_y - wt_c

    # ── 2. OCR 整个主窗口，收集所有文字位置 ─────────────────────────────
    notes.append("OCR：识别主窗口文字…")
    spans = _run_rapid_ocr(crop)
    notes.append(f"OCR：识别到 {len(spans)} 个文本块")

    # ── 3. 从 OCR 结果找关键锚点 ──────────────────────────────────────────
    session_anchor_y: int | None = None
    today_reception_sp = None   # 「今日接待」文字块（用于确定聊天区左/上边界）

    # 千牛图标边栏宽度约 60-80px，OCR 锚点必须在此之后
    icon_bar_w = max(70, int(win_w * 0.06))

    for sp in spans:
        t = sp.text.strip()
        # 会话列表顶部锚点（左侧列表列）
        if any(kw in t for kw in ("正在接待", "全部会话", "全部消息", "所有会话")) \
                and session_anchor_y is None:
            session_anchor_y = wt_c + sp.bbox[1]
            notes.append(f"OCR 找到会话列表锚词「{t}」：y={session_anchor_y}")
        # 「今日接待」标题（出现在聊天主列顶部）
        if "今日接待" in t and today_reception_sp is None \
                and sp.bbox[0] > icon_bar_w:
            today_reception_sp = sp
            notes.append(
                f"OCR 找到「今日接待」：bbox={sp.bbox}，"
                f"绝对左={wl_c + sp.bbox[0]}"
            )

    # ── 4. 聊天区左边界：优先用「今日接待」x，其次竖线，最后估算 ─────────
    chat_left_from_ocr: int | None = None
    if today_reception_sp is not None:
        # 「今日接待」左端 x − 5px 作为聊天区左边界
        chat_left_from_ocr = wl_c + today_reception_sp.bbox[0] - 5
        notes.append(f"OCR 聊天区左边界（今日接待 x）：{chat_left_from_ocr}")

    list_right = _find_vertical_separator(crop, wl_c, wt_c, notes)
    if list_right is None:
        if chat_left_from_ocr is not None:
            # 用「今日接待」x 推算出会话列表右边界（往左退 2px）
            list_right = chat_left_from_ocr - 2
            notes.append(f"CV：以「今日接待」x 估算会话列表右边界：x={list_right}")
        else:
            list_right = wl_c + int(win_w * 0.22)
            notes.append(f"CV：未找到分隔线，估算会话列表右边界：x={list_right}")

    # 最终聊天区左边界 = OCR 精确值 or 竖线 + 2px
    chat_left_final = chat_left_from_ocr if chat_left_from_ocr is not None \
        else (list_right + 2)

    # ── 5. 向上扫描 → 工具栏顶部 ─────────────────────────────────────────
    toolbar_top: int | None = None
    chat_left_local = (chat_left_final - wl_c) if chat_left_final else int(win_w * 0.15)

    # 方法①：整列 Sobel + 上下行方差对比（找消息区底边）
    if coords.send_button_x and coords.send_button_y:
        toolbar_top = _find_toolbar_top(
            crop, wl_c, wt_c,
            coords.send_button_x - wl_c,
            coords.send_button_y - wt_c,
            chat_left_local,
            notes,
        )

    # 方法②：像素方差带（表情栏灰底）
    if toolbar_top is None and coords.send_button_y and coords.send_button_x:
        toolbar_top = _find_toolbar_by_variance(
            crop, wt_c, wb_c,
            coords.send_button_y,
            chat_left_local,
            coords.send_button_x - wl_c,
            notes,
        )

    # 方法③：固定偏移兜底（千牛输入区高度约 155px）
    if toolbar_top is None:
        if coords.send_button_y:
            toolbar_top = max(wt_c, coords.send_button_y - 155)
        else:
            toolbar_top = wb_c - int(win_h * 0.22)
        notes.append(f"CV：估算工具栏顶部（send_y-155）：y={toolbar_top}")

    # ── 6. 右侧面板边界（客服按钮）──────────────────────────────────────
    panel_info = _find_right_panel_boundary(spans, crop, wl_c, wt_c, notes)

    # ── 6b. 聊天区顶部（今日接待标题行底部）─────────────────────────────
    chat_header_bottom: int | None = None
    if today_reception_sp is not None:
        chat_header_bottom = wt_c + today_reception_sp.bbox[3] + 8
        notes.append(f"OCR 聊天区顶部（今日接待底部+8）：y={chat_header_bottom}")
    else:
        # 用传统方法补找
        list_right_local = list_right - wl_c
        chat_header_bottom = _find_chat_header_bottom(spans, list_right_local, wt_c, notes)

    # ── 7. 几何推算所有坐标 ──────────────────────────────────────────────
    title_bar_h = 90   # 默认标题栏+tab 区高度（兜底值）

    # v1.3.92：right 优先级 panel_left（OCR 找到"客服"按钮）> send_button.x+30
    # 之前回退到 wr_c（窗口右边）会把右侧客户信息面板也圈进去——严禁
    if panel_info.get("panel_left"):
        chat_right = panel_info["panel_left"]
    elif coords.send_button_x:
        chat_right = coords.send_button_x + 30
    else:
        # 最后兜底：用窗口宽度的 65% 作为右边界（千牛默认布局下聊天区约占 65%）
        chat_right = wl_c + int(win_w * 0.65)
        notes.append(f"⚠ CV：未找到'客服'按钮也无发送键，right 用窗口 65% 兜底={chat_right}")
    chat_left   = chat_left_final
    chat_top    = chat_header_bottom or (wt_c + title_bar_h)
    # 消息区底 = 工具栏顶横线略上方（留 1～2px 避免抗锯齿压线）
    _margin = 2
    chat_bottom = (toolbar_top - _margin) if toolbar_top else (wb_c - int(win_h * 0.22))

    if chat_right > chat_left + 50 and chat_bottom > chat_top + 50:
        coords.ocr_chat_left   = chat_left
        coords.ocr_chat_top    = chat_top
        coords.ocr_chat_right  = chat_right
        coords.ocr_chat_bottom = chat_bottom
        coords.chat_scroll_x   = (chat_left + chat_right) // 2
        coords.chat_scroll_y   = (chat_top + chat_bottom) // 2
        notes.append(f"聊天 OCR 区：[{chat_left},{chat_top}→{chat_right},{chat_bottom}]")
        if toolbar_top is not None:
            notes.append(
                "【为何每次底边像素常完全一致】校准为纯确定性流程（无随机数）："
                "同一分辨率、同一窗口位置与界面内容时，每帧 Sobel/OCR 输入一致，"
                "中间结果 toolbar_top 就相同；再由固定公式 chat_bottom=toolbar_top−"
                f"{_margin} 推算，因此可逐像素复现。"
            )
            notes.append(
                "若日志里仍是旧版措辞「CV Sobel 找到工具栏顶部边缘」，"
                "表示当前运行的 exe 未包含「整列方差打分」新版本，请重新打包后再测。"
            )

    # 会话列表矩形
    coords.session_list_left   = wl_c
    coords.session_list_top    = session_anchor_y or (wt_c + title_bar_h)
    coords.session_list_right  = list_right
    coords.session_list_bottom = wb_c

    # 输入框中心：工具栏区域水平中点
    if toolbar_top and coords.send_button_x:
        input_x = (chat_left + coords.send_button_x) // 2
        input_y = toolbar_top + (wb_c - toolbar_top) // 2
        coords.input_box_x = input_x
        coords.input_box_y = input_y
        notes.append(f"输入框推算坐标：({input_x}, {input_y})")

    # 客服/接待按钮
    if "service_btn_x" in panel_info:
        coords.service_btn_x = panel_info.get("service_btn_x")
        coords.service_btn_y = panel_info.get("service_btn_y")
        if "panel_left" in panel_info:
            coords.right_panel_left = panel_info["panel_left"]

    return coords


# ── 方案D：本地OCR关键词定位 ────────────────────────────────────────────
_QIANNIU_ANCHORS = {
    "全部会话": "session_top",
    "全部消息": "session_top",
    "正在接待": "session_top",    # 千牛接待中心顶部标签
    "所有会话": "session_top",
    "待回复": "session_top",
    "发　送": "send_btn",
    "发送": "send_btn",
    "输入商品": "input_hint",
    "表情": "input_hint",
    "图片": "input_hint",
}


def _run_fullscreen_ocr(arr: np.ndarray) -> list[tuple[str, int, int, int, int]]:
    """
    用 RapidOCR 对整张截图做 OCR。
    返回 [(text, left, top, right, bottom), ...]
    """
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        result, _ = ocr(arr)
        if not result:
            return []
        boxes = []
        for item in result:
            pts, text, _conf = item[0], item[1], item[2]
            xs = [int(p[0]) for p in pts]
            ys = [int(p[1]) for p in pts]
            boxes.append((str(text), min(xs), min(ys), max(xs), max(ys)))
        return boxes
    except Exception:
        return []


def _ocr_calibrate(
    arr: np.ndarray,
    screen_w: int,
    screen_h: int,
) -> tuple[CalibrateCoords, list[str]]:
    """
    通过 OCR 找到关键词，用启发式规则推算各坐标区域。
    返回 (coords, notes)
    """
    boxes = _run_fullscreen_ocr(arr)
    notes: list[str] = []
    coords = CalibrateCoords()

    # 找发送按钮
    # 只取屏幕下半部分（y > 55%）的「发送」——排除右上角操作按钮
    send_boxes = [
        b for b in boxes
        if b[0].replace(" ", "") in ("发送", "发　送")
        and (b[2] + b[4]) // 2 > screen_h * 0.55
    ]
    _session_kw = ("全部会话", "全部消息", "所有会话", "正在接待", "待回复", "全部")
    session_top_boxes = [
        b for b in boxes
        if any(kw in b[0].replace(" ", "") for kw in _session_kw)
    ]
    # 取 y 最小（最靠顶部）的，作为会话列表的顶端锚点
    session_top_boxes.sort(key=lambda b: b[2])
    input_hint_boxes = [
        b for b in boxes if any(k in b[0] for k in ("输入商品", "表情", "图片", "文件"))
    ]

    # ── 多窗口判断：以 x 坐标聚类，簇间距 > 300px 视为不同窗口 ──────────────
    def _cluster_by_x(boxes, gap=300):
        """按 x 中心坐标聚类，返回簇列表（每簇内按 y 降序取第一个=最靠底）。"""
        if not boxes:
            return []
        sorted_b = sorted(boxes, key=lambda b: (b[1] + b[3]) // 2)
        clusters: list[list] = [[sorted_b[0]]]
        for b in sorted_b[1:]:
            cx = (b[1] + b[3]) // 2
            if cx - (clusters[-1][-1][1] + clusters[-1][-1][3]) // 2 > gap:
                clusters.append([])
            clusters[-1].append(b)
        # 每簇取 y 最大（最靠底）
        return [max(c, key=lambda b: b[2]) for c in clusters]

    send_clusters = _cluster_by_x(send_boxes)
    notes.append(f"找到「发送」文字 {len(send_boxes)} 处，聚类为 {len(send_clusters)} 个窗口")

    if len(send_clusters) == 1:
        # 单窗口：直接使用
        sb = send_clusters[0]
        coords.send_button_x = (sb[1] + sb[3]) // 2
        coords.send_button_y = (sb[2] + sb[4]) // 2
        notes.append(f"单窗口，发送按钮：({coords.send_button_x}, {coords.send_button_y})")
    elif len(send_clusters) > 1:
        # 多窗口：无法自动决定，返回特殊标记让 UI 层触发手动点击
        notes.append(f"检测到 {len(send_clusters)} 个窗口，需用户手动点击确认发送按钮")
        coords.send_button_x = None  # 显式置 None，触发 UI 多窗口流程
        coords.send_button_y = None
    else:
        notes.append("未找到「发送」按钮")

    if coords.send_button_x is not None:
        sb = send_clusters[0]
        # 输入框：发送按钮左侧同行
        if input_hint_boxes:
            ih = input_hint_boxes[0]
            coords.input_box_x = (ih[1] + ih[3]) // 2
            coords.input_box_y = (ih[2] + ih[4]) // 2
        else:
            coords.input_box_x = max(0, sb[1] - 200)
            coords.input_box_y = coords.send_button_y
        coords.chat_scroll_x = coords.send_button_x - 100
        coords.chat_scroll_y = coords.send_button_y - 200

        # 聊天区 OCR：会话列表右侧 → 发送按钮上方
        chat_left_guess = 260
        if session_top_boxes:
            st = session_top_boxes[0]
            chat_left_guess = st[3] + 20
        coords.ocr_chat_left = chat_left_guess
        coords.ocr_chat_top = 100
        coords.ocr_chat_right = sb[3] + 20
        coords.ocr_chat_bottom = sb[2] - 10
        notes.append(
            f"推算聊天区 OCR: ({coords.ocr_chat_left},{coords.ocr_chat_top})"
            f" → ({coords.ocr_chat_right},{coords.ocr_chat_bottom})"
        )

    # 会话列表
    if session_top_boxes:
        st = session_top_boxes[0]
        coords.session_list_left = max(0, st[1] - 10)
        coords.session_list_top = st[2]
        coords.session_list_right = st[3] + 20
        coords.session_list_bottom = screen_h - 50
        notes.append(
            f"推算会话列表: ({coords.session_list_left},{coords.session_list_top})"
            f" → ({coords.session_list_right},{coords.session_list_bottom})"
        )
    else:
        notes.append("未找到「全部会话」，会话列表区域未识别")

    return coords, notes


# ── 方案A：Vision AI 识别 ──────────────────────────────────────────────────
def _build_vision_prompt() -> str:
    """让模型返回 0.0~1.0 的归一化坐标，避开图像缩放带来的坐标偏差。"""
    return """
这是一张电脑屏幕截图（千牛客服工作台）。请识别其中的 UI 元素，并以**归一化坐标**返回。

【坐标说明 — 严格遵守】
- 所有数值必须是 0.0 到 1.0 之间的**浮点小数**，代表元素在图像上的相对位置：
    x = 该元素的水平像素位置 ÷ 图像宽度
    y = 该元素的垂直像素位置 ÷ 图像高度
- 例如：屏幕正中心是 (0.5, 0.5)；右下角是 (1.0, 1.0)
- 千牛窗口在右半屏的话，输入框 x 应该 > 0.5

【UI 元素识别】
千牛聊天窗口通常很大，有几个特征：左侧一列竖排的客户头像列表、中部气泡消息区、底部多行文本输入框和"发送"按钮。

- input_box_x, input_box_y: 底部多行「文本输入框」的中心
- send_button_x, send_button_y: 输入框附近的「发送」按钮（蓝色或绿色矩形按钮）中心
- chat_scroll_x, chat_scroll_y: 消息气泡显示区的中心
- ocr_chat_left/top/right/bottom: 消息气泡显示区的矩形范围（**最关键字段**）
    * left: 紧贴会话列表右边缘（左侧客户列表的右侧分隔线，约会话列表 x_max）
    * top:  顶部"今日接待 / 未下单 / 未付款 / 已付款"这一行 tab 标签的**下方**
    * right: **紧贴右侧蓝色"客服"按钮的左边缘**——千牛主窗口右侧有一个蓝底白字
             的"客服"按钮（带喇叭图标），它的左边缘就是聊天区与"客户信息面板"的
             分界。**绝对不要**把右边的"店铺身份/邀请留资/足迹推荐/历史订单"
             区域也圈进去——那是客户信息面板，不是聊天区！
    * bottom: **必须紧贴底部输入框工具栏的上沿**（即表情/图片/转账等图标那一行
             的上方边线）。不能在工具栏上方留空白，否则会切掉最新一条消息！
- session_list_left/top/right/bottom: 屏幕左侧那一列竖排会话列表的矩形

【输出格式】只返回 JSON，没有任何说明文字、markdown 或代码块标记。所有数值都是 0.0~1.0 之间的小数，不可见的字段填 null：
{"input_box_x":null,"input_box_y":null,"send_button_x":null,"send_button_y":null,"chat_scroll_x":null,"chat_scroll_y":null,"ocr_chat_left":null,"ocr_chat_top":null,"ocr_chat_right":null,"ocr_chat_bottom":null,"session_list_left":null,"session_list_top":null,"session_list_right":null,"session_list_bottom":null}
""".strip()


def _denormalize_coords(coords: "CalibrateCoords", sw: int, sh: int) -> None:
    """把 0.0~1.0 的归一化坐标乘以真实分辨率，转为像素坐标（in-place）。
    若发现已经是大于 1 的整数（模型没听话），直接保留原值。"""
    def conv(v, scale):
        if v is None:
            return None
        # 0~1 浮点 → 像素
        if isinstance(v, float) and 0.0 <= v <= 1.0:
            return int(round(v * scale))
        # 已经是像素坐标（模型返回了整数/超过 1 的值）→ 保留
        try:
            iv = int(v)
            if iv <= 1:                  # 0 或 1 也按归一化处理
                return int(round(float(v) * scale))
            return iv
        except (TypeError, ValueError):
            return None

    coords.input_box_x      = conv(coords.input_box_x,      sw)
    coords.input_box_y      = conv(coords.input_box_y,      sh)
    coords.send_button_x    = conv(coords.send_button_x,    sw)
    coords.send_button_y    = conv(coords.send_button_y,    sh)
    coords.chat_scroll_x    = conv(coords.chat_scroll_x,    sw)
    coords.chat_scroll_y    = conv(coords.chat_scroll_y,    sh)
    coords.ocr_chat_left    = conv(coords.ocr_chat_left,    sw)
    coords.ocr_chat_top     = conv(coords.ocr_chat_top,     sh)
    coords.ocr_chat_right   = conv(coords.ocr_chat_right,   sw)
    coords.ocr_chat_bottom  = conv(coords.ocr_chat_bottom,  sh)
    coords.session_list_left   = conv(coords.session_list_left,   sw)
    coords.session_list_top    = conv(coords.session_list_top,    sh)
    coords.session_list_right  = conv(coords.session_list_right,  sw)
    coords.session_list_bottom = conv(coords.session_list_bottom, sh)


def _pick_vision_model(settings) -> str:
    """
    选择视觉能力最强的已配置模型：
    优先 deep_analysis 模型（通常是 claude-sonnet），其次 front_desk，再退回 gpt-4o。
    """
    from apps.core.ai.llm_client import resolve_litellm_api_key

    candidates = [
        getattr(settings, "model_deep_analysis", ""),
        getattr(settings, "model_front_desk", ""),
        "openai/claude-sonnet-4-6-thinking",   # v1.3.91：thinking 模型视觉精度更高
        "anthropic/claude-sonnet-4-6",
        "openai/gpt-4o",
    ]
    for m in candidates:
        m = (m or "").strip()
        if not m:
            continue
        # 只选具备视觉能力的模型（thinking/sonnet 走 OpenAI 兼容网关也算）
        ml = m.lower()
        if not any(k in ml for k in ("claude", "sonnet", "gpt-4o", "gemini", "vision")):
            continue
        key = resolve_litellm_api_key(settings, m)
        if key:
            return m
    return ""


def _vision_calibrate(
    png_bytes: bytes,
    settings=None,
    sw: int = 0,
    sh: int = 0,
) -> tuple[CalibrateCoords, list[str]]:
    """用 litellm 路由的视觉模型（优先 Claude Sonnet）识别坐标。"""
    notes: list[str] = []
    coords = CalibrateCoords()

    # 若调用方未传，从 PNG 实际像素读出
    if not sw or not sh:
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(png_bytes)) as _img:
                sw, sh = _img.size
        except Exception:
            sw = sw or 1920
            sh = sh or 1080

    try:
        from apps.core.configs.base_settings import load_base_settings
        from apps.core.ai.llm_client import litellm_completion_vision_image

        st = settings or load_base_settings()
        model = _pick_vision_model(st)
        if not model:
            notes.append("未找到可用的视觉模型（需配置 Claude/GPT-4o/Gemini 其中一个 API Key）")
            return coords, notes

        notes.append(f"使用视觉模型：{model}（图像 {sw}×{sh}）")
        raw_text = litellm_completion_vision_image(
            settings=st,
            model=model,
            system=(
                "你是 UI 元素位置识别助手。"
                "你必须只返回严格的 JSON 格式，所有坐标都是 0.0~1.0 之间的归一化浮点小数（图像比例位置）。"
                "禁止 markdown、解释、注释、代码块标记。"
            ),
            user_text=_build_vision_prompt(),
            image_mime="image/png",
            image_bytes=png_bytes,
            max_tokens=600,
            temperature=0.0,
        )

        m = re.search(r"\{[\s\S]+\}", raw_text)
        if not m:
            notes.append(f"模型返回格式无法解析: {raw_text[:200]}")
            return coords, notes

        data = json.loads(m.group())

        def _val(k: str):
            v = data.get(k)
            return v if v is not None else None

        # 先存原始（可能是 float 0~1，也可能是误返回的整数像素）
        coords.input_box_x      = _val("input_box_x")
        coords.input_box_y      = _val("input_box_y")
        coords.send_button_x    = _val("send_button_x")
        coords.send_button_y    = _val("send_button_y")
        coords.chat_scroll_x    = _val("chat_scroll_x")
        coords.chat_scroll_y    = _val("chat_scroll_y")
        coords.ocr_chat_left    = _val("ocr_chat_left")
        coords.ocr_chat_top     = _val("ocr_chat_top")
        coords.ocr_chat_right   = _val("ocr_chat_right")
        coords.ocr_chat_bottom  = _val("ocr_chat_bottom")
        coords.session_list_left   = _val("session_list_left")
        coords.session_list_top    = _val("session_list_top")
        coords.session_list_right  = _val("session_list_right")
        coords.session_list_bottom = _val("session_list_bottom")

        # 显示原始返回方便诊断
        sample = (coords.input_box_x, coords.input_box_y,
                  coords.send_button_x, coords.send_button_y)
        notes.append(f"第一轮原始值（输入框/发送键）: {sample}")

        # 把归一化坐标乘以真实分辨率，得到物理像素
        _denormalize_coords(coords, sw, sh)
        notes.append("第一轮已转换为物理像素坐标")

        # ── 第 1.5 轮：聊天区精修（v1.3.91 新增）─────────────────────────────
        # 用 UIA 窗口边界裁剪整个千牛主窗口，让 AI 专门精修 ocr_chat_* 四个边
        # 解决"bottom 切掉最新一条消息"的核心问题
        _win_for_crop = _locate_qianniu_window_uia()
        if _win_for_crop:
            _wl, _wt, _wr, _wb = _win_for_crop
            try:
                import io as _io_cc
                from PIL import Image as _PIL_cc
                _full = _PIL_cc.open(_io_cc.BytesIO(png_bytes))
                _crop = _full.crop((_wl, _wt, _wr, _wb))
                _cw, _ch = _crop.size
                _scale_cc = max(1.0, 1200 / _cw)
                if _scale_cc > 1.0:
                    _crop = _crop.resize(
                        (int(_cw * _scale_cc), int(_ch * _scale_cc)),
                        _PIL_cc.LANCZOS,
                    )
                _buf_cc = _io_cc.BytesIO()
                _crop.save(_buf_cc, format="PNG")
                _crop_bytes = _buf_cc.getvalue()
                _prompt_cc = (
                    "这是千牛客服工作台主窗口的截图（已放大）。请精准识别"
                    "**消息气泡显示区**（即买家/卖家对话气泡所在的中间区域）"
                    "的矩形，用 0.0~1.0 归一化坐标（相对于本截图）：\n\n"
                    "- left:   紧贴左侧会话列表的右边缘（竖直分隔线）。\n"
                    "- top:    紧贴顶部'今日接待 / 未下单 / 未付款 / 已付款'\n"
                    "          那一行 tab 标签的**下方**。\n"
                    "- right:  **紧贴右侧蓝色'客服'按钮的左边缘**！\n"
                    "          千牛右侧有一个蓝底白字的'客服'按钮（带喇叭图标），\n"
                    "          它的左边缘就是聊天区与'客户信息面板'的分界线。\n"
                    "          **绝对不要**把右边的'店铺身份/邀请留资/足迹/推荐/\n"
                    "          历史订单/商品图'区域圈进去——那是客户信息面板，\n"
                    "          不属于聊天气泡区！\n"
                    "- bottom: **必须紧贴底部输入框工具栏的上沿**——即表情/图片\n"
                    "          /转账等图标那一行的上方。\n"
                    "          **关键：宁可多包含几像素工具栏顶部，也绝不能切掉\n"
                    "          聊天区最后一条消息！这是最常见的错误！**\n\n"
                    "只返回 JSON，无其他内容：\n"
                    '{"ocr_chat_left":null,"ocr_chat_top":null,'
                    '"ocr_chat_right":null,"ocr_chat_bottom":null}'
                )
                _raw_cc = litellm_completion_vision_image(
                    settings=st,
                    model=model,
                    system="只返回 JSON，所有坐标 0.0~1.0。",
                    user_text=_prompt_cc,
                    image_mime="image/png",
                    image_bytes=_crop_bytes,
                    max_tokens=120,
                    temperature=0.0,
                )
                _m_cc = re.search(r"\{[\s\S]+\}", _raw_cc)
                if _m_cc:
                    _d_cc = json.loads(_m_cc.group())
                    _crop_w = _wr - _wl
                    _crop_h = _wb - _wt

                    def _norm_to_screen_x(v):
                        if v is None:
                            return None
                        try:
                            f = float(v)
                            if 0.0 <= f <= 1.0:
                                return int(_wl + f * _crop_w)
                        except (TypeError, ValueError):
                            pass
                        return None

                    def _norm_to_screen_y(v):
                        if v is None:
                            return None
                        try:
                            f = float(v)
                            if 0.0 <= f <= 1.0:
                                return int(_wt + f * _crop_h)
                        except (TypeError, ValueError):
                            pass
                        return None

                    _new_l = _norm_to_screen_x(_d_cc.get("ocr_chat_left"))
                    _new_t = _norm_to_screen_y(_d_cc.get("ocr_chat_top"))
                    _new_r = _norm_to_screen_x(_d_cc.get("ocr_chat_right"))
                    _new_b = _norm_to_screen_y(_d_cc.get("ocr_chat_bottom"))
                    if all(v is not None for v in (_new_l, _new_t, _new_r, _new_b)):
                        notes.append(
                            f"第1.5轮：聊天区精修 "
                            f"[{_new_l},{_new_t}→{_new_r},{_new_b}]"
                        )
                        coords.ocr_chat_left   = _new_l
                        coords.ocr_chat_top    = _new_t
                        coords.ocr_chat_right  = _new_r
                        coords.ocr_chat_bottom = _new_b
                    else:
                        notes.append(f"第1.5轮：精修返回不全 {_d_cc}")
                else:
                    notes.append(f"第1.5轮：精修返回无 JSON: {_raw_cc[:120]}")
            except Exception as _e_cc:
                notes.append(f"第1.5轮：精修异常 {_e_cc!r}")

        # ── 第二轮：裁剪底部工具栏放大，精准定位输入框和发送键 ─────────────
        # 优先用 UIA 拿到千牛主窗口矩形（最可靠），失败则退到第一轮 chat_bottom
        win_rect = _locate_qianniu_window_uia()
        if win_rect:
            wl, wt, wr, wb = win_rect
            notes.append(f"UIA 定位千牛主窗口：[{wl},{wt} → {wr},{wb}]")
            coords.qianniu_window_left   = wl
            coords.qianniu_window_top    = wt
            coords.qianniu_window_right  = wr
            coords.qianniu_window_bottom = wb
            # 工具栏 = 窗口底部往上 25% 高度（包含输入框 + 发送键）
            win_h = wb - wt
            toolbar_top    = max(0, wb - int(win_h * 0.30))
            toolbar_bottom = min(sh, wb)
            toolbar_left   = max(0, wl)
            toolbar_right  = min(sw, wr)
        else:
            chat_bottom_px = coords.ocr_chat_bottom
            chat_right_px  = coords.ocr_chat_right
            chat_left_px   = coords.session_list_right or coords.ocr_chat_left or 0
            if not chat_bottom_px or not chat_right_px:
                toolbar_top = toolbar_bottom = toolbar_left = toolbar_right = 0
            else:
                toolbar_top    = max(0, chat_bottom_px + 5)
                toolbar_bottom = min(sh, chat_bottom_px + int(sh * 0.15))
                toolbar_left   = max(0, chat_left_px)
                toolbar_right  = min(sw, chat_right_px + 100)

        if toolbar_right > toolbar_left and toolbar_bottom > toolbar_top:

            if toolbar_bottom > toolbar_top + 30:  # 工具栏至少 30px 高才裁
                notes.append(
                    f"第二轮：裁剪底部工具栏区域 "
                    f"[{toolbar_left},{toolbar_top}→{toolbar_right},{toolbar_bottom}]"
                )
                try:
                    import io as _io2
                    from PIL import Image as _PILImg
                    full_img = _PILImg.open(_io2.BytesIO(png_bytes))
                    crop = full_img.crop((toolbar_left, toolbar_top, toolbar_right, toolbar_bottom))
                    crop_w, crop_h = crop.size
                    # 放大到至少 800px 宽，提升模型识别精度
                    scale_up = max(1.0, 800 / crop_w)
                    if scale_up > 1:
                        new_w = int(crop_w * scale_up)
                        new_h = int(crop_h * scale_up)
                        crop = crop.resize((new_w, new_h), _PILImg.LANCZOS)
                    crop_buf = _io2.BytesIO()
                    crop.save(crop_buf, format="PNG")
                    crop_bytes = crop_buf.getvalue()

                    prompt2 = (
                        "这是千牛客服工作台底部工具栏区域的截图（已放大）。"
                        "请识别：\n"
                        "1. 文本输入框中心 (input_box_x, input_box_y) —— 客服输入回复的多行文本区域\n"
                        "2. 发送按钮中心 (send_button_x, send_button_y) —— 通常是蓝色/绿色的「发送」按钮\n"
                        "用 0.0~1.0 归一化坐标。只返回 JSON，无其他内容：\n"
                        '{"input_box_x":null,"input_box_y":null,"send_button_x":null,"send_button_y":null}'
                    )
                    raw2 = litellm_completion_vision_image(
                        settings=st,
                        model=model,
                        system="只返回 JSON，所有坐标 0.0~1.0。",
                        user_text=prompt2,
                        image_mime="image/png",
                        image_bytes=crop_bytes,
                        max_tokens=120,
                        temperature=0.0,
                    )
                    m2 = re.search(r"\{[\s\S]+\}", raw2)
                    if m2:
                        d2 = json.loads(m2.group())
                        def _crop_to_screen(val, axis_size, crop_origin, crop_size):
                            if val is None:
                                return None
                            try:
                                fv = float(val)
                                if fv < 0 or fv > 1:
                                    return None
                                # 裁剪坐标 → 全屏坐标
                                return int(crop_origin + fv * crop_size)
                            except (TypeError, ValueError):
                                return None

                        ib_x = _crop_to_screen(d2.get("input_box_x"), sw, toolbar_left, toolbar_right - toolbar_left)
                        ib_y = _crop_to_screen(d2.get("input_box_y"), sh, toolbar_top, toolbar_bottom - toolbar_top)
                        sb_x = _crop_to_screen(d2.get("send_button_x"), sw, toolbar_left, toolbar_right - toolbar_left)
                        sb_y = _crop_to_screen(d2.get("send_button_y"), sh, toolbar_top, toolbar_bottom - toolbar_top)

                        if ib_x and ib_y:
                            coords.input_box_x = ib_x
                            coords.input_box_y = ib_y
                            notes.append(f"第二轮输入框精确坐标：({ib_x}, {ib_y})")
                        if sb_x and sb_y:
                            coords.send_button_x = sb_x
                            coords.send_button_y = sb_y
                            notes.append(f"第二轮发送键精确坐标：({sb_x}, {sb_y})")
                except Exception as e2:
                    notes.append(f"第二轮识别失败（不影响其他坐标）: {e2}")

    except Exception as e:
        notes.append(f"视觉模型调用失败: {e}")

    return coords, notes


# ── 合并两种方案的结果 ────────────────────────────────────────────────────
def _merge(primary: CalibrateCoords, fallback: CalibrateCoords) -> CalibrateCoords:
    """primary 中 None 的字段用 fallback 补填。"""
    merged = CalibrateCoords()
    for f in primary.__dataclass_fields__:
        v = getattr(primary, f)
        merged.__dict__[f] = v if v is not None else getattr(fallback, f)
    return merged


# ── v1.3.97：AI 复检闭环 ─────────────────────────────────────────────────
def _render_annotated_for_qc(coords: CalibrateCoords, png_bytes: bytes) -> bytes | None:
    """在原截图上画出 CV+OCR 识别的红/蓝/绿/橙/紫标注，返回 PNG bytes。
    给 Vision AI 复检用——AI 看带标注图判断对错远比从空白截图猜坐标准确。"""
    if not png_bytes:
        return None
    try:
        import io
        from PIL import Image, ImageDraw

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        draw = ImageDraw.Draw(img, "RGBA")

        def draw_rect(l, t, r, b, color, label):
            if None in (l, t, r, b):
                return
            draw.rectangle([l, t, r, b], outline=color + (240,), width=4)
            draw.rectangle([l, t - 26, l + len(label) * 14 + 8, t], fill=color + (220,))
            draw.text((l + 4, t - 24), label, fill=(255, 255, 255, 255))

        def draw_point(x, y, color, label):
            if x is None or y is None:
                return
            r = 16
            draw.ellipse([x - r, y - r, x + r, y + r],
                         fill=color + (200,), outline=color + (255,), width=3)
            draw.line([x - 24, y, x + 24, y], fill=color + (255,), width=2)
            draw.line([x, y - 24, x, y + 24], fill=color + (255,), width=2)
            draw.rectangle([x + r + 3, y - 14, x + r + 3 + len(label) * 12 + 6, y + 12],
                           fill=(0, 0, 0, 180))
            draw.text((x + r + 6, y - 13), label, fill=(255, 255, 255, 255))

        draw_rect(coords.ocr_chat_left, coords.ocr_chat_top,
                  coords.ocr_chat_right, coords.ocr_chat_bottom,
                  (255, 60, 60), "红框=聊天OCR区")
        draw_rect(coords.session_list_left, coords.session_list_top,
                  coords.session_list_right, coords.session_list_bottom,
                  (60, 140, 255), "蓝框=会话列表")
        draw_point(coords.input_box_x, coords.input_box_y,
                   (50, 205, 50), "绿点=输入框")
        draw_point(coords.send_button_x, coords.send_button_y,
                   (255, 140, 0), "橙点=发送键")
        draw_point(coords.chat_scroll_x, coords.chat_scroll_y,
                   (180, 100, 255), "紫点=滚动")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _vision_qc_review(
    annotated_png: bytes,
    sw: int, sh: int,
    win_rect: tuple[int, int, int, int] | None,
    settings,
    current_coords: CalibrateCoords,
) -> tuple[bool, list[str], dict]:
    """让 AI 看带标注的截图，判断 CV+OCR 识别是否正确。
    返回 (ok, issues, fixes)：
      ok=True 时 issues=[]，fixes={}
      ok=False 时 fixes 字典 key=字段名（如 ocr_chat_right），value=屏幕物理像素值
    """
    if not annotated_png:
        return True, [], {}
    try:
        from apps.core.ai.llm_client import litellm_completion_vision_image

        model = _pick_vision_model(settings)
        if not model:
            return True, [], {}  # 无 API key，跳过复检

        prompt = (
            "你是 UI 校准质检员。这是一张千牛客服工作台截图，CV 算法已经画好了识别"
            "结果的标注（红框/蓝框/绿点/橙点/紫点）。请逐项判断每个标注是否在正确位置。\n\n"
            "【判断标准】\n"
            "- 红框 = 聊天OCR区：必须覆盖中间消息气泡区，且：\n"
            "  * left 紧贴左侧会话列表右边缘（不能盖到会话列表）\n"
            "  * top  紧贴顶部'今日接待/未下单/未付款/已付款'下方\n"
            "  * right 紧贴右侧蓝色'客服'按钮的左边缘（**不能盖到右侧客户信息面板的"
            "          推荐商品/足迹/历史订单！这是最常见错误！**）\n"
            "  * bottom 紧贴底部输入工具栏上沿（不能切掉最新消息！）\n"
            "- 蓝框 = 会话列表：覆盖左侧客户头像列表\n"
            "- 绿点 = 输入框中心：在底部多行文本输入区域\n"
            "- 橙点 = 发送键：必须在蓝色'发送'按钮中心（不要飘到右侧客户面板的按钮上）\n"
            "- 紫点 = 滚动点：聊天消息区中央\n\n"
            "【输出格式】只返回 JSON：\n"
            "如果所有标注都准确，返回：{\"ok\":true,\"issues\":[]}\n"
            "如果有问题，返回：\n"
            "{\"ok\":false, \"issues\":[\"...\"], \"fixes\":{"
            "\"ocr_chat_right\":<0~1>, \"send_button_x\":<0~1>, ...}}\n"
            "fixes 里只列**需要修正**的字段，值是相对**整张截图**的 0~1 归一化坐标。\n"
            "x 字段用 0~1（屏幕宽方向），y 字段用 0~1（屏幕高方向）。"
        )
        raw = litellm_completion_vision_image(
            settings=settings,
            model=model,
            system="你是 UI 质检员，只返回严格 JSON。",
            user_text=prompt,
            image_mime="image/png",
            image_bytes=annotated_png,
            max_tokens=500,
            temperature=0.0,
        )
        m = re.search(r"\{[\s\S]+\}", raw or "")
        if not m:
            return True, [f"QC 模型返回无 JSON: {(raw or '')[:120]}"], {}
        data = json.loads(m.group())
        ok = bool(data.get("ok", True))
        issues = list(data.get("issues") or [])
        raw_fixes = data.get("fixes") or {}

        # 0~1 归一化 → 屏幕物理像素
        fixes: dict = {}
        for k, v in raw_fixes.items():
            try:
                fv = float(v)
                if not (0.0 <= fv <= 1.0):
                    continue
                if k.endswith(("_x", "_left", "_right")):
                    fixes[k] = int(round(fv * sw))
                elif k.endswith(("_y", "_top", "_bottom")):
                    fixes[k] = int(round(fv * sh))
            except (TypeError, ValueError):
                continue

        return ok, issues, fixes
    except Exception as e:
        return True, [f"QC 调用异常（不影响主流程）: {e!r}"], {}


def _apply_qc_fixes(coords: CalibrateCoords, fixes: dict) -> CalibrateCoords:
    """把 AI QC 给的修正值应用到 coords，返回新副本。"""
    if not fixes:
        return coords
    new = CalibrateCoords()
    for f in coords.__dataclass_fields__:
        v = getattr(coords, f)
        if f in fixes:
            new.__dict__[f] = fixes[f]
        else:
            new.__dict__[f] = v
    return new


# ── v1.6.3 anchor 预测路径 ───────────────────────────────────────────────
def _predict_coords_from_anchor(anchor, win_rect) -> CalibrateCoords:
    """anchor + 当前窗口 → 预测出的 CalibrateCoords。"""
    from apps.core.automation.anchor_calibrate import predict_points, predict_rects

    coords = CalibrateCoords()
    pts = predict_points(anchor, win_rect)
    rects = predict_rects(anchor, win_rect)

    if "input_box_point" in pts:
        coords.input_box_x, coords.input_box_y = pts["input_box_point"]
    if "send_button_point" in pts:
        coords.send_button_x, coords.send_button_y = pts["send_button_point"]
    if "chat_scroll_point" in pts:
        coords.chat_scroll_x, coords.chat_scroll_y = pts["chat_scroll_point"]
    if "service_btn_point" in pts:
        coords.service_btn_x, coords.service_btn_y = pts["service_btn_point"]
    if "session_list_rect" in rects:
        (coords.session_list_left, coords.session_list_top,
         coords.session_list_right, coords.session_list_bottom) = rects["session_list_rect"]
    if "ocr_chat_rect" in rects:
        (coords.ocr_chat_left, coords.ocr_chat_top,
         coords.ocr_chat_right, coords.ocr_chat_bottom) = rects["ocr_chat_rect"]

    coords.qianniu_window_left, coords.qianniu_window_top, \
        coords.qianniu_window_right, coords.qianniu_window_bottom = win_rect
    return coords


def _merge_failed_fields_from_cv(
    predicted: CalibrateCoords,
    cv: CalibrateCoords,
    failed_field_keys: set[str],
) -> tuple[CalibrateCoords, list[str]]:
    """复检否决的字段用 CV 满屏搜结果覆盖，其余保留预测值。
    返回 (新 coords, 实际被 CV 覆盖的字段名)。"""
    new = CalibrateCoords()
    overridden: list[str] = []
    for f in predicted.__dataclass_fields__:
        pv = getattr(predicted, f)
        if f in failed_field_keys and getattr(cv, f) is not None:
            new.__dict__[f] = getattr(cv, f)
            overridden.append(f)
        else:
            new.__dict__[f] = pv
    return new, overridden


def _run_anchor_calibrate(
    shop_yaml_path,
    win_rect,
    arr,
    png_bytes: bytes,
    sw: int,
    sh: int,
    st,
    all_notes: list[str],
    log,
) -> AutoCalibrateResult | None:
    """
    anchor 预测路径（窗口+偏移）。返回 None 表示无可用 anchor → 调用方回退满屏搜。

    流程：读 anchor → 当前窗口左上+偏移预测全部组件 → AI 复检：
      - 全过：直接用预测（method=anchor）
      - 个别字段被否决：仅对那几个字段满屏重搜覆盖（method=anchor+局部cv）
    任务栏图标始终独立 UIA 重定位（窗口外，不纳入 anchor）。
    """
    if not shop_yaml_path or not win_rect:
        return None
    try:
        from pathlib import Path as _Path
        from apps.core.automation.anchor_calibrate import from_yaml_dict
        from apps.core.configs.shop_yaml_calibration import read_calib_anchor

        anchor = from_yaml_dict(read_calib_anchor(_Path(shop_yaml_path)))
    except Exception as e:
        all_notes.append(f"anchor 读取异常，回退满屏搜：{e!r}")
        return None
    if anchor is None:
        all_notes.append("无 anchor（首次校准）→ 走满屏搜，写盘时将固化 anchor")
        return None

    log("anchor 预测：当前窗口左上角 + 历史偏移 推算各组件坐标…")
    predicted = _predict_coords_from_anchor(anchor, win_rect)
    all_notes.append(
        f"anchor 预测完成：base_window={anchor.base_window} → 当前窗口={win_rect}"
    )

    if not predicted.has_critical():
        all_notes.append("anchor 预测关键字段不全 → 回退满屏搜")
        return None

    method = "anchor"
    final_coords = predicted

    # ── AI 复检（看带标注图判断预测对错）────────────────────────────────
    qc_model = _pick_vision_model(st)
    if qc_model:
        try:
            annotated = _render_annotated_for_qc(predicted, png_bytes)
            ok, issues, fixes = _vision_qc_review(
                annotated, sw, sh, win_rect, st, predicted,
            )
            if ok:
                log("anchor 复检：✓ 预测全部正确，免满屏搜")
                all_notes.append("anchor 复检通过：直接采用偏移预测坐标")
            else:
                failed_keys = set(fixes.keys())
                log(f"anchor 复检否决字段 {sorted(failed_keys)}，仅对其满屏重搜…")
                all_notes.append(f"anchor 复检发现 {len(issues)} 处疑点：")
                for i in issues:
                    all_notes.append(f"  ⚠ {i}")
                cv_notes: list[str] = []
                cv_coords = _cv_ocr_calibrate(arr, sw, sh, win_rect, cv_notes)
                all_notes.extend(cv_notes)
                final_coords, overridden = _merge_failed_fields_from_cv(
                    predicted, cv_coords, failed_keys,
                )
                final_coords.qianniu_window_left, final_coords.qianniu_window_top, \
                    final_coords.qianniu_window_right, final_coords.qianniu_window_bottom = win_rect
                if overridden:
                    method = "anchor+局部cv"
                    all_notes.append(f"已用满屏搜覆盖字段：{', '.join(overridden)}")
                    final_coords.calib_fallback_fields = list(overridden)
                else:
                    all_notes.append("满屏搜未能给出更优值，保留预测坐标")
        except Exception as e:
            all_notes.append(f"anchor 复检异常（保留预测坐标）：{e!r}")
    else:
        all_notes.append("未配置视觉模型，跳过 anchor 复检，直接用预测坐标")

    # ── 任务栏图标（独立 UIA，窗口外，不纳入 anchor）──────────────────────
    if final_coords.taskbar_icon_x is None:
        tb_pt = _locate_taskbar_icon_uia()
        if tb_pt:
            final_coords.taskbar_icon_x, final_coords.taskbar_icon_y = tb_pt
            all_notes.append(f"UIA 定位任务栏千牛图标：{tb_pt}")
        else:
            all_notes.append("⚠ 任务栏未找到千牛图标")

    confidence = "high" if final_coords.has_critical() else "medium"
    return AutoCalibrateResult(
        coords=final_coords,
        method=method,
        confidence=confidence,
        needs_manual_send=not final_coords.has_critical(),
        multi_window_count=0,
        notes=all_notes,
        screenshot_png=png_bytes,
    )


# ── 公开入口 ────────────────────────────────────────────────────────────
def run_auto_calibrate(
    settings=None,
    progress_cb: Callable[[str], None] | None = None,
    shop_yaml_path=None,
) -> AutoCalibrateResult:
    """
    自动校准流程（v1.3.96：回退 CV+OCR 主力 + Vision AI 仅补缺）：
      ① UIA      → 获取千牛主窗口精确边界
      ② CV+OCR（主力）→ 用 OCR 找"发送/今日接待"等关键词 + 蓝色按钮像素定位（最准确）
      ③ Vision AI（仅补缺）→ CV+OCR 关键字段缺失时才用，避免 AI 视觉猜测把发送键飘到右侧面板
      ④ UIA 任务栏图标（独立，AI 看不到任务栏）
      ⑤ 若仍不完整 → 标记 needs_manual，提示用户手动点选

    实测：Vision AI（claude-sonnet-4-6-thinking）在 2560×1440 千牛界面上
    经常把"发送"按钮误识别成右侧客户信息面板里的按钮（飘移 ~350 像素）；
    而 CV+OCR 找"发送"两个字 + 蓝色像素的朴素方法反而准确（误差 <10 像素）。
    """
    def _log(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    _log("正在截取全屏…")
    arr, sw, sh = _grab_fullscreen()
    png_bytes = _rgb_to_png_bytes(arr)
    all_notes: list[str] = [f"屏幕分辨率：{sw}×{sh}（物理像素）"]

    try:
        import ctypes as _ct
        if hasattr(_ct, "windll"):
            hwnd = _ct.windll.user32.GetDesktopWindow()
            dpi = _ct.windll.user32.GetDpiForWindow(hwnd)
            scale = round(dpi / 96.0 * 100)
            all_notes.append(f"系统 DPI 缩放：{scale}% （DPI={dpi}）")
    except Exception:
        pass

    from apps.core.configs.base_settings import load_base_settings
    st = settings or load_base_settings()

    # ── ① UIA 获取千牛主窗口边界 ────────────────────────────────────────────
    _log("UIA 定位千牛主窗口…")
    win_rect = _locate_qianniu_window_uia()
    if win_rect:
        wl, wt, wr, wb = win_rect
        all_notes.append(f"UIA 定位千牛主窗口：[{wl},{wt}→{wr},{wb}]")
    else:
        all_notes.append("⚠ UIA 未找到千牛主窗口，将在全屏范围内搜索")

    # ── v1.6.3 anchor 预测优先：有 anchor + 窗口可定位 → 用偏移预测，免满屏搜 ──
    anchor_result = _run_anchor_calibrate(
        shop_yaml_path, win_rect, arr, png_bytes, sw, sh, st, all_notes, _log,
    )
    if anchor_result is not None:
        return anchor_result

    # ── ② CV+OCR 主力识别（v1.3.96：回退到原方案，实测比 Vision AI 准）──
    _log("CV+OCR 识别坐标（找'发送/今日接待'文字 + 蓝色按钮像素）…")
    cv_notes: list[str] = []
    cv_coords = _cv_ocr_calibrate(arr, sw, sh, win_rect, cv_notes)
    all_notes.extend(cv_notes)

    vis_coords = CalibrateCoords()
    final_coords: CalibrateCoords
    method: str

    if cv_coords.has_critical():
        _log("CV+OCR 关键字段齐全 ✓")
        final_coords = cv_coords
        method = "cv+ocr"
        # Vision AI 仅补"客服按钮"等 CV 漏识的边角字段（不覆盖 CV 已识别字段）
        vision_model = _pick_vision_model(st)
        if vision_model and not all([
            cv_coords.service_btn_x,
            cv_coords.right_panel_left,
        ]):
            _log(f"Vision AI 补识边角字段（{vision_model}）…")
            try:
                vis_coords, vis_notes = _vision_calibrate(
                    png_bytes, settings=st, sw=sw, sh=sh
                )
                all_notes.extend(vis_notes)
                final_coords = _merge(cv_coords, vis_coords)  # CV 优先，AI 补空
                method = "cv+ocr+vision"
            except Exception as e:
                _log(f"Vision AI 补识失败（不影响主流程）：{e!r}")
    else:
        # CV+OCR 关键字段缺失 → Vision AI 兜底
        _log("⚠ CV+OCR 关键字段缺失，Vision AI 兜底…")
        vision_model = _pick_vision_model(st)
        if vision_model:
            try:
                vis_coords, vis_notes = _vision_calibrate(
                    png_bytes, settings=st, sw=sw, sh=sh
                )
                all_notes.extend(vis_notes)
                final_coords = _merge(cv_coords, vis_coords)  # CV 优先，AI 补 critical
                method = "cv+ocr+vision"
            except Exception as e:
                _log(f"Vision AI 兜底失败：{e!r}")
                final_coords = cv_coords
                method = "cv+ocr"
        else:
            _log("⚠ 未配置视觉模型 API Key，且 CV+OCR 关键字段缺失")
            final_coords = cv_coords
            method = "cv+ocr"

    # 把 UIA 窗口边界写进 coords（无论走哪条路径）
    if win_rect:
        final_coords.qianniu_window_left   = win_rect[0]
        final_coords.qianniu_window_top    = win_rect[1]
        final_coords.qianniu_window_right  = win_rect[2]
        final_coords.qianniu_window_bottom = win_rect[3]

    if not final_coords.has_critical():
        _log("⚠ 关键坐标仍缺失，将提示手动校准")

    # ── ②.5 v1.3.97：AI 复检闭环（CV 跑完 → 画标注 → AI 看图 → 给修正）──
    # 比让 AI 凭空猜坐标精度高得多——AI 看带标注的图判断对错是视觉理解强项
    qc_vision_model = _pick_vision_model(st)
    if qc_vision_model and final_coords.has_critical():
        try:
            _log(f"AI 复检：让 {qc_vision_model} 看标注图判断 CV+OCR 结果是否正确…")
            annotated = _render_annotated_for_qc(final_coords, png_bytes)
            if annotated:
                ok, issues, fixes = _vision_qc_review(
                    annotated, sw, sh, win_rect, st, final_coords,
                )
                if ok:
                    _log("AI 复检：✓ 所有标注正确")
                    all_notes.append("AI 复检通过：CV+OCR 结果无需修正")
                else:
                    _log(f"AI 复检：发现问题 {issues}，应用修正 {list(fixes.keys())}")
                    all_notes.append(f"AI 复检：发现 {len(issues)} 个问题")
                    for i in issues:
                        all_notes.append(f"  ⚠ {i}")
                    if fixes:
                        all_notes.append(
                            f"AI 修正字段：{', '.join(f'{k}={v}' for k, v in fixes.items())}"
                        )
                        final_coords = _apply_qc_fixes(final_coords, fixes)
                        method = method + "+qc"
        except Exception as e:
            _log(f"AI 复检异常（不影响主流程）：{e!r}")

    # ── ④ 任务栏图标定位（独立） ──────────────────────────────────────────
    if final_coords.taskbar_icon_x is None:
        _log("UIA 定位任务栏千牛图标…")
        tb_pt = _locate_taskbar_icon_uia()
        if tb_pt:
            final_coords.taskbar_icon_x = tb_pt[0]
            final_coords.taskbar_icon_y = tb_pt[1]
            all_notes.append(f"UIA 定位到任务栏千牛图标：({tb_pt[0]}, {tb_pt[1]})")
        else:
            all_notes.append("⚠ 任务栏中未找到千牛图标（千牛未运行？）")

    # ── ⑤ 仍不完整 → 标记需要手动 ───────────────────────────────────────
    needs_manual = not final_coords.has_critical()
    if needs_manual:
        _log("⚠ 关键坐标仍缺失，请补充手动校准。")
        all_notes.append("⚠ 自动识别不完整：请用手动点选补充发送按钮 / 输入框位置。")

    multi_count = 0
    for n in all_notes:
        m2 = re.search(r"检测到\s*(\d+)\s*个窗口", n)
        if m2:
            multi_count = int(m2.group(1))

    confidence = (
        "high" if final_coords.has_critical()
        else "medium" if final_coords.ocr_chat_left is not None
        else "low"
    )

    return AutoCalibrateResult(
        coords=final_coords,
        method=method,
        confidence=confidence,
        needs_manual_send=needs_manual,
        multi_window_count=multi_count,
        notes=all_notes,
        screenshot_png=png_bytes,
    )


# ── 将结果写入 YAML ──────────────────────────────────────────────────────
def apply_auto_calibrate_result(yaml_path, result: AutoCalibrateResult) -> list[str]:
    """把 result.coords 里不为 None 的字段写入 YAML，返回写入字段列表。"""
    from pathlib import Path
    from apps.core.configs.shop_yaml_calibration import apply_click_calibration

    p = Path(yaml_path)
    written: list[str] = []
    c = result.coords

    _pt_map = [
        ("input_box_point",   c.input_box_x,    c.input_box_y),
        ("send_button_point", c.send_button_x,   c.send_button_y),
        ("chat_scroll_point", c.chat_scroll_x,   c.chat_scroll_y),
        ("taskbar_icon_point", c.taskbar_icon_x, c.taskbar_icon_y),
        ("service_btn_point", c.service_btn_x,   c.service_btn_y),
    ]
    for tid, x, y in _pt_map:
        if x is not None and y is not None:
            apply_click_calibration(p, tid, x, y)
            written.append(tid)

    _rect_map = [
        ("ocr_chat_tl", c.ocr_chat_left,        c.ocr_chat_top),
        ("ocr_chat_br", c.ocr_chat_right,        c.ocr_chat_bottom),
        ("session_list_tl", c.session_list_left, c.session_list_top),
        ("session_list_br", c.session_list_right, c.session_list_bottom),
    ]
    for tid, x, y in _rect_map:
        if x is not None and y is not None:
            apply_click_calibration(p, tid, x, y)
            written.append(tid)

    # 右侧面板左边界（单值，不是点，用 x 存 left、y 存 0 做标记）
    if c.right_panel_left is not None:
        apply_click_calibration(p, "right_panel_left_point", c.right_panel_left, 0)
        written.append("right_panel_left_point")

    # ── v1.6.3：顺手固化 anchor（窗口 rect + 各组件偏移）──────────────────
    # 下次校准即可走「窗口+偏移」预测，不再满屏瞎搜。任务栏图标不纳入。
    try:
        anchor = _build_anchor_from_coords(c)
        if anchor is not None:
            from apps.core.automation.anchor_calibrate import to_yaml_dict
            from apps.core.configs.shop_yaml_calibration import write_calib_anchor
            write_calib_anchor(p, to_yaml_dict(anchor))
            written.append("calib_anchor")
    except Exception:
        # 固化 anchor 失败不影响坐标写盘主流程
        pass

    return written


def _build_anchor_from_coords(c: "CalibrateCoords"):
    """从 CalibrateCoords + 其窗口边界构造 anchor；窗口边界缺失返回 None。"""
    from apps.core.automation.anchor_calibrate import build_anchor

    if None in (c.qianniu_window_left, c.qianniu_window_top,
                c.qianniu_window_right, c.qianniu_window_bottom):
        return None
    base_window = (
        int(c.qianniu_window_left), int(c.qianniu_window_top),
        int(c.qianniu_window_right), int(c.qianniu_window_bottom),
    )
    points = {
        "input_box_point": _xy(c.input_box_x, c.input_box_y),
        "send_button_point": _xy(c.send_button_x, c.send_button_y),
        "chat_scroll_point": _xy(c.chat_scroll_x, c.chat_scroll_y),
        "service_btn_point": _xy(c.service_btn_x, c.service_btn_y),
    }
    rects = {
        "session_list_rect": _ltrb(
            c.session_list_left, c.session_list_top,
            c.session_list_right, c.session_list_bottom,
        ),
        "ocr_chat_rect": _ltrb(
            c.ocr_chat_left, c.ocr_chat_top,
            c.ocr_chat_right, c.ocr_chat_bottom,
        ),
    }
    points = {k: v for k, v in points.items() if v is not None}
    rects = {k: v for k, v in rects.items() if v is not None}
    if not points and not rects:
        return None
    return build_anchor(base_window, points, rects)


def _xy(x, y):
    return None if x is None or y is None else (int(x), int(y))


def _ltrb(l, t, r, b):  # noqa: E741
    return None if None in (l, t, r, b) else (int(l), int(t), int(r), int(b))
