"""AI 陪伴：会话摘要与历史持久化（避免每次重读超长上下文）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from apps.core.crm.db import connect, init_db
from apps.core.crm.events import now_iso
from apps.core.orchestrator.companion_analysis import ChatTurn
from apps.core.runtime_paths import default_sqlite_db_path

CompanionMode = str  # light_fix | deep_check | optimization

MODES = ("light_fix", "deep_check", "optimization")


@dataclass(slots=True)
class CompanionSessionRow:
    mode: str
    summary_md: str
    history_json: str
    updated_at: str


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS companion_chat_sessions (
          mode TEXT PRIMARY KEY,
          summary_md TEXT NOT NULL DEFAULT '',
          history_json TEXT NOT NULL DEFAULT '[]',
          updated_at TEXT NOT NULL
        )
        """
    )


def load_session(mode: str, db_path=None) -> CompanionSessionRow | None:
    p = db_path or default_sqlite_db_path()
    conn = connect(p)
    init_db(conn)
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT mode, summary_md, history_json, updated_at "
            "FROM companion_chat_sessions WHERE mode = ? LIMIT 1",
            (mode,),
        ).fetchone()
        if not row:
            return None
        return CompanionSessionRow(
            mode=str(row[0]),
            summary_md=str(row[1] or ""),
            history_json=str(row[2] or "[]"),
            updated_at=str(row[3] or ""),
        )
    finally:
        conn.close()


def history_from_json(raw: str) -> list[ChatTurn]:
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    out: list[ChatTurn] = []
    if not isinstance(data, list):
        return out
    for item in data:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "")
        if role in ("user", "assistant"):
            out.append(ChatTurn(role=role, content=content))
    return out


def history_to_json(turns: list[ChatTurn]) -> str:
    payload = [{"role": t.role, "content": t.content} for t in turns]
    return json.dumps(payload, ensure_ascii=False)


def save_session(
    mode: str,
    *,
    summary_md: str | None = None,
    history: list[ChatTurn] | None = None,
    db_path=None,
) -> None:
    p = db_path or default_sqlite_db_path()
    conn = connect(p)
    init_db(conn)
    try:
        _ensure_table(conn)
        prev = load_session(mode, db_path=p)
        sm = summary_md if summary_md is not None else (prev.summary_md if prev else "")
        hj = history_to_json(history) if history is not None else (
            prev.history_json if prev else "[]"
        )
        conn.execute(
            """
            INSERT INTO companion_chat_sessions(mode, summary_md, history_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(mode) DO UPDATE SET
              summary_md = excluded.summary_md,
              history_json = excluded.history_json,
              updated_at = excluded.updated_at
            """,
            (mode, sm, hj, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        from apps.core.orchestrator.companion_storage import rebuild_ai_retrieval_context

        if summary_md is not None and str(summary_md).strip():
            rebuild_ai_retrieval_context()
    except Exception:
        pass
