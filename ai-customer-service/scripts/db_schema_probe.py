"""
第二步探测：查看千牛 .db 里有哪些表、哪张表像「聊天记录」。

为什么要跑这个？
  db_discovery.py 只告诉你「哪个文件会变」；
  本脚本告诉你「该读哪张表、字段叫什么」，才能填进 configs/query_rewrite.yaml。

用法（路径换成上一步输出的 .db 完整路径）：
  python scripts/db_schema_probe.py "D:\\AliWorkbenchData\\xxx\\chat.db"
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile


_CONTENT_KEYS = frozenset({"content", "msg", "text", "message", "body"})
_TIME_KEYS = frozenset({"time", "ts", "timestamp", "create_time", "created_at", "send_time"})
_BUYER_KEYS = frozenset({"buyer", "sender_id", "sender", "from_id", "user_id", "nick"})
_DIRECTION_KEYS = frozenset({"direction", "sender_type", "msg_type", "role", "type"})


def _guess_col(cols: list[str], candidates: frozenset[str]) -> str | None:
    lower = {c.lower(): c for c in cols}
    for key in candidates:
        if key in lower:
            return lower[key]
    return None


def probe_schema(db_path: str) -> int:
    db_path = os.path.abspath(db_path.strip().strip('"'))
    if not os.path.isfile(db_path):
        print(f"\n找不到文件：{db_path}")
        print("请检查路径是否抄错，或先运行：python scripts/db_discovery.py\n")
        return 1

    tmp = tempfile.mktemp(suffix=".db")
    shutil.copy2(db_path, tmp)
    print("=" * 60)
    print("千牛数据库结构探测（只读复制，不会改千牛原文件）")
    print("=" * 60)
    print(f"\n正在分析：{db_path}\n")

    try:
        conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        cur = conn.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        print(f"共 {len(tables)} 张表：{', '.join(tables) if tables else '（无）'}\n")

        candidates: list[dict] = []
        for table in tables:
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
            if not cols:
                continue
            content_col = _guess_col(cols, _CONTENT_KEYS)
            time_col = _guess_col(cols, _TIME_KEYS)
            if not content_col or not time_col:
                continue

            id_col = _guess_col(cols, frozenset({"id", "msg_id", "message_id"})) or cols[0]
            buyer_col = _guess_col(cols, _BUYER_KEYS)
            direction_col = _guess_col(cols, _DIRECTION_KEYS)

            samples: list[tuple[str, str]] = []
            try:
                rows = cur.execute(
                    f"SELECT typeof({content_col}), {content_col} FROM {table} LIMIT 5"
                ).fetchall()
                for typ, val in rows:
                    if isinstance(val, str) and val.strip():
                        preview = val.strip().replace("\n", " ")[:60]
                        samples.append((typ, preview))
                    elif val is not None:
                        samples.append((typ, f"<{typ} 二进制，长度 {len(val) if hasattr(val, '__len__') else '?'}>"))
            except sqlite3.Error as e:
                samples.append(("error", str(e)))

            blob_only = samples and all(
                t in ("blob", "error") or p.startswith("<") for t, p in samples
            )
            candidates.append(
                {
                    "table": table,
                    "cols": cols,
                    "id": id_col,
                    "content": content_col,
                    "time": time_col,
                    "buyer": buyer_col,
                    "direction": direction_col,
                    "samples": samples,
                    "blob_only": blob_only,
                }
            )

        if not candidates:
            print(
                "未找到「既有文字内容又有时间」的表。\n"
                "可能：千牛把聊天内容加密成二进制了，本地 DB 监听方案用不了，请继续用截图识别。\n"
            )
            return 2

        print("【最像聊天记录的表】（按匹配程度列出）\n")
        for i, c in enumerate(candidates, 1):
            print(f"--- 候选 {i}：表名 {c['table']} ---")
            print(f"  全部字段：{', '.join(c['cols'])}")
            print(f"  建议映射：")
            print(f"    消息编号 → {c['id']}")
            print(f"    说话内容 → {c['content']}")
            print(f"    发送时间 → {c['time']}")
            if c["buyer"]:
                print(f"    买家标识 → {c['buyer']}（可选）")
            if c["direction"]:
                print(f"    消息方向 → {c['direction']}（0 或 buyer 表示买家，需实测）")
            print("  内容样例：")
            for typ, prev in c["samples"][:3]:
                print(f"    · 类型={typ}  预览={prev}")
            if c["blob_only"]:
                print(
                    "  ⚠ 样例全是二进制/加密：此表可能无法直接读文字，"
                    "DB 监听多半不可用，请改用截图识别。"
                )
            print()

        best = next((c for c in candidates if not c["blob_only"]), candidates[0])
        print("=" * 60)
        print("【可复制到 configs/query_rewrite.yaml 的示例】")
        print("（打开该文件，找到 db_listener，按下面改路径和表名后保存）\n")
        print("db_listener:")
        print("  poll_interval_seconds: 1.0")
        print(f'  db_path: "{db_path.replace(chr(92), "/")}"')
        print(f'  table: "{best["table"]}"')
        print("  col_map:")
        print(f'    id: "{best["id"]}"')
        print(f'    content: "{best["content"]}"')
        print(f'    time: "{best["time"]}"')
        if best["buyer"]:
            print(f'    buyer: "{best["buyer"]}"')
        if best["direction"]:
            print(f'    direction: "{best["direction"]}"')
        print()
        print("填好后：回到工作台 → 勾选「本地 DB 消息源」→ 启动全自动。")
        print("若启动日志仍提示缺项，把本脚本完整输出保存发技术支持即可。")
        print("=" * 60)
        conn.close()
        return 0 if not best["blob_only"] else 3
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n请带上 .db 路径，例如：")
        print('  python scripts/db_schema_probe.py "D:\\AliWorkbenchData\\xxx\\chat.db"\n')
        sys.exit(1)
    raise SystemExit(probe_schema(sys.argv[1]))
