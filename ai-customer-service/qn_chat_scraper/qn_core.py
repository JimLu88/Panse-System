#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
核心模块：千牛聊天抓取逻辑（可被 CLI / GUI 复用）

设计目标：
- 所有 UIA 操作与抽取逻辑都在这里
- CLI/GUI 只负责：读取配置、传入参数、展示日志/进度、写文件路径选择
"""

from __future__ import annotations

import csv
import hashlib
import re
import sys
import time
import ctypes
from ctypes import wintypes
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Set, Tuple

import uiautomation as auto

# OCR deps（可选）
try:
    from mss import mss
    import numpy as np
    from PIL import Image  # noqa: F401
    from rapidocr_onnxruntime import RapidOCR

    _OCR_AVAILABLE = True
except Exception:
    _OCR_AVAILABLE = False

# 统一的行结构：客户昵称、发送者、时间、内容、hash
ChatRow = Tuple[str, str, str, str, str]


# =========================
# 更可靠的按键注入（SendInput）
# - 解决 Qt 自绘输入框把 "^v" 当文本的问题
# =========================

VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_SHIFT = 0x10
VK_RETURN = 0x0D
VK_DOWN = 0x28
VK_DELETE = 0x2E


def _vk_from_char(ch: str) -> int:
    # 仅用于 a/c/v 这类
    o = ord(ch.upper())
    if 0x41 <= o <= 0x5A:
        return o
    raise ValueError(f"unsupported char for vk: {ch!r}")


# Win32 structs for SendInput
# https://learn.microsoft.com/windows/win32/api/winuser/nf-winuser-sendinput
ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_SendInput = ctypes.windll.user32.SendInput
_SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_SendInput.restype = wintypes.UINT


def _send_input_vk(vk: int, key_down: bool) -> None:
    # dwFlags: 0 for keydown, KEYEVENTF_KEYUP for keyup
    KEYEVENTF_KEYUP = 0x0002
    flags = 0 if key_down else KEYEVENTF_KEYUP
    inp = _INPUT(type=1, ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=ULONG_PTR(0)))
    sent = _SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
    if sent != 1:
        err = ctypes.windll.kernel32.GetLastError()
        raise RuntimeError(f"SendInput failed (sent={sent}, vk={vk}, down={key_down}, err={err})")


def _keybd_event_vk(vk: int, key_down: bool) -> None:
    # legacy fallback: keybd_event
    KEYEVENTF_KEYUP = 0x0002
    flags = 0 if key_down else KEYEVENTF_KEYUP
    ctypes.windll.user32.keybd_event(vk, 0, flags, 0)


def press_vk(vk: int) -> None:
    try:
        _send_input_vk(vk, True)
        time.sleep(0.01)
        _send_input_vk(vk, False)
    except Exception:
        # fallback
        _keybd_event_vk(vk, True)
        time.sleep(0.01)
        _keybd_event_vk(vk, False)


def press_ctrl_combo(ch: str) -> None:
    vk = _vk_from_char(ch)
    try:
        _send_input_vk(VK_CONTROL, True)
        time.sleep(0.01)
        press_vk(vk)
        time.sleep(0.01)
        _send_input_vk(VK_CONTROL, False)
    except Exception:
        _keybd_event_vk(VK_CONTROL, True)
        time.sleep(0.01)
        press_vk(vk)
        time.sleep(0.01)
        _keybd_event_vk(VK_CONTROL, False)



@dataclass(frozen=True)
class QNSelectors:
    main_window_name_contains: str = "千牛"

    # 兜底：如果 UIA 无法定位搜索框控件，可用屏幕坐标点击聚焦（x,y）
    search_point_x: Optional[int] = None
    search_point_y: Optional[int] = None
    # 搜索后“第一条结果/第一个联系人”的点击坐标（千牛很多布局需要点一下才会打开会话）
    first_result_point_x: Optional[int] = None
    first_result_point_y: Optional[int] = None

    results_list_automation_id: Optional[str] = None
    results_list_class_name: Optional[str] = None
    results_list_name_contains: Optional[str] = None

    # 兜底：如果 UIA 无法定位聊天列表容器，可用坐标作为“滚动落点”
    chat_scroll_point_x: Optional[int] = None
    chat_scroll_point_y: Optional[int] = None

    # OCR：聊天内容区域矩形（屏幕坐标）
    chat_ocr_left: Optional[int] = None
    chat_ocr_top: Optional[int] = None
    chat_ocr_right: Optional[int] = None
    chat_ocr_bottom: Optional[int] = None

    message_item_class_name: Optional[str] = None
    close_button_name: str = "关闭"


@dataclass(frozen=True)
class ScrapeOptions:
    sleep_seconds: float = 2.0
    max_scroll_pages: int = 60
    stop_no_new_rounds: int = 3
    global_search_timeout: float = 2.0
    # 将 sleep 拆成输入后等待与滚动后等待（方便你把滚动调快）
    search_wait_seconds: Optional[float] = None
    scroll_wait_seconds: Optional[float] = None
    # 输入后额外停顿（解决千牛“跳结果需要时间”，避免点错/回车太快）
    after_paste_pause_seconds: float = 0.8
    # OCR 模式：对聊天区域截图做 OCR（适用于 Qt 不暴露消息文本）
    use_ocr: bool = False
    # OCR：过滤常见无效行
    ocr_filter_noise: bool = True
    # 进入会话后先滚到最底端，避免漏掉最新消息
    ensure_bottom_before_scrape: bool = True
    bottom_scroll_max_rounds: int = 25
    bottom_scroll_stable_rounds: int = 2
    bottom_scroll_wheel_notches: int = 18


def now_str() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _is_chat_ocr_rect_valid(s: QNSelectors) -> bool:
    vals = [s.chat_ocr_left, s.chat_ocr_top, s.chat_ocr_right, s.chat_ocr_bottom]
    if any(v is None for v in vals):
        return False
    return s.chat_ocr_right > s.chat_ocr_left and s.chat_ocr_bottom > s.chat_ocr_top


def _normalize_ws(t: str) -> str:
    t = (t or "").replace("\u3000", " ").replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _looks_like_noise_line(t: str) -> bool:
    t = _normalize_ws(t)
    if not t:
        return True
    if len(t) <= 1:
        return True
    if re.fullmatch(r"[\-\|\•\·\.\,，。:：;；_—~\(\)\[\]【】<>《》]+", t):
        return True
    ui_words = {
        "已读",
        "未读",
        "置顶",
        "更多",
        "搜索",
        "联系人",
        "或群",
        "最近聊天记录",
        "欢迎",
        "今日接待",
        "接待中",
        "全部买家",
        "其他消息",
        "联系人",
    }
    if t in ui_words:
        return True
    # 常见纯 URL / file 协议噪声
    if t.startswith("http://") or t.startswith("https://") or t.startswith("file://"):
        return True
    # 很多商品卡片会出现“¥660.0”之类，单独一行价格可先过滤（你后续如果要保留可关掉过滤）
    if re.fullmatch(r"[¥￥]?\s*\d+(\.\d+)?", t):
        return True
    return False


class ChatOCR:
    def __init__(self):
        if not _OCR_AVAILABLE:
            raise RuntimeError("OCR 依赖未安装：请先 pip install -r requirements.txt")
        try:
            self._ocr = RapidOCR()
        except FileNotFoundError as e:
            # PyInstaller onefile 常见：模型文件没被打包进去
            missing = getattr(e, "filename", None)
            raise FileNotFoundError(
                e.errno,
                (
                    "OCR 初始化失败：找不到文件（打包 EXE 时未收集数据文件，或运行时缺少依赖 DLL）。\n"
                    f"missing={missing!r}\n"
                    "请用更新后的 build_exe.ps1 重新打包（包含 --collect-all rapidocr_onnxruntime/onnxruntime/mss）。\n"
                    "如果仍失败，把本错误完整文本发我。"
                ),
                missing or e.filename,
            ) from e
        except Exception as e:
            raise RuntimeError(
                "OCR 初始化失败（非 FileNotFoundError）。\n" + traceback.format_exc()
            ) from e
        self._sct = mss()

    def grab_rgb(self, left: int, top: int, right: int, bottom: int) -> "np.ndarray":
        mon = {"left": int(left), "top": int(top), "width": int(right - left), "height": int(bottom - top)}
        img = self._sct.grab(mon)
        arr = np.array(img)[:, :, :3][:, :, ::-1]  # BGRA->RGB
        return arr

    def ocr_lines(self, rgb_img: "np.ndarray") -> List[str]:
        try:
            res, _ = self._ocr(rgb_img)
        except FileNotFoundError as e:
            missing = getattr(e, "filename", None)
            raise FileNotFoundError(
                e.errno,
                f"OCR 推理阶段找不到文件：missing={missing!r}\n{traceback.format_exc()}",
                missing or e.filename,
            ) from e
        except Exception as e:
            raise RuntimeError("OCR 推理失败：\n" + traceback.format_exc()) from e
        if not res:
            return []
        try:
            sortable = []
            for (box, text, score) in res:
                y = float(box[0][1]) if box and box[0] else 0.0
                sortable.append((y, str(text)))
            sortable.sort(key=lambda x: x[0])
            return [t for _, t in sortable if t]
        except Exception:
            return [str(text) for _, text, _ in res if text]


def ensure_chat_csv_header(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["客户昵称", "发送者", "时间", "内容", "hash"])


def ensure_chat_raw_csv_header(path: Path) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["客户昵称", "内容_raw", "hash_raw"])


def append_rows_csv(path: Path, rows: Sequence[ChatRow]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(list(r))


def append_rows_raw_csv(path: Path, rows: Sequence[Tuple[str, str, str]]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(list(r))


def load_existing_hashes(path: Path) -> Set[str]:
    if not path.exists() or path.stat().st_size == 0:
        return set()

    hashes: Set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "hash" not in reader.fieldnames:
            return set()
        for row in reader:
            h = (row.get("hash") or "").strip()
            if h:
                hashes.add(h)
    return hashes


def dump_control_tree(control: auto.Control, out_path: Path, logger: Callable[[str], None]) -> None:
    """
    将控件树导出到文件，便于定位 AutomationId/ClassName/Name。
    """
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # 不同版本的 uiautomation 对“打印控件树”的 API 差异很大，
        # 为避免再踩坑，这里改为我们自己遍历 WalkControl 输出关键信息。
        def safe(getter, default=""):
            try:
                v = getter()
                return "" if v is None else str(v)
            except Exception:
                return default

        with out_path.open("w", encoding="utf-8") as f:
            f.write("=== Control Tree Dump (WalkControl) ===\n")
            f.write(f"Root: Name='{safe(lambda: control.Name)}' ClassName='{safe(lambda: control.ClassName)}' "
                    f"AutomationId='{safe(lambda: control.AutomationId)}' ControlType='{safe(lambda: control.ControlTypeName)}'\n\n")

            for c, depth in auto.WalkControl(control, maxDepth=12):
                name = safe(lambda: c.Name)
                cls = safe(lambda: c.ClassName)
                aid = safe(lambda: c.AutomationId)
                ctype = safe(lambda: c.ControlTypeName)
                rect = safe(lambda: c.BoundingRectangle)
                indent = "  " * depth
                f.write(f"{indent}- {ctype}  Name='{name}'  ClassName='{cls}'  AutomationId='{aid}'  Rect={rect}\n")

        logger(f"[info] 已导出控件树：{out_path}")
    except Exception as e:
        logger(f"[warn] 导出控件树失败：{e!r}")


class QNChatScraper:
    def __init__(
        self,
        selectors: QNSelectors,
        options: ScrapeOptions,
        logger: Callable[[str], None],
        stop_flag: Callable[[], bool] | None = None,
    ):
        self.s = selectors
        self.o = options
        self.log = logger
        self.stop_flag = stop_flag or (lambda: False)

        auto.uiautomation.SetGlobalSearchTimeout(self.o.global_search_timeout)

    # -------- 主窗口定位 --------
    def get_main_window(self) -> auto.WindowControl:
        w = auto.WindowControl(searchDepth=2, NameContains=self.s.main_window_name_contains)
        if not w.Exists(1):
            w = auto.WindowControl(searchDepth=6, NameContains=self.s.main_window_name_contains)
        if not w.Exists(1):
            raise RuntimeError("未找到千牛主窗口：请确认千牛已打开且未最小化。")
        return w

    # -------- 搜索框 --------
    def focus_search_by_point(self) -> None:
        if self.s.search_point_x is None or self.s.search_point_y is None:
            raise RuntimeError("未配置搜索框坐标。")
        # 单击有时不够稳：先移动再点击，避免坐标落点被遮挡
        try:
            auto.MoveTo(self.s.search_point_x, self.s.search_point_y)
        except Exception:
            pass
        auto.Click(self.s.search_point_x, self.s.search_point_y)
        time.sleep(0.2)

    def set_search_text_by_point(self, text: str) -> None:
        """
        坐标模式输入：
        - 点击坐标聚焦（适用于 Qt 自绘输入框）
        - Ctrl+A 清空
        - 剪贴板粘贴
        """
        # 为了解决“实际输入内容不对”（焦点错/输入法/粘贴失败），这里做读回校验并自动重试
        for attempt in range(3):
            self.focus_search_by_point()
            press_ctrl_combo("a")
            time.sleep(0.05)
            press_vk(VK_DELETE)
            time.sleep(0.05)
            auto.SetClipboardText(text)
            time.sleep(0.05)
            press_ctrl_combo("v")
            time.sleep(max(0.0, float(self.o.after_paste_pause_seconds)))
            time.sleep(0.10)

            # 读回校验：Ctrl+A Ctrl+C -> 读剪贴板
            press_ctrl_combo("a")
            time.sleep(0.05)
            press_ctrl_combo("c")
            time.sleep(0.05)
            read_back = (auto.GetClipboardText() or "").strip()

            if read_back == text.strip():
                return

            # 失败就再试一次（可能坐标偏了或焦点被抢）
            if attempt < 2:
                time.sleep(0.2)

        raise RuntimeError(
            "搜索框输入校验失败：粘贴后读回的内容与目标昵称不一致。\n"
            "这通常是坐标偏移/DPI 缩放/焦点被抢导致。\n"
            "请重新抓取“搜索框坐标”，并确保点击后光标确实在搜索框内。"
        )

    def click_first_search_result_if_configured(self) -> None:
        """
        千牛常见行为：输入并回车后，左侧会出现匹配结果，需要点击第一条才能进入会话。
        如果用户配置了 first_result_point_x/y，就执行点击；否则用键盘兜底（↓ 回车）。
        """
        if self.s.first_result_point_x is not None and self.s.first_result_point_y is not None:
            try:
                auto.MoveTo(self.s.first_result_point_x, self.s.first_result_point_y)
            except Exception:
                pass
            auto.Click(self.s.first_result_point_x, self.s.first_result_point_y)
            return

        # 键盘兜底：让焦点在搜索结果列表时，↓ 再回车通常能打开第一条
        press_vk(VK_DOWN)
        time.sleep(0.1)
        press_vk(VK_RETURN)

    def open_chat_by_search(self, nickname: str) -> auto.Control:
        main = self.get_main_window()

        # 按用户要求：只使用坐标模式
        self.set_search_text_by_point(nickname)
        time.sleep(self.o.search_wait_seconds if self.o.search_wait_seconds is not None else self.o.sleep_seconds)

        press_vk(VK_RETURN)
        time.sleep(self.o.search_wait_seconds if self.o.search_wait_seconds is not None else self.o.sleep_seconds)

        # 补上“点第一条搜索结果/联系人”步骤
        self.click_first_search_result_if_configured()
        time.sleep(self.o.search_wait_seconds if self.o.search_wait_seconds is not None else self.o.sleep_seconds)

        chat_win = auto.WindowControl(searchDepth=3, NameContains=nickname)
        if chat_win.Exists(0.5):
            try:
                chat_win.SetFocus()
            except Exception:
                pass
            return chat_win
        return main

    # -------- 聊天列表 --------
    def find_chat_list_from_point(self, container: auto.Control) -> auto.Control:
        """
        坐标模式下的聊天列表定位：
        - 从聊天区滚动坐标处取 ControlFromPoint
        - 然后向上找一个“像容器”的 Pane/List/Group
        说明：Qt 自绘可能仍然只有大容器，但至少能用于枚举 children（如果该版本暴露消息项）。
        """
        if self.s.chat_scroll_point_x is None or self.s.chat_scroll_point_y is None:
            raise RuntimeError("未配置聊天区滚动坐标。")

        # 防止坐标点到了别的窗口：校验该点控件的顶层窗口名包含 main_window_name_contains
        try:
            top = container  # container 通常就是千牛主窗口（WindowControl）
        except Exception:
            top = None

        c = auto.ControlFromPoint(self.s.chat_scroll_point_x, self.s.chat_scroll_point_y)
        if not c:
            raise RuntimeError("无法从聊天区坐标获取控件（ControlFromPoint 返回空）。")

        try:
            top2 = c.GetTopLevelControl()
            top2_name = (getattr(top2, "Name", "") or "")
            if self.s.main_window_name_contains and self.s.main_window_name_contains not in top2_name:
                raise RuntimeError(
                    f"聊天区坐标似乎不在千牛窗口内（顶层窗口='{top2_name}'）。请重新抓取“聊天区坐标”。"
                )
        except Exception as e:
            # 如果无法取顶层，也提示重新抓点（比误抓桌面强）
            raise RuntimeError(f"聊天区坐标校验失败：{e}")

        # 向上找容器
        target = c
        for _ in range(12):
            try:
                ct = target.ControlType
            except Exception:
                ct = None
            if ct in {auto.ControlType.PaneControl, auto.ControlType.ListControl, auto.ControlType.GroupControl}:
                return target
            try:
                target = target.GetParentControl()
            except Exception:
                break
        return c

    # -------- 抽取与去重 --------
    _re_time1 = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
    _re_time2 = re.compile(r"^\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}\s+\d{1,2}:\d{2}(:\d{2})?$")

    def looks_like_time(self, t: str) -> bool:
        t = t.strip().replace("：", ":")
        return bool(self._re_time1.match(t) or self._re_time2.match(t))

    def hash_message(self, ts: str, text: str) -> str:
        raw = (ts.strip() + "|" + text.strip()).encode("utf-8", errors="ignore")
        return hashlib.sha256(raw).hexdigest()

    def extract_message_fields(self, msg_item: auto.Control) -> Tuple[str, str, str]:
        texts: List[str] = []
        for c, _depth in auto.WalkControl(msg_item, maxDepth=8):
            try:
                name = (c.Name or "").strip()
                if name:
                    texts.append(name)
            except Exception:
                pass
            try:
                vp = c.GetValuePattern()
                if vp:
                    v = (vp.Value or "").strip()
                    if v and v not in texts:
                        texts.append(v)
            except Exception:
                pass

        if not texts:
            try:
                if msg_item.ControlType == auto.ControlType.ImageControl:
                    return "", "", "[图片]"
            except Exception:
                pass
            return "", "", "[图片]"

        ts = ""
        for t in texts:
            if self.looks_like_time(t):
                ts = t
                break

        sender = ""
        for t in texts:
            if t == ts:
                continue
            if 1 <= len(t) <= 20 and not self.looks_like_time(t):
                sender = t
                break

        remain = [t for t in texts if t not in {ts, sender}]
        content = max(remain, key=len) if remain else texts[-1]
        return sender, ts, content

    def iter_visible_message_items(self, chat_list: auto.Control) -> List[auto.Control]:
        children = chat_list.GetChildren()
        if children:
            return children
        out: List[auto.Control] = []
        for c, _depth in auto.WalkControl(chat_list, maxDepth=3):
            out.append(c)
        return out

    # -------- 翻页 --------
    def scroll_up_once(self, chat_list: auto.Control) -> None:
        # 坐标模式滚动：把鼠标移动到聊天区坐标，再滚轮向上
        if self.s.chat_scroll_point_x is not None and self.s.chat_scroll_point_y is not None:
            try:
                auto.MoveTo(self.s.chat_scroll_point_x, self.s.chat_scroll_point_y)
                auto.Click(self.s.chat_scroll_point_x, self.s.chat_scroll_point_y)
            except Exception:
                pass
        else:
            try:
                chat_list.Click()
            except Exception:
                pass

        time.sleep(self.o.scroll_wait_seconds if self.o.scroll_wait_seconds is not None else self.o.sleep_seconds)
        try:
            auto.WheelUp(12)
        except Exception:
            # 兜底：PageUp（使用 uiautomation 的语法，有些环境可用）
            auto.SendKeys("{PGUP}")
        time.sleep(self.o.scroll_wait_seconds if self.o.scroll_wait_seconds is not None else self.o.sleep_seconds)

    def _scroll_down_once(self) -> None:
        time.sleep(self.o.scroll_wait_seconds if self.o.scroll_wait_seconds is not None else self.o.sleep_seconds)
        auto.WheelDown(int(self.o.bottom_scroll_wheel_notches))
        time.sleep(self.o.scroll_wait_seconds if self.o.scroll_wait_seconds is not None else self.o.sleep_seconds)

    def ensure_scrolled_to_bottom(self, ocr: Optional["ChatOCR"]) -> None:
        """
        尽量滚到最底端：
        - OCR 模式：对聊天区域做 OCR，检测“稳定不变”后停止
        - 非 OCR：做固定轮数 WheelDown
        """
        if self.s.chat_scroll_point_x is not None and self.s.chat_scroll_point_y is not None:
            try:
                auto.MoveTo(self.s.chat_scroll_point_x, self.s.chat_scroll_point_y)
                auto.Click(self.s.chat_scroll_point_x, self.s.chat_scroll_point_y)
            except Exception:
                pass

        if ocr is None or not _is_chat_ocr_rect_valid(self.s):
            for _ in range(int(self.o.bottom_scroll_max_rounds)):
                if self.stop_flag():
                    return
                try:
                    self._scroll_down_once()
                except Exception:
                    break
            return

        stable = 0
        last_sig: Optional[str] = None
        for _ in range(int(self.o.bottom_scroll_max_rounds)):
            if self.stop_flag():
                return
            rgb = ocr.grab_rgb(self.s.chat_ocr_left, self.s.chat_ocr_top, self.s.chat_ocr_right, self.s.chat_ocr_bottom)
            lines = ocr.ocr_lines(rgb)
            # 用末尾若干行做签名（越底部越敏感）
            tail = [_normalize_ws(x) for x in lines[-12:]]
            sig = hashlib.sha256(("\n".join(tail)).encode("utf-8", errors="ignore")).hexdigest()
            if sig == last_sig:
                stable += 1
            else:
                stable = 0
                last_sig = sig
            if stable >= int(self.o.bottom_scroll_stable_rounds):
                return
            self._scroll_down_once()

    # -------- 收尾 --------
    def close_chat_if_popup(self, container: auto.Control) -> None:
        try:
            if isinstance(container, auto.WindowControl):
                btn = container.ButtonControl(Name=self.s.close_button_name, searchDepth=6)
                if btn.Exists(0.5):
                    btn.Click()
                    time.sleep(1)
        except Exception:
            pass

    def clear_search_box(self) -> None:
        try:
            main = self.get_main_window()
            self.focus_search_by_point()
            time.sleep(0.1)
            press_ctrl_combo("a")
            time.sleep(0.05)
            press_vk(VK_DELETE)
            time.sleep(0.2)
        except Exception:
            pass

    # -------- 单客户抓取 --------
    def scrape_one_customer(self, nickname: str, existing_hashes: Set[str]) -> List[ChatRow]:
        container = self.open_chat_by_search(nickname)
        chat_list = self.find_chat_list_from_point(container)

        seen_local: Set[str] = set()
        rows: List[ChatRow] = []

        ocr = None
        if self.o.use_ocr:
            if not _is_chat_ocr_rect_valid(self.s):
                raise RuntimeError("已开启 OCR，但未配置聊天 OCR 区域矩形（左上/右下坐标）。")
            ocr = ChatOCR()

        # 关键：先滚到最底端，避免漏掉最新消息
        if self.o.ensure_bottom_before_scrape:
            try:
                self.ensure_scrolled_to_bottom(ocr)
            except Exception:
                # 不阻断主流程
                pass

        def read_visible_uia() -> int:
            added = 0
            for item in self.iter_visible_message_items(chat_list):
                if self.s.message_item_class_name:
                    try:
                        if item.ClassName != self.s.message_item_class_name:
                            continue
                    except Exception:
                        pass

                sender, ts, content = self.extract_message_fields(item)
                h = self.hash_message(ts, content)
                if h in existing_hashes or h in seen_local:
                    continue
                seen_local.add(h)
                rows.append((nickname, sender, ts, content, h))
                added += 1
            return added

        def read_visible_ocr() -> int:
            assert ocr is not None
            rgb = ocr.grab_rgb(self.s.chat_ocr_left, self.s.chat_ocr_top, self.s.chat_ocr_right, self.s.chat_ocr_bottom)
            lines = ocr.ocr_lines(rgb)
            added = 0
            for t in lines:
                t2 = _normalize_ws(t)
                if self.o.ocr_filter_noise and _looks_like_noise_line(t2):
                    continue
                h = self.hash_message("", t2)
                if h in existing_hashes or h in seen_local:
                    continue
                seen_local.add(h)
                rows.append((nickname, "", "", t2, h))
                added += 1
            return added

        # 初始读取
        (read_visible_ocr() if ocr is not None else read_visible_uia())

        no_new = 0
        for _i in range(self.o.max_scroll_pages):
            if self.stop_flag():
                break

            self.scroll_up_once(chat_list)
            added = read_visible_ocr() if ocr is not None else read_visible_uia()
            if added == 0:
                no_new += 1
            else:
                no_new = 0
            if no_new >= self.o.stop_no_new_rounds:
                break

        self.close_chat_if_popup(container)
        self.clear_search_box()
        return rows


def run_scrape(
    names: Sequence[str],
    out_csv: Path,
    selectors: QNSelectors,
    options: ScrapeOptions,
    logs_dir: Path,
    logger: Callable[[str], None],
    stop_flag: Callable[[], bool] | None = None,
) -> None:
    """
    供 CLI/GUI 调用的一站式入口。
    """
    logger(f"[info] selectors={asdict(selectors)}")
    logger(f"[info] options={asdict(options)}")

    ensure_chat_csv_header(out_csv)
    raw_csv = out_csv.with_name(out_csv.stem + "_raw.csv")
    if options.use_ocr:
        ensure_chat_raw_csv_header(raw_csv)
    existing_hashes = load_existing_hashes(out_csv)
    logger(f"[info] 已存在 hash 数：{len(existing_hashes)}")

    scraper = QNChatScraper(selectors, options, logger=logger, stop_flag=stop_flag)

    for idx, nickname in enumerate(names, start=1):
        if stop_flag and stop_flag():
            logger("[info] 已请求停止，结束任务。")
            break

        logger(f"\n[{idx}/{len(names)}] 开始抓取：{nickname}")
        try:
            rows = scraper.scrape_one_customer(nickname, existing_hashes=existing_hashes)
            logger(f"[info] 新增 {len(rows)} 条")
            append_rows_csv(out_csv, rows)
            for r in rows:
                existing_hashes.add(r[-1])
            # OCR 模式：同时追加 raw（未清洗）文本，便于后续调整过滤规则
            if options.use_ocr and rows:
                raw_rows = []
                for r in rows:
                    content = r[3]
                    h = r[4]
                    raw_rows.append((r[0], content, h))
                append_rows_raw_csv(raw_csv, raw_rows)
        except Exception as e:
            # 打印更完整的异常信息（尤其对 EXE 环境下的 FileNotFound 很关键）
            logger(f"[error] 抓取失败：{e!r}")
            try:
                logger(traceback.format_exc())
            except Exception:
                pass
            try:
                main_win = scraper.get_main_window()
                tree_path = logs_dir / f"control-tree-{now_str()}-{idx}.txt"
                dump_control_tree(main_win, tree_path, logger=logger)
            except Exception as e2:
                logger(f"[warn] 无法导出控件树：{e2!r}")
            scraper.clear_search_box()

        time.sleep(1)

    logger(f"\n[done] 输出文件：{out_csv}")
    if options.use_ocr:
        logger(f"[done] 原始OCR：{raw_csv}")

