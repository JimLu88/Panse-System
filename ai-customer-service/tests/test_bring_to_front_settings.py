"""置前配置加载与校验逻辑。"""

from apps.core.ai.input_quality_gate import load_bring_to_front_settings


def test_bring_to_front_restore_sequence_order():
    cfg = load_bring_to_front_settings()
    assert isinstance(cfg.restore_sequence, tuple)
    assert isinstance(cfg.foreground_sequence, tuple)
    methods = [s.method for s in cfg.restore_sequence]
    assert methods[0] == "taskbar_click"
    assert "win32_show_normal" in methods
    fg_methods = [s.method for s in cfg.foreground_sequence]
    assert fg_methods[0] == "win32_setforeground"


def test_bring_to_front_verify_flags():
    cfg = load_bring_to_front_settings()
    assert cfg.verify_not_minimized is True
    assert cfg.verify_window_visible is True
    assert cfg.wait_after_restore_ms == 1.2
    assert cfg.verify_retry_limit >= 2
