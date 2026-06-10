#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
千牛聊天记录抓取 GUI（Tkinter）

特点：
- 选择昵称文件（names.txt / names.csv）
- 选择输出 CSV 路径
- 可设置 sleep 秒数 / 最大翻页次数 / 无新增停止轮数
- 运行时实时输出日志
- 失败自动导出控件树到 logs/，便于精准定位 selector

打包：
- 推荐 PyInstaller 打包为单文件 EXE（见 README）
"""

from __future__ import annotations

import csv
import json
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, TOP, VERTICAL, W, X, Y, filedialog, messagebox, ttk
import tkinter as tk

from qn_core import QNSelectors, ScrapeOptions, run_scrape


APP_TITLE = "千牛聊天记录导出工具"
CONFIG_NAME = "config.json"
CAPTURE_COUNTDOWN_SECONDS = 5
CAPTURE_COUNTDOWN_SECONDS_LONG = 10


def read_names_file(path: Path, csv_column: str = "name") -> list[str]:
    """
    读取昵称列表：
    - txt：每行一个
    - csv：优先列名 csv_column，否则第一列
    - 如果传入的是“文件夹”，会自动尝试在该文件夹下寻找 names.txt / names.csv
    """
    if not path.exists():
        raise FileNotFoundError(str(path))

    # 允许用户误选“文件夹”：自动在里面找 names.txt / names.csv
    if path.is_dir():
        cand_txt = path / "names.txt"
        cand_csv = path / "names.csv"
        if cand_txt.exists():
            path = cand_txt
        elif cand_csv.exists():
            path = cand_csv
        else:
            raise FileNotFoundError(f"你选择的是文件夹：{path}\n但里面没有找到 names.txt 或 names.csv")

    def clean_one(s: str) -> str:
        # 清洗不可见控制字符（避免出现 ^a^c 这种污染）
        s = (s or "").replace("\ufeff", "").strip()
        s = "".join(ch for ch in s if ch.isprintable())
        # 再次去掉首尾空白
        return s.strip()

    if path.suffix.lower() == ".txt":
        out = []
        for x in path.read_text(encoding="utf-8-sig").splitlines():
            x2 = clean_one(x)
            if x2:
                out.append(x2)
        return out

    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            return []
        header = rows[0]
        if csv_column in header:
            idx = header.index(csv_column)
            out = []
            for r in rows[1:]:
                if idx < len(r):
                    x2 = clean_one(r[idx])
                    if x2:
                        out.append(x2)
            return out

        out = []
        for r in rows:
            if not r:
                continue
            x2 = clean_one(r[0])
            if x2 and x2.lower() != csv_column.lower():
                out.append(x2)
        return out

    raise ValueError("仅支持 .txt 或 .csv")


def get_app_dir() -> Path:
    """
    返回“应用运行目录”：
    - 源码运行：当前文件所在目录
    - PyInstaller 打包后：exe 所在目录
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "executable"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


class GuiApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("980x650")

        self.base_dir = get_app_dir()
        self.logs_dir = self.base_dir / "logs"

        self.stop_event = threading.Event()
        self.worker_thread: threading.Thread | None = None

        # ---- 状态变量 ----
        self.names_path = tk.StringVar(value=str(self.base_dir / "names.txt"))
        self.out_csv_path = tk.StringVar(value=str(self.base_dir / "chat_history.csv"))

        self.sleep_seconds = tk.DoubleVar(value=2.0)
        self.max_scroll_pages = tk.IntVar(value=60)
        self.stop_no_new_rounds = tk.IntVar(value=3)
        self.global_search_timeout = tk.DoubleVar(value=2.0)

        # 高级（仅坐标兜底 + 少量可选字段）
        self.sel_main_title = tk.StringVar(value="千牛")
        self.sel_search_x = tk.StringVar(value="")
        self.sel_search_y = tk.StringVar(value="")
        self.sel_first_result_x = tk.StringVar(value="")
        self.sel_first_result_y = tk.StringVar(value="")
        self.sel_chat_x = tk.StringVar(value="")
        self.sel_chat_y = tk.StringVar(value="")
        self.sel_ocr_left = tk.StringVar(value="")
        self.sel_ocr_top = tk.StringVar(value="")
        self.sel_ocr_right = tk.StringVar(value="")
        self.sel_ocr_bottom = tk.StringVar(value="")
        self.use_ocr = tk.BooleanVar(value=True)
        self.ensure_bottom = tk.BooleanVar(value=True)
        self.sel_msg_item_class = tk.StringVar(value="")
        self.sel_close_btn_name = tk.StringVar(value="关闭")

        self._build_ui()
        self._load_config_safely()
        self._refresh_buttons()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(side=TOP, fill=X)

        # 输入文件
        f1 = ttk.Frame(top)
        f1.pack(fill=X, pady=(0, 6))
        ttk.Label(f1, text="昵称文件（.txt/.csv）").grid(row=0, column=0, sticky=W)
        ttk.Entry(f1, textvariable=self.names_path).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(f1, text="选择…", command=self._choose_names).grid(row=0, column=2, sticky=W)
        f1.grid_columnconfigure(1, weight=1)

        # 输出文件
        f2 = ttk.Frame(top)
        f2.pack(fill=X, pady=(0, 6))
        ttk.Label(f2, text="输出 CSV").grid(row=0, column=0, sticky=W)
        ttk.Entry(f2, textvariable=self.out_csv_path).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(f2, text="选择…", command=self._choose_out_csv).grid(row=0, column=2, sticky=W)
        f2.grid_columnconfigure(1, weight=1)

        # 参数区
        opt = ttk.LabelFrame(top, text="运行参数", padding=10)
        opt.pack(fill=X, pady=(0, 8))
        row = ttk.Frame(opt)
        row.pack(fill=X)

        def add_labeled(parent: ttk.Frame, label: str, var, width=10):
            box = ttk.Frame(parent)
            box.pack(side=LEFT, padx=(0, 14))
            ttk.Label(box, text=label).pack(anchor=W)
            ttk.Entry(box, textvariable=var, width=width).pack(anchor=W)

        add_labeled(row, "sleep 秒（建议 2）", self.sleep_seconds, width=12)
        add_labeled(row, "最大翻页次数", self.max_scroll_pages, width=12)
        add_labeled(row, "无新增停止轮数", self.stop_no_new_rounds, width=12)
        add_labeled(row, "UIA 搜索超时", self.global_search_timeout, width=12)

        # 高级：仅保留坐标兜底
        adv = ttk.LabelFrame(top, text="高级（仅坐标兜底：Qt 不暴露控件时使用）", padding=10)
        adv.pack(fill=X)

        grid = ttk.Frame(adv)
        grid.pack(fill=X)

        def g(label: str, var: tk.StringVar, r: int, c: int, w: int = 28):
            ttk.Label(grid, text=label).grid(row=r, column=c * 2, sticky=W, padx=(0, 8), pady=2)
            ttk.Entry(grid, textvariable=var, width=w).grid(row=r, column=c * 2 + 1, sticky=W, pady=2)

        g("主窗口标题包含", self.sel_main_title, 0, 0, 28)
        g("关闭按钮 Name", self.sel_close_btn_name, 0, 1, 28)

        g("搜索框坐标 X(兜底)", self.sel_search_x, 2, 0, 28)
        g("搜索框坐标 Y(兜底)", self.sel_search_y, 2, 1, 28)

        g("第一条结果坐标 X(兜底)", self.sel_first_result_x, 3, 0, 28)
        g("第一条结果坐标 Y(兜底)", self.sel_first_result_y, 3, 1, 28)

        g("聊天区滚动坐标 X(兜底)", self.sel_chat_x, 4, 0, 28)
        g("聊天区滚动坐标 Y(兜底)", self.sel_chat_y, 4, 1, 28)

        g("OCR 左上 X", self.sel_ocr_left, 5, 0, 28)
        g("OCR 左上 Y", self.sel_ocr_top, 5, 1, 28)
        g("OCR 右下 X", self.sel_ocr_right, 6, 0, 28)
        g("OCR 右下 Y", self.sel_ocr_bottom, 6, 1, 28)

        ttk.Checkbutton(grid, text="启用 OCR（推荐）", variable=self.use_ocr).grid(row=7, column=0, sticky=W, pady=(6, 0))
        ttk.Checkbutton(grid, text="进入会话先滚到最底端（避免漏消息）", variable=self.ensure_bottom).grid(
            row=7, column=1, sticky=W, pady=(6, 0)
        )

        g("消息项 ClassName(可选)", self.sel_msg_item_class, 8, 0, 28)

        for i in range(6):
            grid.grid_columnconfigure(i, weight=0)

        # 按钮区
        btns = ttk.Frame(self.root, padding=10)
        btns.pack(fill=X)
        self.btn_start = ttk.Button(btns, text="开始导出", command=self._start)
        self.btn_stop = ttk.Button(btns, text="停止", command=self._stop)
        self.btn_capture_search_pt = ttk.Button(btns, text="抓取搜索框坐标(5秒)", command=self._capture_search_point)
        self.btn_capture_chat_pt = ttk.Button(btns, text="抓取聊天区坐标(5秒)", command=self._capture_chat_point)
        self.btn_capture_first_result_pt = ttk.Button(
            btns, text="抓取第一条结果坐标(10秒)", command=self._capture_first_result_point
        )
        self.btn_capture_ocr_lt = ttk.Button(btns, text="抓取OCR左上(5秒)", command=self._capture_ocr_left_top)
        self.btn_capture_ocr_rb = ttk.Button(btns, text="抓取OCR右下(5秒)", command=self._capture_ocr_right_bottom)
        self.btn_save_cfg = ttk.Button(btns, text="保存配置", command=self._save_config_safely)
        self.btn_open_logs = ttk.Button(btns, text="打开 logs 目录", command=self._open_logs_dir)

        # 用 grid 两行布局，避免按钮被挤到看不到
        self.btn_start.grid(row=0, column=0, sticky="w")
        self.btn_stop.grid(row=0, column=1, sticky="w", padx=(8, 0))
        self.btn_save_cfg.grid(row=0, column=2, sticky="w", padx=(8, 0))
        self.btn_open_logs.grid(row=0, column=3, sticky="w", padx=(8, 0))

        self.btn_capture_search_pt.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.btn_capture_first_result_pt.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(8, 0))
        self.btn_capture_chat_pt.grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
        self.btn_capture_ocr_lt.grid(row=1, column=3, sticky="w", padx=(8, 0), pady=(8, 0))
        self.btn_capture_ocr_rb.grid(row=1, column=4, sticky="w", padx=(8, 0), pady=(8, 0))

        # 日志区
        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill=BOTH, expand=True)

        self.txt = tk.Text(bottom, wrap="word")
        self.txt.pack(side=LEFT, fill=BOTH, expand=True)
        sb = ttk.Scrollbar(bottom, orient=VERTICAL, command=self.txt.yview)
        sb.pack(side=RIGHT, fill=Y)
        self.txt.configure(yscrollcommand=sb.set)

        self._ui_log("就绪。建议先打开千牛并登录，然后点击“开始导出”。")
        self._ui_log("提示：建议抓 5 个点：搜索框、第一条结果、聊天区、OCR左上、OCR右下。然后开始导出。")

    # ---------------- Config ----------------
    def _config_path(self) -> Path:
        return self.base_dir / CONFIG_NAME

    def _load_config_safely(self) -> None:
        p = self._config_path()
        if not p.exists():
            return
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return

        # 只做宽松读取：字段缺失就跳过
        self.names_path.set(data.get("names_path", self.names_path.get()))
        self.out_csv_path.set(data.get("out_csv_path", self.out_csv_path.get()))

        self.sleep_seconds.set(float(data.get("sleep_seconds", self.sleep_seconds.get())))
        self.max_scroll_pages.set(int(data.get("max_scroll_pages", self.max_scroll_pages.get())))
        self.stop_no_new_rounds.set(int(data.get("stop_no_new_rounds", self.stop_no_new_rounds.get())))
        self.global_search_timeout.set(float(data.get("global_search_timeout", self.global_search_timeout.get())))

        sel = data.get("selectors", {}) or {}
        self.sel_main_title.set(sel.get("main_window_name_contains", self.sel_main_title.get()))
        self.sel_search_x.set(str(sel.get("search_point_x", self.sel_search_x.get() or "")))
        self.sel_search_y.set(str(sel.get("search_point_y", self.sel_search_y.get() or "")))
        self.sel_first_result_x.set(str(sel.get("first_result_point_x", self.sel_first_result_x.get() or "")))
        self.sel_first_result_y.set(str(sel.get("first_result_point_y", self.sel_first_result_y.get() or "")))
        self.sel_chat_x.set(str(sel.get("chat_scroll_point_x", self.sel_chat_x.get() or "")))
        self.sel_chat_y.set(str(sel.get("chat_scroll_point_y", self.sel_chat_y.get() or "")))
        self.sel_ocr_left.set(str(sel.get("chat_ocr_left", self.sel_ocr_left.get() or "")))
        self.sel_ocr_top.set(str(sel.get("chat_ocr_top", self.sel_ocr_top.get() or "")))
        self.sel_ocr_right.set(str(sel.get("chat_ocr_right", self.sel_ocr_right.get() or "")))
        self.sel_ocr_bottom.set(str(sel.get("chat_ocr_bottom", self.sel_ocr_bottom.get() or "")))
        # options（只读入常用开关）
        opts = data.get("options", {}) or {}
        if "use_ocr" in opts:
            self.use_ocr.set(bool(opts.get("use_ocr")))
        if "ensure_bottom_before_scrape" in opts:
            self.ensure_bottom.set(bool(opts.get("ensure_bottom_before_scrape")))
        self.sel_msg_item_class.set(sel.get("message_item_class_name", self.sel_msg_item_class.get()))
        self.sel_close_btn_name.set(sel.get("close_button_name", self.sel_close_btn_name.get()))

    def _save_config_safely(self) -> None:
        try:
            data = {
                "names_path": self.names_path.get(),
                "out_csv_path": self.out_csv_path.get(),
                "sleep_seconds": self.sleep_seconds.get(),
                "max_scroll_pages": self.max_scroll_pages.get(),
                "stop_no_new_rounds": self.stop_no_new_rounds.get(),
                "global_search_timeout": self.global_search_timeout.get(),
                "selectors": asdict(self._build_selectors()),
                "options": {
                    "use_ocr": bool(self.use_ocr.get()),
                    "ensure_bottom_before_scrape": bool(self.ensure_bottom.get()),
                },
            }
            self._config_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self._ui_log(f"[info] 已保存配置：{self._config_path()}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    # ---------------- Helpers ----------------
    def _ui_log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self.txt.insert(END, f"[{ts}] {msg}\n")
        self.txt.see(END)

    def _logger(self, msg: str) -> None:
        # worker 线程里调用：用 after 回到主线程更新 UI
        self.root.after(0, lambda: self._ui_log(msg))

    def _choose_names(self) -> None:
        # 既支持选文件，也支持选目录（目录下自动找 names.txt/names.csv）
        p = filedialog.askopenfilename(
            title="选择昵称文件（.txt/.csv）",
            filetypes=[("Text/CSV", "*.txt *.csv"), ("All", "*.*")],
        )
        if p:
            self.names_path.set(p)
            return

        d = filedialog.askdirectory(title="或选择一个目录（会自动寻找 names.txt/names.csv）")
        if d:
            self.names_path.set(d)

    def _choose_out_csv(self) -> None:
        p = filedialog.asksaveasfilename(
            title="选择输出 CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="chat_history.csv",
        )
        if p:
            self.out_csv_path.set(p)

    def _open_logs_dir(self) -> None:
        # Windows 用 explorer 打开
        try:
            self.logs_dir.mkdir(parents=True, exist_ok=True)
            import os

            os.startfile(str(self.logs_dir))
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _build_selectors(self) -> QNSelectors:
        def none_if_empty(s: str) -> str | None:
            s = (s or "").strip()
            return s if s else None

        def int_or_none(s: str) -> int | None:
            s = (s or "").strip()
            if not s:
                return None
            try:
                return int(float(s))
            except Exception:
                return None

        return QNSelectors(
            main_window_name_contains=(self.sel_main_title.get() or "千牛").strip() or "千牛",
            search_point_x=int_or_none(self.sel_search_x.get()),
            search_point_y=int_or_none(self.sel_search_y.get()),
            first_result_point_x=int_or_none(self.sel_first_result_x.get()),
            first_result_point_y=int_or_none(self.sel_first_result_y.get()),
            chat_scroll_point_x=int_or_none(self.sel_chat_x.get()),
            chat_scroll_point_y=int_or_none(self.sel_chat_y.get()),
            chat_ocr_left=int_or_none(self.sel_ocr_left.get()),
            chat_ocr_top=int_or_none(self.sel_ocr_top.get()),
            chat_ocr_right=int_or_none(self.sel_ocr_right.get()),
            chat_ocr_bottom=int_or_none(self.sel_ocr_bottom.get()),
            message_item_class_name=none_if_empty(self.sel_msg_item_class.get()),
            close_button_name=(self.sel_close_btn_name.get() or "关闭").strip() or "关闭",
        )

    def _build_options(self) -> ScrapeOptions:
        return ScrapeOptions(
            sleep_seconds=float(self.sleep_seconds.get()),
            max_scroll_pages=int(self.max_scroll_pages.get()),
            stop_no_new_rounds=int(self.stop_no_new_rounds.get()),
            global_search_timeout=float(self.global_search_timeout.get()),
            use_ocr=bool(self.use_ocr.get()),
            ensure_bottom_before_scrape=bool(self.ensure_bottom.get()),
        )

    def _refresh_buttons(self) -> None:
        running = self.worker_thread is not None and self.worker_thread.is_alive()
        self.btn_start.config(state=("disabled" if running else "normal"))
        self.btn_stop.config(state=("normal" if running else "disabled"))
        self.btn_capture_search_pt.config(state=("disabled" if running else "normal"))
        self.btn_capture_chat_pt.config(state=("disabled" if running else "normal"))
        self.btn_capture_first_result_pt.config(state=("disabled" if running else "normal"))
        self.btn_capture_ocr_lt.config(state=("disabled" if running else "normal"))
        self.btn_capture_ocr_rb.config(state=("disabled" if running else "normal"))

    def _countdown(self, title: str, seconds: int) -> None:
        for i in range(seconds, 0, -1):
            self._ui_log(f"[info] {title}：请在 {i} 秒内把鼠标指到目标控件上…")
            self.root.update()
            time.sleep(1)

    def _describe_control(self, c) -> str:
        try:
            name = getattr(c, "Name", "") or ""
        except Exception:
            name = ""
        try:
            aid = getattr(c, "AutomationId", "") or ""
        except Exception:
            aid = ""
        try:
            cls = getattr(c, "ClassName", "") or ""
        except Exception:
            cls = ""
        try:
            ctype = getattr(c, "ControlTypeName", "") or ""
        except Exception:
            ctype = ""
        return f"Name='{name}' AutomationId='{aid}' ClassName='{cls}' ControlType='{ctype}'"

    def _capture_control_from_cursor(self):
        # 延迟 import，避免没装 uiautomation 时 GUI 启动失败
        import uiautomation as auto

        c = auto.ControlFromCursor()
        if not c:
            raise RuntimeError("未能从鼠标位置获取控件（ControlFromCursor 返回空）。")
        return c

    # 已移除“抓取控件 selector”相关功能（Qt 不暴露子控件时不可靠），仅保留坐标兜底方式

    def _capture_point(self, title: str, seconds: int) -> tuple[int, int]:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        self._countdown(title, seconds)
        pt = POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)) == 0:
            raise RuntimeError("GetCursorPos 失败")
        self._ui_log(f"[info] {title}：已捕获坐标 x={pt.x} y={pt.y}")

        # 顺便推断主窗口标题包含（从坐标点拿顶层窗口名）
        try:
            import uiautomation as auto

            c = auto.ControlFromPoint(int(pt.x), int(pt.y))
            if c:
                top = c.GetTopLevelControl()
                top_name = (getattr(top, "Name", "") or "").strip()
                if top_name:
                    # 自动填入，避免你把“千牛”填死导致坐标校验失败
                    self.sel_main_title.set(top_name[:32])
                    self._ui_log(f"[info] 已自动更新主窗口标题包含（截断前32字）：{self.sel_main_title.get()}")
        except Exception:
            pass

        return int(pt.x), int(pt.y)

    def _capture_search_point(self) -> None:
        try:
            x, y = self._capture_point("抓取搜索框坐标", CAPTURE_COUNTDOWN_SECONDS)
            self.sel_search_x.set(str(x))
            self.sel_search_y.set(str(y))
            self._ui_log("[info] 已填入搜索框坐标兜底（Qt 不暴露控件时可用）。")
        except Exception as e:
            messagebox.showerror("抓取搜索框坐标失败", str(e))

    def _capture_first_result_point(self) -> None:
        try:
            x, y = self._capture_point("抓取第一条搜索结果坐标", CAPTURE_COUNTDOWN_SECONDS_LONG)
            self.sel_first_result_x.set(str(x))
            self.sel_first_result_y.set(str(y))
            self._ui_log("[info] 已填入第一条结果坐标兜底（用于点击进入会话）。")
        except Exception as e:
            messagebox.showerror("抓取第一条结果坐标失败", str(e))

    def _capture_chat_point(self) -> None:
        try:
            x, y = self._capture_point("抓取聊天区坐标", CAPTURE_COUNTDOWN_SECONDS)
            self.sel_chat_x.set(str(x))
            self.sel_chat_y.set(str(y))
            self._ui_log("[info] 已填入聊天区滚动坐标兜底。")
        except Exception as e:
            messagebox.showerror("抓取聊天区坐标失败", str(e))

    def _capture_ocr_left_top(self) -> None:
        try:
            x, y = self._capture_point("抓取OCR左上", CAPTURE_COUNTDOWN_SECONDS)
            self.sel_ocr_left.set(str(x))
            self.sel_ocr_top.set(str(y))
            self._ui_log("[info] 已填入 OCR 左上坐标。")
        except Exception as e:
            messagebox.showerror("抓取OCR左上失败", str(e))

    def _capture_ocr_right_bottom(self) -> None:
        try:
            x, y = self._capture_point("抓取OCR右下", CAPTURE_COUNTDOWN_SECONDS)
            self.sel_ocr_right.set(str(x))
            self.sel_ocr_bottom.set(str(y))
            self._ui_log("[info] 已填入 OCR 右下坐标。")
        except Exception as e:
            messagebox.showerror("抓取OCR右下失败", str(e))

    # ---------------- Run/Stop ----------------
    def _start(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return

        names_file = Path(self.names_path.get()).expanduser()
        out_csv = Path(self.out_csv_path.get()).expanduser()

        try:
            names = read_names_file(names_file)
        except Exception as e:
            messagebox.showerror("昵称文件读取失败", str(e))
            return

        if not names:
            messagebox.showwarning("提示", "昵称列表为空。")
            return

        selectors = self._build_selectors()
        options = self._build_options()

        self.stop_event.clear()
        self._ui_log("[info] 开始任务…（请保持千牛窗口可见，且不要最小化）")
        self._refresh_buttons()

        def worker():
            try:
                run_scrape(
                    names=names,
                    out_csv=out_csv,
                    selectors=selectors,
                    options=options,
                    logs_dir=self.logs_dir,
                    logger=self._logger,
                    stop_flag=self.stop_event.is_set,
                )
                self._logger("[info] 任务结束。")
            except Exception as e:
                self._logger(f"[error] 运行失败：{e!r}")
            finally:
                self.root.after(0, self._refresh_buttons)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()
        self._refresh_buttons()

    def _stop(self) -> None:
        self.stop_event.set()
        self._ui_log("[info] 已请求停止：将会在当前步骤完成后退出。")
        self._refresh_buttons()


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except Exception:
        pass
    GuiApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

