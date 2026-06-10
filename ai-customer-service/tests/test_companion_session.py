"""AI 陪伴会话持久化。"""

from apps.core.orchestrator.companion_analysis import ChatTurn, greeting_for_mode
from apps.core.orchestrator.companion_session import (
    history_from_json,
    history_to_json,
    load_session,
    save_session,
)


def test_greeting_not_empty():
    for mode in ("light_fix", "deep_check", "optimization"):
        assert "你好" in greeting_for_mode(mode)


def test_history_roundtrip():
    turns = [
        ChatTurn("user", "有叮咚不弹窗"),
        ChatTurn("assistant", "请检查 taskbar_icon_point"),
    ]
    raw = history_to_json(turns)
    back = history_from_json(raw)
    assert len(back) == 2
    assert back[0].content.startswith("有叮咚")


def test_save_and_load_session(tmp_path):
    db = tmp_path / "t.db"
    save_session(
        "light_fix",
        summary_md="上次：OCR 为空",
        history=[ChatTurn("user", "test")],
        db_path=db,
    )
    row = load_session("light_fix", db_path=db)
    assert row is not None
    assert "OCR" in row.summary_md
