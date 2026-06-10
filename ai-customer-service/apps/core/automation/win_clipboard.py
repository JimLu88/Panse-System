from __future__ import annotations

import ctypes
from ctypes import wintypes


_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

# 64 位 Windows 上句柄/指针都是 64 位，必须用 c_void_p / c_size_t，
# 不能用默认的 c_int（32 位），否则大地址会 OverflowError。
_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.restype = ctypes.c_void_p
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.restype = ctypes.c_void_p
_user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.GetClipboardData.argtypes = [wintypes.UINT]
_user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
_user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]


def set_clipboard_text(text: str) -> None:
    """
    Set Unicode text to Windows clipboard using Win32 APIs (no pywin32 dependency).
    """
    if text is None:
        text = ""
    data = str(text).replace("\r\n", "\n").replace("\n", "\r\n")
    # Null-terminated UTF-16LE
    raw = (data + "\x00").encode("utf-16le")

    if not _user32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        if not _user32.EmptyClipboard():
            raise RuntimeError("EmptyClipboard failed")

        hmem = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw))
        if not hmem:
            raise MemoryError("GlobalAlloc failed")
        locked = _kernel32.GlobalLock(hmem)
        if not locked:
            _kernel32.GlobalFree(hmem)
            raise MemoryError("GlobalLock failed")
        try:
            ctypes.memmove(locked, raw, len(raw))
        finally:
            _kernel32.GlobalUnlock(hmem)

        if not _user32.SetClipboardData(CF_UNICODETEXT, hmem):
            _kernel32.GlobalFree(hmem)
            raise RuntimeError("SetClipboardData failed")
        # On success, system owns hmem; do not free.
    finally:
        _user32.CloseClipboard()


def get_clipboard_text() -> str | None:
    """
    读取剪贴板的 Unicode 文本。

    返回 None 的情况：
      - 剪贴板中不是文本格式（图片、文件列表等）
      - OpenClipboard 失败（其他进程占用）
      - 剪贴板为空

    实现细节：
      - 用 CF_UNICODETEXT 直接读 UTF-16LE，不依赖 pywin32
      - 把 \\r\\n 还原为 \\n，便于上层做行匹配
    """
    if not _user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None

    # OpenClipboard 可能因为千牛/微信等正在写而瞬时失败，简单重试 3 次
    opened = False
    for _ in range(3):
        if _user32.OpenClipboard(None):
            opened = True
            break
    if not opened:
        return None

    try:
        h = _user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        locked = _kernel32.GlobalLock(h)
        if not locked:
            return None
        try:
            # CF_UNICODETEXT 是 null-terminated UTF-16LE
            raw = ctypes.wstring_at(locked)
        finally:
            _kernel32.GlobalUnlock(h)
        if raw is None:
            return None
        return str(raw).replace("\r\n", "\n")
    finally:
        _user32.CloseClipboard()

