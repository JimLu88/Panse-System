#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
千牛 PC 客户端聊天记录抓取（uiautomation）

目标：
- 读取 names.txt / names.csv（客户昵称列表）
- 遍历昵称：搜索 -> 打开会话 -> 向上翻页加载历史 -> 抽取消息 -> 去重 -> 追加写入 chat_history.csv

为什么要做成“可配置 selector”：
- 千牛 UI 版本/布局/语言差异会导致 AutomationId/ClassName/Name 不同
- 先跑通框架，再根据你运行时打印的控件树，把 selector 精确补齐即可稳定运行

运行建议：
- Windows 管理员权限运行（部分控件/滚动需要）
- 千牛窗口保持可见且不要最小化（UIA 对最小化窗口支持不稳定）

依赖：
    pip install uiautomation
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

import uiautomation as auto


# =========================
# 基础配置（你后续主要改这里）
# =========================


@dataclass(frozen=True)
class QNSelectors:
    """
    关键控件定位线索。

    - main_window_name_contains：千牛主窗口标题包含的文字（一般包含“千牛”）
    - search_edit_*：搜索框 Edit 的定位线索（建议最终用 AutomationId）
    - results_*：搜索结果/联系人列表（可选；如果 Enter 能直接打开会话可以先不配）
    - chat_list_*：聊天消息列表容器（强烈建议最终配上 AutomationId 或稳定 ClassName）
    - close_button_name：独立聊天窗口的关闭按钮名称（常见为“关闭”/“Close”）
    """

    # 主窗口
    main_window_name_contains: str = "千牛"

    # 搜索框（优先 AutomationId，次选 ClassName，最后 NameContains 兜底）
    search_edit_automation_id: Optional[str] = None
    search_edit_class_name: Optional[str] = None
    search_edit_name_contains: Optional[str] = "搜索"

    # 搜索结果（可选）
    results_list_automation_id: Optional[str] = None
    results_list_class_name: Optional[str] = None
    results_list_name_contains: Optional[str] = None

    # 聊天列表（消息容器）
    chat_list_automation_id: Optional[str] = None
    chat_list_class_name: Optional[str] = None
    chat_list_name_contains: Optional[str] = None

    # 单条消息项（可选：你确定后可填，能减少噪声控件）
    message_item_class_name: Optional[str] = None

    # 关闭按钮
    close_button_name: str = "关闭"


# =========================
# I/O：读取昵称列表
# =========================


def read_names(
    base_dir: Path,
    txt_name: str = "names.txt",
    csv_name: str = "names.csv",
    csv_column: str = "name",
) -> List[str]:
    """
    从 base_dir 下读取昵称列表：
    - names.txt：每行一个昵称
    - names.csv：默认读取列名为 csv_column 的列；若无表头则读取第一列
    """

    txt_path = base_dir / txt_name
    if txt_path.exists():
        names: List[str] = []
        for line in txt_path.read_text(encoding="utf-8-sig").splitlines():
            n = line.strip()
            if n:
                names.append(n)
        return names

    csv_path = base_dir / csv_name
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))

        if not rows:
            return []

        header = rows[0]
        if csv_column in header:
            idx = header.index(csv_column)
            out: List[str] = []
            for r in rows[1:]:
                if idx < len(r):
                    n = r[idx].strip()
                    if n:
                        out.append(n)
            return out

        # 兜底：取第一列（含无表头）
        out = []
        for r in rows:
            if not r:
                continue
            n = r[0].strip()
            if n and n.lower() != csv_column.lower():
                out.append(n)
        return out

    raise FileNotFoundError(f"未找到 {txt_path.name} 或 {csv_path.name}（请在脚本同目录放置其一）")


# =========================
# I/O：聊天 CSV
# =========================


CHAT_OUT_NAME = "chat_history.csv"
STATE_OUT_NAME = "state.jsonl"


def ensure_chat_csv_header(path: Path) -> None:
    """确保输出 CSV 有表头。"""
    if path.exists() and path.stat().st_size > 0:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["客户昵称", "发送者", "时间", "内容", "hash"])


def append_rows_csv(path: Path, rows: Sequence[Tuple[str, str, str, str, str]]) -> None:
    """追加写入多行记录。"""
    if not rows:
        return
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(list(r))


def load_existing_hashes(path: Path) -> Set[str]:
    """
    从已有 chat_history.csv 读取 hash 列，支持断点续爬/增量写入。
    - 如果文件不存在或为空，返回空集合
    """
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


# =========================
# 日志与控件树导出
# =========================


def now_str() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def log(msg: str) -> None:
    print(msg, flush=True)


def dump_control_tree(control: auto.Control, out_path: Path) -> None:
    """
    将控件树导出到文件，便于你把 ClassName/AutomationId/Name 发我做精确定位。
    """
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # PrintControlIdentifiers 会直接 print，这里用 stdout 重定向写入文件
        original = sys.stdout
        with out_path.open("w", encoding="utf-8") as f:
            sys.stdout = f
            control.PrintControlIdentifiers()
        sys.stdout = original
    except Exception as e:
        # 最差情况下只打印错误，不中断主流程
        try:
            sys.stdout = original
        except Exception:
            pass
        log(f"[warn] 导出控件树失败：{e!r}")


# =========================
# UIA：核心抓取逻辑
# =========================


class QNChatScraper:
    def __init__(self, selectors: QNSelectors, base_dir: Path, sleep_seconds: float = 2.0):
        self.s = selectors
        self.base_dir = base_dir
        self.sleep_seconds = sleep_seconds

    # -------- 主窗口定位 --------

    def get_main_window(self) -> auto.WindowControl:
        """
        定位千牛主窗口。
        先用 NameContains，失败再加深 searchDepth。
        """
        w = auto.WindowControl(searchDepth=2, NameContains=self.s.main_window_name_contains)
        if not w.Exists(1):
            w = auto.WindowControl(searchDepth=5, NameContains=self.s.main_window_name_contains)
        if not w.Exists(1):
            raise RuntimeError("未找到千牛主窗口：请确认千牛已打开且未最小化。")
        return w

    # -------- 搜索框定位与输入 --------

    def find_search_edit(self, main: auto.Control) -> auto.EditControl:
        """
        定位搜索框 Edit。
        """
        try:
            main.SetFocus()
        except Exception:
            pass

        if self.s.search_edit_automation_id:
            e = main.EditControl(AutomationId=self.s.search_edit_automation_id, searchDepth=12)
            if e.Exists(1):
                return e

        if self.s.search_edit_class_name:
            e = main.EditControl(ClassName=self.s.search_edit_class_name, searchDepth=12)
            if e.Exists(1):
                return e

        if self.s.search_edit_name_contains:
            e = main.EditControl(NameContains=self.s.search_edit_name_contains, searchDepth=14)
            if e.Exists(1):
                return e

        # 兜底：遍历找一个可用 Edit
        for c, _depth in auto.WalkControl(main, maxDepth=14):
            if isinstance(c, auto.EditControl) and c.IsEnabled:
                return c

        raise RuntimeError("未找到搜索框 Edit：需要你提供该控件的 AutomationId/ClassName/Name。")

    def set_edit_text_fast(self, edit: auto.EditControl, text: str) -> None:
        """
        设置文本（更稳的做法：全选清空 -> SetValue；失败则剪贴板粘贴）。
        """
        edit.Click()
        time.sleep(0.2)
        auto.SendKeys("^a")
        time.sleep(0.05)
        auto.SendKeys("{DEL}")
        time.sleep(0.05)

        try:
            edit.GetValuePattern().SetValue(text)
        except Exception:
            auto.SetClipboardText(text)
            time.sleep(0.05)
            auto.SendKeys("^v")

    def open_chat_by_search(self, nickname: str) -> auto.Control:
        """
        输入昵称 -> 等待 -> Enter 触发 -> 等待会话打开。
        返回聊天容器：
        - 若出现独立窗口（标题含昵称），返回该窗口
        - 否则返回主窗口（右侧聊天面板）
        """
        main = self.get_main_window()
        edit = self.find_search_edit(main)

        self.set_edit_text_fast(edit, nickname)
        time.sleep(self.sleep_seconds)  # 关键：等搜索结果刷新

        auto.SendKeys("{ENTER}")
        time.sleep(self.sleep_seconds)  # 关键：等会话打开/切换

        # 尝试捕获弹窗会话（标题包含昵称）
        chat_win = auto.WindowControl(searchDepth=3, NameContains=nickname)
        if chat_win.Exists(0.5):
            try:
                chat_win.SetFocus()
            except Exception:
                pass
            return chat_win

        return main

    # -------- 聊天列表定位 --------

    def find_chat_list(self, container: auto.Control) -> auto.Control:
        """
        定位聊天消息列表容器（List 或 Pane）。
        """
        if self.s.chat_list_automation_id:
            c = container.Control(AutomationId=self.s.chat_list_automation_id, searchDepth=16)
            if c.Exists(1):
                return c

        if self.s.chat_list_class_name:
            c = container.Control(ClassName=self.s.chat_list_class_name, searchDepth=16)
            if c.Exists(1):
                return c

        if self.s.chat_list_name_contains:
            c = container.Control(NameContains=self.s.chat_list_name_contains, searchDepth=16)
            if c.Exists(1):
                return c

        # 兜底：找 ScrollPattern 可垂直滚动的控件
        for c, _depth in auto.WalkControl(container, maxDepth=16):
            try:
                sp = c.GetScrollPattern()
                if sp and sp.VerticallyScrollable:
                    return c
            except Exception:
                continue

        raise RuntimeError("未找到聊天消息列表容器：需要你提供该控件的 AutomationId/ClassName/Name。")

    # -------- 消息抽取与去重 --------

    _re_time1 = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
    _re_time2 = re.compile(r"^\d{4}[-/\.]\d{1,2}[-/\.]\d{1,2}\s+\d{1,2}:\d{2}(:\d{2})?$")

    def looks_like_time(self, t: str) -> bool:
        t = t.strip().replace("：", ":")
        return bool(self._re_time1.match(t) or self._re_time2.match(t))

    def hash_message(self, ts: str, text: str) -> str:
        """
        使用 时间+文本 生成 hash，用于精准去重。
        - ts 可能为空（取不到时间时也尽量去重）
        """
        raw = (ts.strip() + "|" + text.strip()).encode("utf-8", errors="ignore")
        return hashlib.sha256(raw).hexdigest()

    def extract_message_fields(self, msg_item: auto.Control) -> Tuple[str, str, str]:
        """
        尝试从单条消息控件中提取：发送者、时间、内容。

        说明：
        - 千牛不同版本可能是原生控件/网页控件混合
        - 这里先用“宽松策略”：遍历子孙控件收集所有可见文本，再做启发式拆分
        - 你回传控件树后，我们再把这里改成“按固定子控件精确提取”
        """
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

        # 完全无文本：大概率是图片/富媒体/占位
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
        # 发送者通常短一些；且不太像时间
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
        """
        获取当前可见的“消息项”候选集合。
        """
        children = chat_list.GetChildren()
        if children:
            return children

        # 兜底：深度遍历一层，避免 chat_list 本身不直接暴露 children
        out: List[auto.Control] = []
        for c, _depth in auto.WalkControl(chat_list, maxDepth=3):
            out.append(c)
        return out

    # -------- 翻页（向上滚动加载历史） --------

    def scroll_up_once(self, chat_list: auto.Control) -> None:
        """
        向上滚动触发历史记录加载。
        - 必须先让 chat_list 获得焦点
        - 必须等待加载（你要求 sleep(2) 左右）
        """
        try:
            chat_list.Click()
        except Exception:
            pass

        time.sleep(self.sleep_seconds)

        # 优先滚轮向上；失败则 PageUp
        try:
            auto.WheelUp(12)
        except Exception:
            auto.SendKeys("{PGUP}")

        time.sleep(self.sleep_seconds)

    def scrape_one_customer(
        self,
        nickname: str,
        existing_hashes: Set[str],
        max_scroll_pages: int = 60,
        stop_no_new_rounds: int = 3,
    ) -> List[Tuple[str, str, str, str, str]]:
        """
        抓取单个客户聊天记录，返回新增 rows（已过滤 existing_hashes）。

        stop_no_new_rounds：
        - 连续几轮“没有新增消息”就停止（通常表示到顶或定位不对）
        """
        container = self.open_chat_by_search(nickname)
        chat_list = self.find_chat_list(container)

        seen_local: Set[str] = set()  # 本客户本轮去重（避免同屏重复）
        rows: List[Tuple[str, str, str, str, str]] = []

        def read_visible() -> int:
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

        # 初始读取
        read_visible()

        no_new = 0
        for _i in range(max_scroll_pages):
            self.scroll_up_once(chat_list)
            added = read_visible()
            if added == 0:
                no_new += 1
            else:
                no_new = 0
            if no_new >= stop_no_new_rounds:
                break

        # 如果是弹窗会话，尽量关闭
        self.close_chat_if_popup(container)
        # 清空搜索框，避免下一轮残留
        self.clear_search_box()

        return rows

    # -------- 收尾操作 --------

    def close_chat_if_popup(self, container: auto.Control) -> None:
        """
        如果当前会话是独立窗口，尝试点击关闭按钮。
        """
        try:
            if isinstance(container, auto.WindowControl):
                btn = container.ButtonControl(Name=self.s.close_button_name, searchDepth=6)
                if btn.Exists(0.5):
                    btn.Click()
                    time.sleep(1)
        except Exception:
            pass

    def clear_search_box(self) -> None:
        """
        清空搜索框：便于下一个昵称准确输入。
        """
        try:
            main = self.get_main_window()
            edit = self.find_search_edit(main)
            edit.Click()
            time.sleep(0.1)
            auto.SendKeys("^a")
            time.sleep(0.05)
            auto.SendKeys("{DEL}")
            time.sleep(0.2)
        except Exception:
            pass


# =========================
# 主流程
# =========================


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    out_csv = base_dir / CHAT_OUT_NAME
    logs_dir = base_dir / "logs"

    # UIA 全局超时不要太长，失败要快速暴露以便你定位控件
    auto.uiautomation.SetGlobalSearchTimeout(2.0)

    selectors = QNSelectors(
        # 你后续拿到控件信息后，把下面这些字段逐步补齐即可：
        # search_edit_automation_id="...",
        # chat_list_automation_id="...",
    )

    log(f"[info] selectors={asdict(selectors)}")

    names = read_names(base_dir)
    if not names:
        log("[warn] 昵称列表为空。")
        return

    ensure_chat_csv_header(out_csv)
    existing_hashes = load_existing_hashes(out_csv)
    log(f"[info] 已存在 hash 数：{len(existing_hashes)}")

    scraper = QNChatScraper(selectors, base_dir=base_dir, sleep_seconds=2.0)

    for idx, nickname in enumerate(names, start=1):
        log(f"\n[{idx}/{len(names)}] 开始抓取：{nickname}")
        try:
            rows = scraper.scrape_one_customer(nickname, existing_hashes=existing_hashes)
            log(f"[info] 新增 {len(rows)} 条")
            append_rows_csv(out_csv, rows)
            for r in rows:
                existing_hashes.add(r[-1])
        except Exception as e:
            log(f"[error] 抓取失败：{e!r}")
            # 失败时把控件树自动导出，减少你手动复制负担
            try:
                main_win = scraper.get_main_window()
                tree_path = logs_dir / f"control-tree-{now_str()}-{idx}.txt"
                dump_control_tree(main_win, tree_path)
                log(f"[info] 已导出主窗口控件树：{tree_path}")
            except Exception as e2:
                log(f"[warn] 无法导出控件树：{e2!r}")

            # 一轮失败后，尽量清空搜索框，避免卡死在错误状态
            scraper.clear_search_box()

        # 客户间缓冲
        time.sleep(1)

    log(f"\n[done] 输出文件：{out_csv}")


if __name__ == "__main__":
    main()

