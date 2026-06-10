"""将位图写入 Windows 剪贴板（CF_DIB），供聊天窗口 Ctrl+V 粘贴图片。"""

from __future__ import annotations

import ctypes
from io import BytesIO
from pathlib import Path

from PIL import Image

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

CF_DIB = 8
GMEM_MOVEABLE = 0x0002


def set_clipboard_dib_from_image_path(path: Path) -> None:
    """从本地 PNG/JPEG 等文件加载并写入剪贴板为 DIB。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(str(p))
    im = Image.open(p)
    set_clipboard_dib_from_pil(im)


def set_clipboard_dib_from_pil(im: Image.Image) -> None:
    """RGB 图像 → BMP 内存 → 去掉 14 字节文件头 → CF_DIB。"""
    bio = BytesIO()
    im.convert("RGB").save(bio, format="BMP")
    raw = bio.getvalue()
    if len(raw) < 14 + 40:
        raise ValueError("BMP 数据过短")
    dib = raw[14:]
    if not _user32.OpenClipboard(None):
        raise RuntimeError("OpenClipboard failed")
    try:
        if not _user32.EmptyClipboard():
            raise RuntimeError("EmptyClipboard failed")
        size = len(dib)
        hmem = _kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not hmem:
            raise MemoryError("GlobalAlloc failed")
        locked = _kernel32.GlobalLock(hmem)
        if not locked:
            _kernel32.GlobalFree(hmem)
            raise MemoryError("GlobalLock failed")
        try:
            ctypes.memmove(locked, dib, size)
        finally:
            _kernel32.GlobalUnlock(hmem)
        if not _user32.SetClipboardData(CF_DIB, hmem):
            _kernel32.GlobalFree(hmem)
            raise RuntimeError("SetClipboardData(CF_DIB) failed")
    finally:
        _user32.CloseClipboard()
