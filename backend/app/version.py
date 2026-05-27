"""运行版本信息 — 用于核对「当前跑的代码」是哪个 commit, 是否和最新对齐.

来源优先级:
  1) 环境变量 GIT_COMMIT 等 (docker build --build-arg 烤进镜像, 最可靠)
  2) build_version.json (部署时由看门狗写入)
  3) 运行时 git 命令 (本地开发用)
  4) 未知 (兜底)

容器内没有 .git, 所以必须在「构建/部署时」由宿主机 (看门狗) 把 git 信息注入进来。
看门狗在每次 docker compose build 前: 既设置 --build-arg 环境变量, 也写 build_version.json,
双保险。
"""
from __future__ import annotations

import json
import os
import subprocess
from functools import lru_cache
from pathlib import Path

# backend/app/version.py → backend/build_version.json
_VERSION_FILE = Path(__file__).resolve().parent.parent / "build_version.json"
# backend/app/version.py → 仓库根 (本地开发跑 git 用)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _from_env() -> dict | None:
    commit = os.environ.get("GIT_COMMIT", "").strip()
    if not commit or commit == "unknown":
        return None
    return {
        "commit": commit[:7],
        "commit_full": commit,
        "commit_date": os.environ.get("GIT_COMMIT_DATE", "").strip(),
        "commit_message": os.environ.get("GIT_COMMIT_MSG", "").strip(),
        "branch": os.environ.get("GIT_BRANCH", "").strip(),
        "deployed_at": os.environ.get("BUILD_TIME", "").strip(),
        "source": "build_env",
    }


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
    """返回当前运行版本信息. 进程内缓存 (容器生命周期内版本不变)."""
    info = _from_env() or _from_file() or _from_git() or {
        "commit": "unknown", "commit_full": "", "commit_date": "",
        "commit_message": "", "branch": "", "deployed_at": "", "source": "unknown",
    }
    info.setdefault("commit", "unknown")
    return info
