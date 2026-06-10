"""
PyInstaller onefile：tiktoken 用 pkgutil.iter_modules 扫描 tiktoken_ext，frozen 下常得到
「Plugins found: []」，导致 Unknown encoding cl100k_base。在任意 tiktoken.get_encoding
之前把 openai_public 里的编码表并入 registry。
"""

from __future__ import annotations

try:
    import tiktoken.registry as _reg
    import tiktoken_ext.openai_public as _pub

    with _reg._lock:
        if _reg.ENCODING_CONSTRUCTORS is None:
            _reg.ENCODING_CONSTRUCTORS = dict(_pub.ENCODING_CONSTRUCTORS)
except Exception:
    pass
