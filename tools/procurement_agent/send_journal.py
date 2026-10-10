"""Local fail-closed send guard. Not an ERP outbox or a delivery receipt.

All processes controlling the same desktop must share this absolute path.
Only identifiers, hashes and states are stored; no message text or credentials.
The bounded dispatcher stores its encrypted short-lived permit separately in
this SAME database and shares this guard/OS lock (never a second send owner).
Unresolved attempts deliberately require later reconciliation, never replay.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any


class SendBlocked(RuntimeError):
    pass


class SendJournal:
    def __init__(self, path: Path, *, server_scope: str) -> None:
        path = Path(path)
        if not path.is_absolute() or not server_scope.strip():
            raise ValueError("发送账本须使用固定绝对路径及明确 ERP 身份")
        self.path = path.resolve()
        self.scope = hashlib.sha256(server_scope.strip().rstrip("/").encode()).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS send_attempts (
                intent_key TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                task_id INTEGER NOT NULL,
                inquiry_id INTEGER NOT NULL,
                action_key TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN (
                    'executing', 'sent_observed', 'confirmed', 'manual', 'unknown'
                )),
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=2)
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA journal_mode=DELETE")
            with conn:
                yield conn
        finally:
            conn.close()

    def _identity(self, action: dict[str, Any]) -> tuple[str, int, int, str]:
        task_id, inquiry_id = action.get("task_id"), action.get("inquiry_id")
        action_key = action.get("action_key")
        if any(type(v) is not int or v <= 0 for v in (task_id, inquiry_id)):
            raise SendBlocked("任务或商家稳定标识缺失；禁止调用发送驱动")
        if not isinstance(action_key, str) or not re.fullmatch(
            r"initial:0|followup:[1-9][0-9]*", action_key
        ):
            raise SendBlocked("消息轮次标识缺失或不合法；禁止调用发送驱动")
        identity = json.dumps([self.scope, task_id, inquiry_id, action_key])
        key = hashlib.sha256(identity.encode()).hexdigest()
        return key, task_id, inquiry_id, action_key

    def begin(self, action: dict[str, Any]) -> None:
        """Commit before entering a driver that could produce external effects."""
        key, task_id, inquiry_id, action_key = self._identity(action)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM send_attempts WHERE intent_key=?", (key,)
            ).fetchone():
                raise SendBlocked("这一消息轮次已有执行记录；禁止自动重发")
            # Conservative containment: a timed-out driver may still own a UI.
            # Do not continue to another merchant until the unresolved attempt
            # and any old driver have been explicitly reconciled.
            if conn.execute(
                "SELECT 1 FROM send_attempts WHERE state != 'confirmed' LIMIT 1"
            ).fetchone():
                raise SendBlocked("本机有未决发送；先核对旧结果，禁止继续发送")
            conn.execute(
                "INSERT INTO send_attempts "
                "(intent_key, scope, task_id, inquiry_id, action_key, state) "
                "VALUES (?, ?, ?, ?, ?, 'executing')",
                (key, self.scope, task_id, inquiry_id, action_key),
            )

    def mark(self, action: dict[str, Any], state: str) -> None:
        transitions = {
            "sent_observed": ("executing",),
            "confirmed": ("sent_observed",),
            "manual": ("executing",),
            "unknown": ("executing",),
        }
        if state not in transitions:
            raise ValueError("不允许的发送账本转换")
        key = self._identity(action)[0]
        with self._connect() as conn:
            current = conn.execute(
                "SELECT state FROM send_attempts WHERE intent_key=?", (key,)
            ).fetchone()
            # Keep observed delivery evidence if only the ERP callback failed.
            if state == "unknown" and current and current[0] != "executing":
                return
            if not current or current[0] not in transitions[state]:
                raise SendBlocked("发送账本状态冲突；禁止继续发送")
            conn.execute(
                "UPDATE send_attempts SET state=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE intent_key=?", (state, key),
            )

    def state(self, action: dict[str, Any]) -> str | None:
        key = self._identity(action)[0]
        with self._connect() as conn:
            row = conn.execute(
                "SELECT state FROM send_attempts WHERE intent_key=?", (key,)
            ).fetchone()
        return row[0] if row else None

    def require_clear_desktop(self) -> None:
        with self._connect() as conn:
            pending = conn.execute(
                "SELECT 1 FROM send_attempts WHERE state != 'confirmed' LIMIT 1"
            ).fetchone()
        if pending:
            raise SendBlocked("本机有未决发送；暂停搜索与会话切换，等待核对")

    def require_only(self, action: dict[str, Any]) -> None:
        """Recovery may inspect its own intent, but cannot cross another owner."""
        key = self._identity(action)[0]
        with self._connect() as conn:
            pending = conn.execute(
                "SELECT 1 FROM send_attempts WHERE intent_key != ? "
                "AND state != 'confirmed' LIMIT 1", (key,),
            ).fetchone()
        if pending:
            raise SendBlocked("存在其他未决发送，禁止切换界面进行恢复")

    @contextmanager
    def execution_lock(self):
        """OS-released lock covering discovery, inbox switching and sending.

        This is a local shared-path guard, not a distributed account lease.
        A crash releases the OS lock, but the durable unresolved row still blocks
        subsequent sends. Never delete/rotate the database to bypass it.
        """
        with self.path.with_suffix(self.path.suffix + ".lock").open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise SendBlocked("另一执行器正在使用采购界面") from exc
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise SendBlocked("另一执行器正在使用采购界面") from exc
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
