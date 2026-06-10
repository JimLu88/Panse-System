"""
Dev vs PyInstaller one-file: resolve install dir, bundled resources, and writable paths.

Frozen layout after first run::
    AIWorkbench.exe
    configs/                    ← default instance (user-editable)
    data/sqlite/

多实例（三店并排）：启动加 ``--profile 店A`` 或环境变量 ``AIWORKBENCH_PROFILE``，
数据落在 ``instances/<profile>/``，与 exe 并列，互不共用数据库与 configs。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

# apps/core/runtime_paths.py → repository root in development
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def is_frozen_onefile() -> bool:
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def profile_name() -> str | None:
    """当前实例名；未设置则为默认单实例目录。"""
    raw = (os.environ.get("AIWORKBENCH_PROFILE") or "").strip()
    if not raw:
        return None
    safe = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", raw, flags=re.UNICODE).strip("_")[:64]
    return safe or None


def project_root() -> Path:
    """exe 所在目录；若带 profile 则为 exe旁 instances/<profile>/。"""
    if is_frozen_onefile():
        base = Path(sys.executable).resolve().parent
    else:
        base = _REPO_ROOT
    pn = profile_name()
    if pn:
        inst = base / "instances" / pn
        inst.mkdir(parents=True, exist_ok=True)
        return inst
    return base


def bundle_root() -> Path:
    """PyInstaller extract folder (_MEIPASS) or repo root in development."""
    if is_frozen_onefile():
        return Path(sys._MEIPASS)
    return _REPO_ROOT


def bootstrap_frozen_bundle() -> None:
    """打包首次运行：从 bundle 解压 configs；开发环境若使用 ``--profile`` 则从仓库模板复制一份到实例目录。"""
    dst = project_root() / "configs"
    if dst.is_dir() and any(dst.iterdir()):
        return
    if is_frozen_onefile():
        src = bundle_root() / "configs"
    elif profile_name():
        src = _REPO_ROOT / "configs"
    else:
        return
    if src.is_dir():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)


def configs_dir() -> Path:
    return project_root() / "configs"


def default_sqlite_db_path() -> Path:
    return project_root() / "data" / "sqlite" / "app.db"


def default_few_shot_path() -> Path:
    return configs_dir() / "few_shot" / "default.txt"


def default_panse_customer_chat_log_csv() -> Path:
    """全量客户对话 CSV（运营复盘用）；与实例目录一致，多 profile 互不混写。"""
    d = project_root() / "data" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "panse_customer_chats_log.csv"


def default_panse_embedding_finetune_jsonl() -> Path:
    """畔色专属 Embedding 微调语料（JSONL）；按实例目录隔离。"""
    d = project_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / "panse_embedding_finetune_data.jsonl"


def image_library_root() -> Path:
    """HITL 图库根目录：images/products 与 images/tutorials。"""
    root = project_root() / "images"
    (root / "products").mkdir(parents=True, exist_ok=True)
    (root / "tutorials").mkdir(parents=True, exist_ok=True)
    return root


def image_library_products_dir() -> Path:
    return image_library_root() / "products"


def image_library_tutorials_dir() -> Path:
    return image_library_root() / "tutorials"


def brand_shop_image_kb_root(brand_id: str, shop_id: str) -> Path:
    """按品牌/店铺隔离的产品实拍图目录（可再分子文件夹：类目/品名）。"""
    safe_brand = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", (brand_id or "default").strip(), flags=re.UNICODE).strip("_")[
        :80
    ]
    safe_shop = re.sub(r"[^\w\-\u4e00-\u9fff]", "_", (shop_id or "shop").strip(), flags=re.UNICODE).strip("_")[
        :120
    ]
    root = project_root() / "data" / "image_kb" / safe_brand / safe_shop
    root.mkdir(parents=True, exist_ok=True)
    return root


def default_shadow_actions_jsonl() -> Path:
    """影子模式：前台窗口切换等行为序列 JSONL。"""
    d = project_root() / "data" / "logs" / "shadow"
    d.mkdir(parents=True, exist_ok=True)
    return d / "human_action_sequence.jsonl"


def default_shadow_security_log() -> Path:
    d = project_root() / "data" / "logs" / "shadow"
    d.mkdir(parents=True, exist_ok=True)
    return d / "shadow_security.log"


def ui_prefs_path() -> Path:
    """UI 用户偏好（上次选择的店铺等），JSON 格式，与实例目录绑定。"""
    d = project_root() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d / "ui_prefs.json"


def default_evolution_rules_path() -> Path:
    """可合并写入的演化规则（JSON）；与 configs 同实例目录。"""
    p = configs_dir() / "shadow"
    p.mkdir(parents=True, exist_ok=True)
    return p / "evolution_rules.json"


# ---------------------------------------------------------------------------
# 手机接待 IPC 路径
# ---------------------------------------------------------------------------

def mobile_state_dir() -> Path:
    """手机接待 IPC 状态目录（overview / devices / recent_msgs JSON 及 control_signal）。
    不自动 mkdir，写入方负责创建。
    """
    return project_root() / "data" / "mobile_state"


def shadow_replies_path() -> Path:
    """影子模式拦截回复记录（JSONL）；mobile_state_dir() 的子文件。"""
    return mobile_state_dir() / "shadow_replies.jsonl"
