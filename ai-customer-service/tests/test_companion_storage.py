"""AI 陪伴存储与全仓扫描。"""

from pathlib import Path

from apps.core.orchestrator.companion_analysis import ChatTurn
from apps.core.orchestrator.companion_storage import (
    ai_retrieval_path,
    append_conversation_turn,
    archive_full_session,
    companion_data_dir,
    load_ai_retrieval_context,
    rebuild_ai_retrieval_context,
    scan_full_repository,
    template_condense_session,
)


def test_companion_dirs_created():
    d = companion_data_dir()
    assert d.is_dir()
    assert (d / "archive").is_dir()


def test_append_and_ai_retrieval(tmp_path, monkeypatch):
    import apps.core.orchestrator.companion_storage as st

    root = tmp_path / "inst"
    root.mkdir()
    monkeypatch.setattr(st, "project_root", lambda: root)

    append_conversation_turn(
        mode="light_fix",
        session_id="abc",
        role="user",
        content="测试消息",
    )
    assert st.conversation_log_path().is_file()

    condensed = template_condense_session(
        mode="light_fix",
        session_id="abc",
        history=[ChatTurn("user", "hello")],
    )
    archive_full_session(
        mode="light_fix",
        session_id="abc",
        history=[ChatTurn("user", "hello")],
        condensed_md=condensed,
    )
    assert ai_retrieval_path().is_file()
    assert "hello" in load_ai_retrieval_context()


def test_full_repo_scan_finds_apps():
    from apps.core.orchestrator.companion_storage import load_deep_scan_settings

    cfg = load_deep_scan_settings()
    result = scan_full_repository(settings=cfg)
    assert result.file_count >= 10
    assert result.full_scan_path.is_file()
    assert len(result.llm_excerpt) <= cfg.llm_excerpt_max_chars + 500
    assert "def " in result.llm_excerpt or "class " in result.llm_excerpt
