"""Sum PyInstaller Analysis-00.toc BINARY/DATA sizes."""
from __future__ import annotations

import ast
import re
from pathlib import Path

toc_path = Path(__file__).resolve().parents[1] / "build" / "AIWorkbench" / "Analysis-00.toc"
if not toc_path.is_file():
    print("No toc"); raise SystemExit(1)

text = toc_path.read_text(encoding="utf-8", errors="replace")
# Find binaries section: last big list before datas
idx = text.find("'BINARY')]")
if idx < 0:
    print("no binaries"); raise SystemExit(1)

# Parse tuples (dest, src, type) with ast
entries: list[tuple[str, str, str]] = []
for m in re.finditer(r"\('((?:[^'\\]|\\.)*)',\s*'((?:[^'\\]|\\.)*)',\s*'(BINARY|DATA|EXTENSION)'\)", text):
    dest = m.group(1).encode().decode("unicode_escape") if "\\" in m.group(1) else m.group(1)
    src = m.group(2).encode().decode("unicode_escape") if "\\" in m.group(2) else m.group(2)
    entries.append((dest, src, m.group(3)))

by_kind: dict[str, int] = {}
rows: list[tuple[int, str, str, str]] = []
for dest, src, kind in entries:
    try:
        sz = Path(src).stat().st_size if Path(src).is_file() else 0
    except OSError:
        sz = 0
    by_kind[kind] = by_kind.get(kind, 0) + sz
    rows.append((sz, kind, dest, src))

rows.sort(reverse=True)
print("Totals MB:", {k: round(v/1024/1024, 1) for k, v in by_kind.items()})
print("\nTop 25:")
for sz, kind, dest, src in rows[:25]:
    print(f"{sz/1024/1024:8.1f} MB [{kind}] {dest[:70]}")

# Check if any source is under dist/
dist_hits = [(sz, src) for sz, _, _, src in rows if "\\dist\\" in src.lower() or "/dist/" in src.lower()]
print(f"\ndist/* hits: {len(dist_hits)}", sum(s//1024//1024 for s,_ in dist_hits), "MB total")
for sz, src in dist_hits[:10]:
    print(f"  {sz/1024/1024:.1f} MB {src}")
