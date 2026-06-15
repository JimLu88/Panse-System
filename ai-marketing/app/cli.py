"""命令行入口：让 AI agent / 自动化脚本驱动核心操作。

对标 Postiz Agent CLI。用法：
  python -m app.cli topics 餐桌 3
  python -m app.cli draft <topic_id> <account_id>
  python -m app.cli scan-comments
  python -m app.cli weekly-report
  python -m app.cli seed-batch
配合 /api/agent/actions（动作清单+参数schema）可被 MCP/Claude 直接调用。
"""
from __future__ import annotations

import json
import sys

from .database import SessionLocal, init_db


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return 0
    init_db()
    cmd, args = argv[0], argv[1:]
    db = SessionLocal()
    try:
        result = _dispatch(db, cmd, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:  # CLI 友好报错
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    finally:
        db.close()


def _dispatch(db, cmd: str, args: list[str]):
    from .services import (comment_engine, content_seeder, crawl_service,
                           generator, topic_engine, weekly_report)
    if cmd == "topics":
        cat = args[0] if args else "餐桌"
        n = int(args[1]) if len(args) > 1 else 3
        return [{"id": t.id, "title": t.title} for t in topic_engine.generate_topics(db, cat, n)]
    if cmd == "draft":
        d = generator.generate_draft(db, int(args[0]), int(args[1]) if len(args) > 1 else None)
        return {"id": d.id, "title": d.title, "ai_likeness": d.ai_likeness}
    if cmd == "scan-comments":
        return {"opportunities": len(comment_engine.scan_opportunities(db))}
    if cmd == "hot-notes":
        return crawl_service.mine_hot_notes(db, args[0] if args else "")
    if cmd == "weekly-report":
        return weekly_report.build(db)
    if cmd == "seed-batch":
        return content_seeder.seed_batch(db, int(args[0]) if args else 1)
    raise ValueError(f"未知命令: {cmd}")


# Agent 动作清单（供 /api/agent/actions 暴露，agent 据此知道能调什么）
ACTIONS = [
    {"name": "topics", "desc": "生成选题", "params": {"category": "str", "count": "int"}},
    {"name": "draft", "desc": "生成草稿", "params": {"topic_id": "int", "account_id": "int?"}},
    {"name": "scan-comments", "desc": "扫描评论引流机会", "params": {}},
    {"name": "hot-notes", "desc": "抓竞品爆文", "params": {"category": "str?"}},
    {"name": "weekly-report", "desc": "生成一周战报", "params": {}},
    {"name": "seed-batch", "desc": "批量预热内容", "params": {"per_category": "int?"}},
]


if __name__ == "__main__":
    raise SystemExit(main())
