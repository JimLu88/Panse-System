from __future__ import annotations

import os
import sys


def _apply_cli_profile() -> None:
    """支持 ``AIWorkbench.exe --profile 店A``，配置与数据库落在 instances/店A/。"""
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--profile" and i + 1 < len(args):
            os.environ["AIWORKBENCH_PROFILE"] = args[i + 1].strip()
            return


def _enable_dpi_awareness() -> None:
    """
    强制全进程使用 Per-Monitor V2 DPI awareness（物理像素坐标系）。
    必须在 QApplication 创建前调用，否则 Qt 已经选定坐标系，无法改变。

    这样 mss 截图、Vision AI 识别、ctypes 鼠标点击都统一用物理像素，
    即使 Windows 缩放设为 125% / 150% / 200% 也不会错位。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        # 老版 Windows 退回 SetProcessDpiAwareness(2) = PROCESS_PER_MONITOR_DPI_AWARE
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def _install_crash_capture() -> None:
    """全局崩溃捕获：未捕获异常 / C 扩展段错误的栈写入 data/logs/crash_*.log。

    为什么必须有：打包后的窗口 exe 一旦崩溃就直接闪退、不留任何痕迹，导致
    无法定位（反复盲修）。faulthandler 捕获 C 扩展段错误（mss/cv2/uiautomation
    等），sys.excepthook + threading.excepthook 捕获 Python 未处理异常。
    所有失败路径都吞掉，绝不让崩溃捕获本身再崩溃。
    """
    import datetime
    import traceback
    from pathlib import Path

    # project_root() = exe 同级目录（可写区），与 debug 截图同根，方便用户找
    try:
        from apps.core.runtime_paths import project_root
        log_dir = project_root() / "data" / "logs"
    except Exception:
        import tempfile
        log_dir = Path(tempfile.gettempdir())

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    crash_path = log_dir / f"crash_{stamp}.log"

    # ① C 扩展段错误（faulthandler 直接写文件句柄，崩溃瞬间也能落盘）
    try:
        import faulthandler
        fh = open(crash_path, "a", encoding="utf-8", buffering=1)
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:
        pass

    def _write(header: str, text: str) -> None:
        try:
            with open(crash_path, "a", encoding="utf-8") as f:
                f.write(f"\n===== {header} @ {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
                f.write(text)
                f.write("\n")
        except Exception:
            pass

    # ② Python 主线程未捕获异常
    def _excepthook(exc_type, exc, tb) -> None:
        _write("UNCAUGHT EXCEPTION", "".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook

    # ③ Python 子线程未捕获异常（QThread.run 内未捕获异常也能落盘）
    try:
        import threading

        def _thread_hook(args) -> None:
            _write(
                f"UNCAUGHT THREAD EXCEPTION ({getattr(args, 'thread', None)})",
                "".join(traceback.format_exception(
                    args.exc_type, args.exc_value, args.exc_traceback)),
            )

        threading.excepthook = _thread_hook
    except Exception:
        pass


def main() -> None:
    _install_crash_capture()
    _apply_cli_profile()
    _enable_dpi_awareness()

    from PyQt6.QtWidgets import QApplication

    from apps.core.runtime_paths import bootstrap_frozen_bundle
    from apps.ui.workbench_shell import WorkbenchShell

    bootstrap_frozen_bundle()
    app = QApplication(sys.argv)

    from apps.ui.jim_dark_theme import JIM_DARK_QSS
    app.setStyleSheet(JIM_DARK_QSS)

    w = WorkbenchShell()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
