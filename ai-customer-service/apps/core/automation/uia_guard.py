"""
v1.6.17 UIA/COM 并发守护。

崩溃根因（crash_20260601_150923.log）：
  risk_warning_revise(4s) 与 popup_worker(5s) 两个后台线程同时调 uiautomation
  遍历控件树（GetChildren/GetFirstChildControl/GetNextSiblingControl），
  COM 在多线程并发访问下抛 0x80040155 + access violation → 整进程闪退。

修法（本模块）：
  1. 全局 _UIA_LOCK：任一时刻只允许一个线程遍历 UIA 控件树（串行化）。
  2. 每个使用 UIA 的后台线程在入口做一次 CoInitializeEx（COM 单元初始化）。
  用法：
    在后台线程 _run 顶部： init_com_for_thread()
    包裹每次 UIA 遍历：    with uia_lock(): ...auto.GetRootControl()...
"""
from __future__ import annotations

import threading

# 全局 UIA 串行锁：所有 uiautomation 控件树遍历必须持锁进行。
_UIA_LOCK = threading.RLock()

# 记录已初始化 COM 的线程 id，避免重复初始化。
_com_inited_threads: set[int] = set()
_com_lock = threading.Lock()


def uia_lock() -> "threading.RLock":
    """返回全局 UIA 锁，用于 `with uia_lock():` 包裹控件树遍历。"""
    return _UIA_LOCK


def init_com_for_thread() -> None:
    """在当前线程初始化 COM（多线程套间 MTA）。同线程只做一次，异常吞掉。

    UIA(comtypes) 在未初始化 COM 的线程里遍历控件树会触发 0x80040155。
    """
    tid = threading.get_ident()
    with _com_lock:
        if tid in _com_inited_threads:
            return
        _com_inited_threads.add(tid)
    try:
        import comtypes
        # MTA（多线程套间）：与 uiautomation 默认初始化方式一致，避免与主线程 STA 冲突
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except Exception:
        # 已被初始化为其它套间 / comtypes 不可用 → 忽略；锁仍能保证串行安全
        pass
