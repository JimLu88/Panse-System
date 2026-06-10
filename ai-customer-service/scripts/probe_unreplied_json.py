#!/usr/bin/env python3
"""
探查千牛 UnReplyedConversation.json 的内部结构。

用法：
    python scripts/probe_unreplied_json.py

功能：
    1. 自动发现 D:\AliWorkbenchData\NewAppData\*#*\recept\UnReplyedConversation.json
    2. 每 0.5s 轮询 mtime，有变化时打印：
       - 完整 JSON（pretty-print）
       - 与上一份快照的 diff（新增/删除/变化的 key）
    3. 按 Ctrl+C 退出

目的：
    了解 JSON 内部结构（key/value/嵌套），以决定 file_sentinel 是否可以
    升级为"内容差集驱动"（按 buyer_id 级别去重触发）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

DATA_ROOT = Path(r"D:\AliWorkbenchData")
POLL_S = 0.5


def discover_files() -> list[Path]:
    base = DATA_ROOT / "NewAppData"
    if not base.is_dir():
        return []
    return sorted(
        p
        for p in base.glob("*#*/recept/UnReplyedConversation.json")
        if p.is_file()
    )


def safe_read(path: Path) -> dict | list | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        return json.loads(raw)
    except Exception as e:
        print(f"  [读取失败] {path}: {e}")
        return None


def diff_snapshots(prev: object, curr: object) -> list[str]:
    """简单 diff：对比两份 JSON 对象，输出变化描述。"""
    lines: list[str] = []

    if type(prev) != type(curr):
        lines.append(f"类型变化：{type(prev).__name__} -> {type(curr).__name__}")
        return lines

    if isinstance(curr, dict) and isinstance(prev, dict):
        prev_keys = set(prev.keys())
        curr_keys = set(curr.keys())

        for k in sorted(curr_keys - prev_keys):
            val_preview = json.dumps(curr[k], ensure_ascii=False)[:200]
            lines.append(f"  [新增] {k!r}: {val_preview}")

        for k in sorted(prev_keys - curr_keys):
            lines.append(f"  [删除] {k!r}")

        for k in sorted(prev_keys & curr_keys):
            if prev[k] != curr[k]:
                old_preview = json.dumps(prev[k], ensure_ascii=False)[:120]
                new_preview = json.dumps(curr[k], ensure_ascii=False)[:120]
                lines.append(f"  [变化] {k!r}: {old_preview} -> {new_preview}")

    elif isinstance(curr, list) and isinstance(prev, list):
        if len(prev) != len(curr):
            lines.append(f"列表长度变化：{len(prev)} -> {len(curr)}")
        for i in range(min(20, max(len(prev), len(curr)))):
            old_item = prev[i] if i < len(prev) else "<不存在>"
            new_item = curr[i] if i < len(curr) else "<不存在>"
            if old_item != new_item:
                lines.append(
                    f"  [项{i}变化] "
                    f"{json.dumps(old_item, ensure_ascii=False)[:100]} -> "
                    f"{json.dumps(new_item, ensure_ascii=False)[:100]}"
                )
    else:
        if prev != curr:
            lines.append(f"值变化：{prev!r} -> {curr!r}")

    return lines


def main() -> None:
    print("=" * 60)
    print("千牛 UnReplyedConversation.json 探查工具")
    print(f"监控目录：{DATA_ROOT / 'NewAppData'}")
    print("按 Ctrl+C 退出")
    print("=" * 60)

    files = discover_files()
    if not files:
        print(f"\n⚠ 未找到任何 UnReplyedConversation.json，请确认：")
        print(f"  1. 千牛正在运行")
        print(f"  2. 数据目录为 {DATA_ROOT}")
        print(f"\n持续轮询中…")

    mtimes: dict[str, float] = {}
    snapshots: dict[str, object] = {}

    for f in files:
        key = str(f)
        try:
            mtimes[key] = f.stat().st_mtime
        except OSError:
            mtimes[key] = 0.0
        content = safe_read(f)
        snapshots[key] = content
        acct = f.parts[-3]
        print(f"\n--- 账号 {acct} 初始状态 ---")
        if content is not None:
            print(json.dumps(content, indent=2, ensure_ascii=False))
            if isinstance(content, dict):
                print(f"\n  顶层 key 数量：{len(content)}")
                for k in list(content.keys())[:5]:
                    v = content[k]
                    print(
                        f"  key={k!r}  value_type={type(v).__name__}  "
                        f"preview={json.dumps(v, ensure_ascii=False)[:200]}"
                    )
            elif isinstance(content, list):
                print(f"\n  列表长度：{len(content)}")
                for i, item in enumerate(content[:3]):
                    print(
                        f"  [{i}] type={type(item).__name__}  "
                        f"preview={json.dumps(item, ensure_ascii=False)[:200]}"
                    )
        else:
            print("  (空或读取失败)")

    print(f"\n{'=' * 60}")
    print(f"初始化完成，开始监控变化…  (轮询间隔 {POLL_S}s)")
    print(f"{'=' * 60}\n")

    try:
        while True:
            time.sleep(POLL_S)

            current_files = discover_files()
            for f in current_files:
                key = str(f)
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    continue

                prev_mtime = mtimes.get(key, 0.0)
                if mtime <= prev_mtime:
                    continue

                mtimes[key] = mtime
                content = safe_read(f)
                prev_content = snapshots.get(key)
                snapshots[key] = content

                acct = f.parts[-3]
                ts = time.strftime("%H:%M:%S")

                print(f"\n{'─' * 50}")
                print(f"[{ts}] 账号 {acct} 文件变化！")
                print(f"{'─' * 50}")

                if content is not None:
                    print(json.dumps(content, indent=2, ensure_ascii=False))

                    if isinstance(content, dict):
                        print(f"\n  顶层 key 数量：{len(content)}")
                        for k in list(content.keys())[:10]:
                            v = content[k]
                            print(f"  key={k!r}  value_type={type(v).__name__}")
                    elif isinstance(content, list):
                        print(f"\n  列表长度：{len(content)}")
                else:
                    print("  (内容为空)")

                if prev_content is not None and content is not None:
                    diffs = diff_snapshots(prev_content, content)
                    if diffs:
                        print(f"\n  与上次的差异：")
                        for d in diffs:
                            print(f"    {d}")
                    else:
                        print(f"\n  与上次内容完全相同（仅 mtime 变化）")
                elif prev_content is None and content is not None:
                    print(f"\n  首次出现内容")

    except KeyboardInterrupt:
        print("\n\n已退出。")


if __name__ == "__main__":
    main()
