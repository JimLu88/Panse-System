"""运行版本信息 — 用于核对「当前跑的代码」是哪个 commit, 是否和最新对齐.

来源优先级:
  1) build_version.json (部署时由看门狗写入, 容器内 .git 不存在时唯一可靠来源)
  2) 运行时 git 命令 (本地开发用)
  3) 未知 (兜底)

build_version.json 由 deploy/windows/panse_tray.py 在每次 build 前写入,
内容含 commit 短哈希 / 完整哈希 / commit 时间 / commit 信息 / 分支 / 部署时间。
"""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

# backend/app/version.py → backend/build_version.json
_VERSION_FILE = Path(__file__).resolve().parent.parent / "build_version.json"
# backend/app/version.py → 仓库根 (本地开发跑 git 用)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _from_file() -> dict | None:
    if not _VERSION_FILE.exists():
        return None
    try:
        data = json.loads(_VERSION_FILE.read_text(encoding="utf-8"))
        data["source"] = "build_file"
        return data
    except Exception:
        return None


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(_REPO_ROOT),
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _from_git() -> dict | None:
    full = _git("rev-parse", "HEAD")
    if not full:
        return None
    return {
        "commit": full[:7],
        "commit_full": full,
        "commit_date": _git("show", "-s", "--format=%ci", "HEAD") or "",
        "commit_message": _git("show", "-s", "--format=%s", "HEAD") or "",
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "",
        "deployed_at": "",
        "source": "runtime_git",
    }


@lru_cache(maxsize=1)
def get_version() -> dict:
    """返回当前运行版本信息. 进程内缓存 (build_version.json 在容器生命周期内不变)."""
    info = _from_file() or _from_git() or {
        "commit": "unknown", "commit_full": "", "commit_date": "",
        "commit_message": "", "branch": "", "deployed_at": "", "source": "unknown",
    }
    info.setdefault("commit", "unknown")
    return info
