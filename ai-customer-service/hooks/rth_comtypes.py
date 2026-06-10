# PyInstaller runtime hook: comtypes frozen-exe fix.
# comtypes tries to write generated COM wrapper cache files to
# <_MEIPASS>/comtypes/gen/ which is read-only in a onefile build.
# Redirect the cache to a writable temp directory so pycaw / WASAPI work.
import os
import sys
import tempfile

if getattr(sys, "frozen", False):
    _gen_dir = os.path.join(tempfile.gettempdir(), "comtypes_gen_aiworkbench")
    os.makedirs(_gen_dir, exist_ok=True)
    try:
        import comtypes.gen
        comtypes.gen.__path__ = [_gen_dir]
    except Exception:
        pass
