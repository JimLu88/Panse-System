"""大促 SKU 轮换提醒: 618(5/13~6/18)/双11(10/10~11/11) 窗口判定 (用户 2026-07-03)。"""
from __future__ import annotations

from datetime import date

from app.services import scheduler as sch

W = sch._DEFAULT_ROTATION_WINDOWS


def test_618_window():
    assert sch._active_rotation_window(date(2026, 5, 13), W)["name"] == "618大促"   # 起点
    assert sch._active_rotation_window(date(2026, 6, 1), W)["name"] == "618大促"
    assert sch._active_rotation_window(date(2026, 6, 18), W)["name"] == "618大促"   # 终点(含)
    assert sch._active_rotation_window(date(2026, 5, 12), W) is None                # 前一天
    assert sch._active_rotation_window(date(2026, 6, 19), W) is None                # 后一天


def test_double11_window():
    assert sch._active_rotation_window(date(2026, 10, 10), W)["name"] == "双11大促"
    assert sch._active_rotation_window(date(2026, 11, 11), W)["name"] == "双11大促"
    assert sch._active_rotation_window(date(2026, 10, 9), W) is None
    assert sch._active_rotation_window(date(2026, 11, 12), W) is None


def test_july_between_no_reminder():
    # 现在(7月)在两个大促之间, 不该提醒
    assert sch._active_rotation_window(date(2026, 7, 3), W) is None
    assert sch._active_rotation_window(date(2026, 1, 1), W) is None


def test_bad_window_ignored():
    assert sch._active_rotation_window(date(2026, 6, 1), [{"name": "坏", "start": "", "end": ""}]) is None
    assert sch._active_rotation_window(date(2026, 6, 1), []) is None
