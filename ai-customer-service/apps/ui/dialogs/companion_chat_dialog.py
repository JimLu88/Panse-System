"""AI 陪伴：交互式对话（问候 → 用户描述 → 修复/检查/优化，多轮对话 + 持久归档）。"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from apps.core.orchestrator.companion_analysis import (
    ChatTurn,
    CompanionEvidence,
    CompanionMode,
    generate_companion_plan,
    generate_companion_reply,
    generate_deep_check_report,
    generate_optimization_report,
    generate_session_summary,
    gather_companion_evidence,
    greeting_for_mode,
)
from apps.core.orchestrator.companion_session import load_session, save_session
from apps.core.orchestrator.companion_storage import (
    ai_retrieval_path,
    append_conversation_turn,
    archive_full_session,
    load_ai_retrieval_context,
    new_session_id,
    rebuild_ai_retrieval_context,
    template_condense_session,
)
from apps.core.runtime_paths import default_sqlite_db_path

_MODE_TITLES: dict[CompanionMode, str] = {
    "light_fix": "轻度问题修复",
    "deep_check": "深度检查",
    "optimization": "功能优化",
}


class CompanionChatDialog(QDialog):
    """立即问候；全量对话写入 JSONL，精简记忆写入 ai_retrieval_context.md。"""

    def __init__(
        self,
        parent,
        *,
        mode: CompanionMode = "light_fix",
        pool: ThreadPoolExecutor,
        console_excerpt: str = "",
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode: CompanionMode = mode
        self._pool = pool
        self._on_log = on_log or (lambda _m: None)
        self._db_path = default_sqlite_db_path()
        self._console_excerpt = (console_excerpt or "")[-14000:]
        self._session_id = new_session_id()
        self._history: list[ChatTurn] = []
        self._evidence: CompanionEvidence | None = None
        self._session_summary: str = ""
        self._ai_memory: str = load_ai_retrieval_context()
        self._plan_text: str = ""
        self._condensed_saved = False
        self._bg_future: Future | None = None
        self._reply_future: Future | None = None
        self._plan_future: Future | None = None
        self._summary_future: Future | None = None

        saved = load_session(mode, self._db_path)
        if saved and saved.summary_md.strip():
            self._session_summary = saved.summary_md.strip()

        title = _MODE_TITLES.get(mode, "运行诊断")
        self.setWindowTitle(f"AI 陪伴 — {title}")
        self.setMinimumSize(760, 560)
        self.resize(820, 620)
        self._apply_dark_theme()

        lay = QVBoxLayout(self)
        mem_hint = ""
        if self._ai_memory.strip():
            mem_hint = f" 已加载精简记忆库（{ai_retrieval_path().name}）。"
        self._hint = QLabel(self._hint_text() + mem_hint)
        self._hint.setWordWrap(True)
        self._hint.setObjectName("companionHint")
        lay.addWidget(self._hint)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setFont(QFont("Segoe UI", 10))
        self._browser.setObjectName("companionBrowser")
        lay.addWidget(self._browser, stretch=1)

        row = QHBoxLayout()
        self._btn_summary = QPushButton("生成简短摘要")
        self._btn_summary.setToolTip(
            "用 AI 压缩本场对话，写入 data/companion/condensed/ 并更新 ai_retrieval_context.md"
        )
        self._btn_summary.clicked.connect(self._on_generate_summary)
        row.addWidget(self._btn_summary)
        self._btn_plan = QPushButton("生成完整 Plan")
        self._btn_plan.clicked.connect(self._on_generate_plan)
        row.addWidget(self._btn_plan)
        self._btn_copy_plan = QPushButton("复制 Plan")
        self._btn_copy_plan.setEnabled(False)
        self._btn_copy_plan.clicked.connect(self._on_copy_plan)
        row.addWidget(self._btn_copy_plan)
        self._btn_export_plan = QPushButton("导出 Plan")
        self._btn_export_plan.setEnabled(False)
        self._btn_export_plan.clicked.connect(self._on_export_plan)
        row.addWidget(self._btn_export_plan)
        row.addStretch(1)
        self._status = QLabel("")
        row.addWidget(self._status)
        lay.addLayout(row)

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText(
            "描述你遇到的问题（Enter 发送，Shift+Enter 换行）…"
        )
        self._input.setMaximumHeight(88)
        self._input.setObjectName("companionInput")
        lay.addWidget(self._input)

        send_row = QHBoxLayout()
        self._btn_send = QPushButton("发送")
        self._btn_send.setDefault(True)
        self._btn_send.clicked.connect(self._on_send)
        send_row.addWidget(self._btn_send)
        send_row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        send_row.addWidget(bb)
        lay.addLayout(send_row)

        self._poll = QTimer(self)
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._poll_futures)

        if self._ai_memory.strip():
            self._append_message(
                "assistant",
                "📚 **历史精简记忆**（来自 ai_retrieval_context.md，每次对话自动加载）：\n\n"
                f"{self._ai_memory[:6000]}"
                + ("…" if len(self._ai_memory) > 6000 else ""),
            )
        greeting = greeting_for_mode(mode)
        self._record_turn("assistant", greeting)
        self._append_message("assistant", greeting)

        self._poll.start()
        self._start_background_work()

    def _hint_text(self) -> str:
        base = _MODE_TITLES.get(self._mode, "对话")
        extra = {
            "light_fix": "请先描述现象；每轮对话永久保存到 conversation_full.jsonl。",
            "deep_check": "后台全仓扫描代码 + 全量日志，结果在 data/companion/repo_scan/。",
            "optimization": "后台分析运行数据，可结合历史精简记忆讨论优化。",
        }.get(self._mode, "")
        return f"【{base}】{extra}"

    def _record_turn(self, role: str, content: str) -> None:
        self._history.append(ChatTurn(role, content))
        try:
            append_conversation_turn(
                mode=self._mode,
                session_id=self._session_id,
                role=role,
                content=content,
            )
        except Exception:
            pass

    def _set_busy(self, busy: bool) -> None:
        self._input.setEnabled(not busy)
        self._btn_send.setEnabled(not busy)
        self._btn_summary.setEnabled(not busy and len(self._history) > 1)
        can_plan = not busy and self._evidence is not None and len(self._history) > 1
        self._btn_plan.setEnabled(can_plan)
        self._status.setText("思考中…" if busy else "")

    def _apply_dark_theme(self) -> None:
        self.setStyleSheet(
            """
            QDialog { background-color: #1e1e1e; color: #e8e8e8; }
            QLabel#companionHint { color: #b0b0b0; }
            QTextBrowser#companionBrowser {
                background-color: #252526;
                color: #e8e8e8;
                border: 1px solid #3c3c3c;
                border-radius: 6px;
                padding: 8px;
            }
            QPlainTextEdit#companionInput {
                background-color: #2d2d2d;
                color: #f0f0f0;
                border: 1px solid #3c3c3c;
                border-radius: 4px;
                padding: 6px;
            }
            QPushButton {
                background-color: #3a3a3a;
                color: #f0f0f0;
                border: 1px solid #555;
                border-radius: 4px;
                padding: 6px 12px;
            }
            QPushButton:hover { background-color: #4a4a4a; }
            QPushButton:disabled { color: #888; background-color: #2a2a2a; }
            QLabel { color: #c8c8c8; }
            """
        )

    def _append_message(self, role: str, markdown: str) -> None:
        who = "你" if role == "user" else "AI 陪伴"
        if role == "user":
            bg, fg, border = "#1e3a5f", "#e8f4fc", "#2d5a87"
        else:
            bg, fg, border = "#2d2d30", "#e8e8e8", "#454545"
        block = (
            f"<div style='margin:10px 0;padding:10px 12px;background:{bg};"
            f"color:{fg};border:1px solid {border};border-radius:8px;'>"
            f"<b style='color:{fg};'>{html.escape(who)}</b><br/>"
            f"<span style='color:{fg};'>{_md_to_simple_html(markdown)}</span></div>"
        )
        self._browser.append(block)
        self._browser.moveCursor(QTextCursor.MoveOperation.End)

    def _start_background_work(self) -> None:
        mode = self._mode
        db = self._db_path
        excerpt = self._console_excerpt
        summary = self._session_summary or self._ai_memory

        def _work() -> tuple[str | None, CompanionEvidence]:
            ev = gather_companion_evidence(db, hours=72, console_excerpt=excerpt)
            if mode == "deep_check":
                text, ev = generate_deep_check_report(
                    db, console_excerpt=excerpt, session_summary=summary
                )
                return text, ev
            if mode == "optimization":
                deep_row = load_session("deep_check", db)
                prior = (deep_row.summary_md if deep_row else "")[:4000]
                text, ev = generate_optimization_report(
                    db,
                    console_excerpt=excerpt,
                    session_summary=summary,
                    prior_check_excerpt=prior,
                )
                return text, ev
            return None, ev

        if mode in ("deep_check", "optimization"):
            self._set_busy(True)
            self._append_message("assistant", "⏳ **后台分析中**（深度模式会全仓扫描代码）…")
        self._bg_future = self._pool.submit(_work)

    def _poll_futures(self) -> None:
        if self._bg_future is not None and self._bg_future.done():
            fut, self._bg_future = self._bg_future, None
            try:
                report, ev = fut.result()
            except Exception as e:
                report, ev = f"后台分析失败：`{e!r}`", None
            self._evidence = ev
            if report:
                self._record_turn("assistant", report)
                self._append_message("assistant", report)
                self._on_log(f"AI 陪伴：{self._mode} 报告已生成。")
            else:
                n = ev.total_logs if ev else 0
                self._hint.setText(f"已加载 {n} 条健康日志。请描述你遇到的问题。")
            self._persist_history()
            self._set_busy(False)

        if self._reply_future is not None and self._reply_future.done():
            fut, self._reply_future = self._reply_future, None
            try:
                text = fut.result()
            except Exception as e:
                text = f"回复失败：`{e!r}`"
            self._record_turn("assistant", text)
            self._append_message("assistant", text)
            self._persist_history()
            self._set_busy(False)

        if self._plan_future is not None and self._plan_future.done():
            fut, self._plan_future = self._plan_future, None
            try:
                plan = fut.result()
            except Exception as e:
                plan = f"生成失败：`{e!r}`"
            self._plan_text = plan
            self._record_turn("assistant", plan)
            self._append_message("assistant", plan)
            self._btn_copy_plan.setEnabled(True)
            self._btn_export_plan.setEnabled(True)
            self._persist_history()
            self._set_busy(False)
            self._on_log("AI 陪伴：完整 Plan 已生成。")

        if self._summary_future is not None and self._summary_future.done():
            fut, self._summary_future = self._summary_future, None
            try:
                sm = fut.result()
            except Exception as e:
                sm = f"摘要失败：`{e!r}`"
            self._apply_condensed_summary(sm)

    def _apply_condensed_summary(self, sm: str) -> None:
        self._session_summary = sm
        self._condensed_saved = True
        save_session(self._mode, summary_md=sm, history=self._history, db_path=self._db_path)
        try:
            archive_full_session(
                mode=self._mode,
                session_id=self._session_id,
                history=self._history,
                condensed_md=sm,
            )
        except Exception:
            rebuild_ai_retrieval_context()
        self._ai_memory = load_ai_retrieval_context()
        self._append_message(
            "assistant",
            f"📋 **会话摘要已保存** → `data/companion/ai_retrieval_context.md`\n\n{sm}",
        )
        self._status.setText("摘要已保存")
        self._set_busy(False)
        self._on_log("AI 陪伴：简短摘要已写入精简记忆库。")

    def _persist_history(self) -> None:
        try:
            save_session(
                self._mode,
                summary_md=self._session_summary,
                history=self._history,
                db_path=self._db_path,
            )
        except Exception:
            pass

    def _finalize_session_on_close(self) -> None:
        if len(self._history) <= 1:
            return
        if not self._condensed_saved:
            sm = template_condense_session(
                mode=self._mode,
                session_id=self._session_id,
                history=self._history,
            )
            try:
                archive_full_session(
                    mode=self._mode,
                    session_id=self._session_id,
                    history=self._history,
                    condensed_md=sm,
                )
                self._session_summary = sm
                save_session(
                    self._mode,
                    summary_md=sm,
                    history=self._history,
                    db_path=self._db_path,
                )
            except Exception:
                pass
        self._persist_history()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
                return
            if self._btn_send.isEnabled():
                self._on_send()
                return
        super().keyPressEvent(event)

    def _ensure_evidence(self) -> None:
        if self._evidence is None:
            self._evidence = gather_companion_evidence(
                self._db_path, hours=72, console_excerpt=self._console_excerpt
            )

    def _on_send(self) -> None:
        msg = self._input.toPlainText().strip()
        if not msg:
            return
        self._ensure_evidence()
        self._input.clear()
        self._record_turn("user", msg)
        self._append_message("user", msg)
        self._set_busy(True)
        ev = self._evidence
        hist = list(self._history[:-1])
        mode = self._mode
        memory = self._ai_memory or self._session_summary
        excerpt = self._console_excerpt

        self._reply_future = self._pool.submit(
            lambda: generate_companion_reply(
                mode=mode,
                history=hist,
                user_message=msg,
                evidence=ev,
                session_summary=memory,
                console_excerpt=excerpt,
            )
        )
        self._on_log(f"AI 陪伴：已提交追问（{len(msg)} 字）")

    def _on_generate_summary(self) -> None:
        if len(self._history) <= 1:
            return
        self._ensure_evidence()
        self._set_busy(True)
        mode = self._mode
        hist = list(self._history)
        ev = self._evidence
        self._summary_future = self._pool.submit(
            lambda: generate_session_summary(mode=mode, history=hist, evidence=ev)
        )
        self._on_log("AI 陪伴：正在生成简短摘要…")

    def _on_generate_plan(self) -> None:
        if self._evidence is None:
            self._ensure_evidence()
        self._record_turn("user", "请生成完整实施计划。")
        self._append_message("user", "请根据以上对话，生成 **完整实施计划**。")
        self._set_busy(True)
        ev = self._evidence
        hist = list(self._history)
        self._plan_future = self._pool.submit(
            lambda: generate_companion_plan(history=hist, evidence=ev)
        )
        self._on_log("AI 陪伴：正在生成完整 Plan…")

    def _on_copy_plan(self) -> None:
        if not self._plan_text.strip():
            QMessageBox.information(self, "无 Plan", "请先点击「生成完整 Plan」。")
            return
        QApplication.clipboard().setText(self._plan_text)
        self._btn_copy_plan.setText("已复制 ✓")

    def _on_export_plan(self) -> None:
        if not self._plan_text.strip():
            QMessageBox.information(self, "无 Plan", "请先点击「生成完整 Plan」。")
            return
        from PyQt6.QtWidgets import QFileDialog

        default_name = f"companion_plan_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出实施计划",
            default_name,
            "Markdown (*.md);;所有文件 (*.*)",
        )
        if not path:
            return
        Path(path).write_text(self._plan_text, encoding="utf-8")
        QMessageBox.information(self, "已导出", f"计划已保存至：\n{path}")

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self._finalize_session_on_close()
        super().closeEvent(event)


def _md_to_simple_html(md: str) -> str:
    s = html.escape(md or "")
    s = re.sub(r"^### (.+)$", r"<h4>\1</h4>", s, flags=re.M)
    s = re.sub(r"^## (.+)$", r"<h3>\1</h3>", s, flags=re.M)
    s = re.sub(r"^# (.+)$", r"<h2>\1</h2>", s, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s.replace("\n", "<br/>")