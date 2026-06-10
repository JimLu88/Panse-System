"""
版本号自动递增工具：
  - 每次跑 patch +1（1.5.0 → 1.5.1 → 1.5.2 ...）
  - 同步写到 apps/release_info.py 和 apps/__version__.py
  - 同步更新 BUILD_DATE 为今天
  - 输出新版本号到 stdout（_build_temp.ps1 会捕获）

用法：
  py _bump_ver.py            # 跑一次，patch +1
  py _bump_ver.py 1.6.0      # 跳到指定版本（major/minor 升级时用）
"""
import os
import re
import sys
import datetime
from pathlib import Path

# 用脚本所在目录推算项目根 → 避免迁移目录后失效
HERE = Path(__file__).resolve().parent

PATHS = [
    HERE / "apps" / "release_info.py",
    HERE / "apps" / "__version__.py",
]

primary = PATHS[0]
if not primary.is_file():
    sys.exit(f"ERROR: 找不到 {primary}")

text = primary.read_text(encoding="utf-8")
m = re.search(r'__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text)
if not m:
    sys.exit("ERROR: version not found in " + str(primary))

# 优先用命令行指定版本（如 1.6.0），否则自动 patch +1
if len(sys.argv) >= 2 and re.match(r"^\d+\.\d+\.\d+$", sys.argv[1]):
    new_ver = sys.argv[1]
else:
    new_patch = int(m.group(3)) + 1
    new_ver = f"{m.group(1)}.{m.group(2)}.{new_patch}"

today = str(datetime.date.today())

for path in PATHS:
    t = path.read_text(encoding="utf-8")
    t = re.sub(r'__version__\s*=\s*"[\d.]+"', f'__version__ = "{new_ver}"', t)
    t = re.sub(r'BUILD_DATE\s*=\s*"[\d-]+"', f'BUILD_DATE = "{today}"', t)
    path.write_text(t, encoding="utf-8", newline="\n")

# 输出新版本号给上游脚本捕获
print(new_ver)
