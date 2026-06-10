"""
千牛「本地 DB 消息源」一键配置向导（全程中文，按回车即可）。

用法：在本程序文件夹打开命令行，执行：
  python scripts/setup_db_listener_wizard.py

做完后：回到工作台 → 勾选「本地 DB 消息源」→ 点「启动全自动」。
"""
from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

# 保证能 import apps.*
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml  # noqa: E402

from scripts.db_schema_probe import probe_schema  # noqa: E402


def _pause(msg: str = "按 回车 继续…") -> None:
    try:
        input(msg)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        raise SystemExit(0) from None


def _scan_db_files() -> dict[str, float]:
    roots = [
        os.path.expandvars(r"%APPDATA%\AliWorkbench"),
        os.path.expandvars(r"%LOCALAPPDATA%\AliWorkbench"),
        r"D:\AliWorkbenchData",
        r"C:\AliWorkbenchData",
    ]
    out: dict[str, float] = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for f in glob.glob(os.path.join(root, "**", "*.db"), recursive=True):
            try:
                out[f] = os.path.getmtime(f)
            except OSError:
                pass
    return out


def _find_changed_db(before: dict[str, float], after: dict[str, float]) -> str | None:
    changed: list[tuple[str, float]] = []
    for f, t0 in before.items():
        t1 = after.get(f)
        if t1 is not None and t1 != t0:
            changed.append((f, t1 - t0))
    if not changed:
        return None
    changed.sort(key=lambda x: -x[1])
    return changed[0][0]


def _pick_best_table(db_path: str) -> dict | None:
    """复用 probe_schema 逻辑，返回最佳候选或 None。"""
    import shutil
    import sqlite3
    import tempfile

    from scripts.db_schema_probe import (
        _BUYER_KEYS,
        _CONTENT_KEYS,
        _DIRECTION_KEYS,
        _TIME_KEYS,
        _guess_col,
    )

    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(db_path, tmp)
    try:
        conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        cur = conn.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        best: dict | None = None
        for table in tables:
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
            content_col = _guess_col(cols, _CONTENT_KEYS)
            time_col = _guess_col(cols, _TIME_KEYS)
            if not content_col or not time_col:
                continue
            id_col = _guess_col(cols, frozenset({"id", "msg_id", "message_id"})) or cols[0]
            row = cur.execute(
                f"SELECT typeof({content_col}), {content_col} FROM {table} LIMIT 1"
            ).fetchone()
            if not row:
                continue
            typ, val = row[0], row[1]
            if typ == "blob" or (val is not None and not isinstance(val, str)):
                continue
            if isinstance(val, str) and not val.strip():
                continue
            cand = {
                "table": table,
                "id": id_col,
                "content": content_col,
                "time": time_col,
                "buyer": _guess_col(cols, _BUYER_KEYS),
                "direction": _guess_col(cols, _DIRECTION_KEYS),
            }
            best = cand
            break
        conn.close()
        return best
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _write_yaml(db_path: str, table_info: dict) -> Path:
    from apps.core.runtime_paths import configs_dir

    cfg_path = configs_dir() / "query_rewrite.yaml"
    if cfg_path.is_file():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    dl = raw.setdefault("db_listener", {})
    if not isinstance(dl, dict):
        dl = {}
        raw["db_listener"] = dl
    dl["poll_interval_seconds"] = 1.0
    dl["db_path"] = db_path.replace("\\", "/")
    dl["table"] = table_info["table"]
    cm: dict[str, str] = {
        "id": table_info["id"],
        "content": table_info["content"],
        "time": table_info["time"],
    }
    if table_info.get("buyer"):
        cm["buyer"] = table_info["buyer"]
    if table_info.get("direction"):
        cm["direction"] = table_info["direction"]
    dl["col_map"] = cm
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(
        yaml.dump(raw, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return cfg_path


def main() -> int:
    print()
    print("=" * 56)
    print("  千牛「读本地聊天记录」一键配置向导")
    print("=" * 56)
    print()
    print("只需做一件事：下面让你按回车前，")
    print("请用另一个账号（或手机）给本店千牛发一条测试消息，")
    print("例如发「你好」或随便点一个商品卡片。")
    print()
    print("前提：千牛客服工作台已登录、窗口开着。")
    print()
    _pause("发完测试消息后，按 回车 开始检测…")

    print("\n正在扫描千牛数据文件（约 3 秒）…")
    before = _scan_db_files()
    time.sleep(3)
    after = _scan_db_files()

    db_path = _find_changed_db(before, after)
    if not db_path:
        print()
        print("【没检测到变化】")
        print("  可能买家消息还没进来，或千牛把数据写在别的位置。")
        print("  请再发一条消息，重新运行本向导。")
        print("  若多次失败：请继续用默认的「截图识别」，不必强求 DB 模式。")
        print()
        return 1

    print()
    print("【找到了可能是聊天记录的文件】")
    print(f"  {db_path}")
    print()
    print("正在分析表结构…")

    code = probe_schema(db_path)
    if code == 3:
        print()
        print("【结论】聊天内容像是加密的，本机读不了明文。")
        print("  请不要勾选「本地 DB 消息源」，继续用原来的自动接待即可。")
        return 3

    table_info = _pick_best_table(db_path)
    if not table_info:
        print()
        print("【结论】没能自动认出聊天表，请把上面完整输出截图发给技术支持。")
        return 2

    print()
    print("【检测成功】程序已认出聊天表，可以写入配置。")
    print(f"  表名：{table_info['table']}")
    print()
    ans = input("是否自动写入配置并保存？(直接回车=是，输入 n=否): ").strip().lower()
    if ans in ("n", "no", "否"):
        print()
        print("未自动保存。请按上面「可复制到 yaml」那段自己粘贴到：")
        print(f"  {_ROOT / 'configs' / 'query_rewrite.yaml'}")
        print()
        return 0

    saved = _write_yaml(db_path, table_info)
    print()
    print("【已保存】")
    print(f"  {saved}")
    print()
    print("接下来请您：")
    print("  1. 打开 AI 工作台")
    print("  2. 勾选「本地 DB 消息源（实验）」")
    print("  3. 点「启动全自动客服系统」")
    print("  4. 看下方日志是否出现「已从千牛聊天记录库读取新消息」")
    print()
    print("若没有：取消勾选 DB，仍用截图识别，不影响正常接待。")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
