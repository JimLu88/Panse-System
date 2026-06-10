"""
千牛主窗口 HWND 枚举（与校准模块同源思路）：按进程名 + 面积最大，
避免仅靠 UIA NameContains 在最小化 / 非前台时找不到控件。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

# v1.6.12：引导/广告类子窗口标题或类名（命中即跳过，绝不作为聊天主窗）。
# 注意：必须精确到这些噪声窗口，不能误伤主窗（如 lightrat-接待中心）。
# v1.6.14 修：之前只落地了引用、漏了定义 → NameError 被外层 except 吞 → 恒返回 None
# → 全程"找不到千牛"。本次补回定义。
_QN_TITLE_CLASS_BLACKLIST = (
    "appguideview",   # 千牛引导/广告页（日志实测误选项）
    "guideview",
    "使用指南",
    "引导页",
    "splashwindow",   # 启动闪屏
    "alimaxwindow",   # 千牛广告/活动弹窗类名
    "千牛登录",        # v1.6.16：登录窗（白屏）绝不作为聊天主窗
    "登录",
)

# v1.6.16：聊天主窗优先级分两档（修 v1.6.15 误把"工作台/登录"当主窗）：
#   一级（priority=2，最高，压过一切面积）：真正的聊天接待窗
#   二级（priority=1，仅在无一级窗口时兜底）：工作台等容器窗
# 实测真聊天窗标题为「lightrat-接待中心」，故"接待中心"必须最高优先。
_QN_MAIN_TITLE_HINTS_PRIMARY = (
    "接待中心",
    "聊天",
)
_QN_MAIN_TITLE_HINTS_SECONDARY = (
    "工作台",
    "千牛工作台",
)

# v1.6.22：已知「绝不是千牛」的前台程序 exe 关键字。
# 修根本 bug：标题里含「千牛/接待中心/工作台」的窗口（如 Claude Code 正显示本项目
# 对话、浏览器开着千牛相关网页、编辑器/资源管理器在「千牛」目录）被旧逻辑误判为千牛主窗
# → 置前置错对象（实测：最小化所有窗口后，跳出来的是 Claude Code 而非千牛）。
# 命中此表的进程：绝不允许靠「标题含千牛」兜底匹配为千牛。
_FOREIGN_EXE_KEYWORDS = (
    "claude", "code", "cursor", "node", "electron",
    "chrome", "msedge", "firefox", "iexplore", "opera", "brave",
    "explorer", "powershell", "pwsh", "cmd", "conhost",
    "windowsterminal", "wt", "python", "pythonw",
    "notepad", "devenv", "idea64", "pycharm64", "sublime_text", "obsidian",
    "wechat", "dingtalk", "feishu",
)


def enum_qianniu_main_hwnd(*, visible_only: bool) -> int | None:
    """
    返回面积最大的千牛 AliWorkbench / 标题含「千牛」的顶层 HWND。
    visible_only=True 时跳过不可见窗口；False 时也枚举最小化等到 HWND。
    """
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        my_pid = os.getpid()

        # (priority, area, hwnd)：priority 高者优先；同 priority 再比面积
        candidates: list[tuple[int, int, int]] = []

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            if visible_only and not user32.IsWindowVisible(hwnd):
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""

            # 类名（用于黑名单二次判断；引导页有时标题为空但类名固定）
            cbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cbuf, 256)
            class_name = cbuf.value or ""

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) == my_pid:
                return True

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

            exe_low = exe_name.lower()
            # v1.6.22 根治"置前置错成 Claude Code"：
            # 标题兜底（标题含「千牛/接待中心」即判千牛）只在「进程不是已知外来程序」时才信任。
            #  - exe 读得到且是 Claude/浏览器/编辑器/IM 等 → 即便标题含「千牛/接待中心」也不当千牛；
            #  - exe 读不到（千牛以管理员权限运行、本程序非管理员时 OpenProcess 受限）→ exe_low 为空
            #    → 不在外来表内 → 仍允许靠标题强标记兜底，保证真千牛不漏。
            # 真聊天窗（标题「lightrat-接待中心」、exe=aliworkbench）走 exe 匹配，不受影响。
            exe_is_qianniu = ("aliworkbench" in exe_low) or ("qianniu" in exe_low)
            exe_is_foreign = bool(exe_low) and any(
                k in exe_low for k in _FOREIGN_EXE_KEYWORDS
            )
            title_qn_marker = (
                ("千牛" in title or "接待中心" in title or "千牛工作台" in title)
                and "校准" not in title
                and "AIWorkbench" not in title
            )
            is_qianniu = exe_is_qianniu or (title_qn_marker and not exe_is_foreign)
            if not is_qianniu:
                return True

            # v1.6.12：引导/广告类子窗口（AppGuideView 等）跳过，绝不作为聊天主窗
            _tl = f"{title} {class_name}".lower()
            if any(bad in _tl for bad in _QN_TITLE_CLASS_BLACKLIST):
                return True

            # v1.6.24 根治"千牛一直开着(最小化)却 千牛HWND=None、叫不出窗口"：
            # 最小化窗口 GetWindowRect 返回离屏占位坐标(约 -32000)，算出的 w/h 为负/极小，
            # 旧 size 闸门(w<400 或 h<300)会把「最小化的千牛」一并拒掉 → 枚举恒返回 None
            # → 退回任务栏点击(点偏)→ 退回 minibubble(点到 Claude)，一条链全错。
            # 最小化时跳过屏幕尺寸校验，给名义面积参与排序，保证能被枚举到并由 HWND 直接还原。
            if bool(user32.IsIconic(hwnd)):
                area = 1_000_000
            else:
                rect = wintypes.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                w = rect.right - rect.left
                h_ = rect.bottom - rect.top
                if w < 400 or h_ < 300:
                    return True
                area = w * h_

            # v1.6.16：分两档优先。接待中心/聊天=2（最高），工作台=1，其它=0。
            if any(h2 in title for h2 in _QN_MAIN_TITLE_HINTS_PRIMARY):
                priority = 2
            elif any(h2 in title for h2 in _QN_MAIN_TITLE_HINTS_SECONDARY):
                priority = 1
            else:
                priority = 0
            candidates.append((priority, area, int(hwnd)))
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        if not candidates:
            return None
        # 先按 priority 降序，再按面积降序
        candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
        return candidates[0][2]
    except Exception:
        return None


def find_qianniu_main_hwnd_best_effort() -> int | None:
    """先可见窗口；没有再尝试包含最小化等非前台枚举。"""
    h = enum_qianniu_main_hwnd(visible_only=True)
    if h:
        return h
    return enum_qianniu_main_hwnd(visible_only=False)


def hwnd_is_user_visible(hwnd: int) -> bool:
    """
    千牛是否处于用户可见状态（非最小化、非 0 尺寸）。
    仅 HWND==GetForegroundWindow 不够：最小化/被遮挡时用户仍感觉「没弹出」。
    """
    if hwnd <= 0:
        return False
    try:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return False
        if user32.IsIconic(hwnd):
            return False
        if not user32.IsWindowVisible(hwnd):
            return False
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        w = rect.right - rect.left
        h_ = rect.bottom - rect.top
        return w >= 400 and h_ >= 300
    except Exception:
        return False
