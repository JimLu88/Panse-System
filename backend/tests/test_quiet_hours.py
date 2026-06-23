"""夜间模式 (2026-06-23 降 NAS 负载): 安静时段看门狗不写盘, 让盘休眠。"""
from app.services import system_monitor as sm


def test_window_default(monkeypatch):
    monkeypatch.delenv("PANSE_QUIET_HOURS", raising=False)
    assert sm._quiet_hours_window() == (23, 7)


def test_in_quiet_hours_overnight_wrap(monkeypatch):
    monkeypatch.delenv("PANSE_QUIET_HOURS", raising=False)
    for h in (23, 0, 3, 6):
        assert sm._in_quiet_hours(h) is True      # 23:00-07:00 内
    for h in (7, 8, 12, 18, 22):
        assert sm._in_quiet_hours(h) is False     # 白天/低峰外


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("PANSE_QUIET_HOURS", "off")
    assert sm._quiet_hours_window() is None
    assert sm._in_quiet_hours(3) is False         # 禁用 → 任何时刻都不算安静时段


def test_custom_window(monkeypatch):
    monkeypatch.setenv("PANSE_QUIET_HOURS", "1-5")
    assert sm._quiet_hours_window() == (1, 5)
    assert sm._in_quiet_hours(2) is True
    assert sm._in_quiet_hours(0) is False
    assert sm._in_quiet_hours(5) is False
