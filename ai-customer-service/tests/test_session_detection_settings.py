"""会话黄条阈值与截图等待配置加载。"""

from apps.core.ai.input_quality_gate import (
    load_audio_cooldown_fallback_s,
    load_debug_snapshot_settings,
    load_session_detection_settings,
    resolve_capture_delay_s,
)


def test_session_detection_audio_threshold():
    sd = load_session_detection_settings()
    assert sd.yel_max_first_frame_audio == 15.0
    assert sd.yel_max_first_frame_visual == 55.0


def test_capture_delay_merges_yaml():
    assert resolve_capture_delay_s(0.8) >= 1.5


def test_audio_cooldown_and_debug_snapshot():
    assert load_audio_cooldown_fallback_s() == 4.0
    assert load_debug_snapshot_settings().save_snapshot is True
