"""探测千牛运行时变化的 .db 文件（实施计划 3.1）。用法: python scripts/db_discovery.py"""
from __future__ import annotations

import glob
import os
import time


def find_qianniu_db() -> list[str]:
    scan_roots = [
        os.path.expandvars(r"%APPDATA%\AliWorkbench"),
        os.path.expandvars(r"%LOCALAPPDATA%\AliWorkbench"),
        r"D:\AliWorkbenchData",
        r"C:\AliWorkbenchData",
    ]
    baseline: dict[str, float] = {}
    for root in scan_roots:
        if not os.path.exists(root):
            continue
        for f in glob.glob(os.path.join(root, "**", "*.db"), recursive=True):
            try:
                baseline[f] = os.path.getmtime(f)
            except OSError:
                pass

    print(f"已记录 {len(baseline)} 个 .db 基线，请让买家发一条测试消息后按 Enter...")
    input()
    time.sleep(3)

    changed: list[tuple[str, float]] = []
    for f, old_mtime in baseline.items():
        try:
            new_mtime = os.path.getmtime(f)
            if new_mtime != old_mtime:
                changed.append((f, round(new_mtime - old_mtime, 2)))
        except OSError:
            pass

    changed.sort(key=lambda x: -x[1])
    print("\n【有变化的文件（越靠前越可能是聊天记录库）】")
    for f, delta in changed:
        print(f"  +{delta}s  {f}")
    if changed:
        top = changed[0][0]
        print("\n下一步：复制上面最靠前那条完整路径，运行结构探测：")
        print(f'  python scripts/db_schema_probe.py "{top}"')
        print("按脚本末尾提示，把内容填进 configs/query_rewrite.yaml 的 db_listener。\n")
    else:
        print(
            "\n没有检测到文件变化。请确认千牛已登录、买家确实发了消息，再试一次。\n"
        )
    return [f for f, _ in changed]


if __name__ == "__main__":
    find_qianniu_db()
