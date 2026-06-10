"""anchor_calibrate 纯函数单测（v1.6.3 窗口+偏移预测）。"""
from __future__ import annotations

from apps.core.automation.anchor_calibrate import (
    CalibAnchor,
    build_anchor,
    from_yaml_dict,
    predict_points,
    predict_rects,
    to_yaml_dict,
    window_unchanged,
)


def test_build_anchor_computes_offsets_relative_to_window_topleft():
    # Arrange
    base_window = (100, 200, 1100, 1000)  # wl=100, wt=200
    points = {"input_box_point": (300, 500), "send_button_point": (900, 800)}
    rects = {"ocr_chat_rect": (150, 250, 1000, 700)}

    # Act
    anchor = build_anchor(base_window, points, rects)

    # Assert
    assert anchor.point_offsets["input_box_point"] == (200, 300)
    assert anchor.point_offsets["send_button_point"] == (800, 600)
    assert anchor.rect_offsets["ocr_chat_rect"] == (50, 50, 900, 500)


def test_build_anchor_skips_unknown_and_none_fields():
    base_window = (0, 0, 100, 100)
    points = {"input_box_point": (10, 10), "unknown_point": (5, 5),
              "send_button_point": (None, 20)}
    anchor = build_anchor(base_window, points, {})
    assert "input_box_point" in anchor.point_offsets
    assert "unknown_point" not in anchor.point_offsets
    assert "send_button_point" not in anchor.point_offsets


def test_predict_round_trip_recovers_original_when_window_unmoved():
    base_window = (100, 200, 1100, 1000)
    points = {"input_box_point": (300, 500)}
    rects = {"session_list_rect": (110, 240, 320, 900)}
    anchor = build_anchor(base_window, points, rects)

    # 窗口没动 → 预测应等于原始坐标
    assert predict_points(anchor, base_window)["input_box_point"] == (300, 500)
    assert predict_rects(anchor, base_window)["session_list_rect"] == (110, 240, 320, 900)


def test_predict_follows_window_translation():
    base_window = (100, 200, 1100, 1000)
    points = {"input_box_point": (300, 500)}
    anchor = build_anchor(base_window, points, {})

    # 窗口整体平移 (+50, +30)
    moved = (150, 230, 1150, 1030)
    assert predict_points(anchor, moved)["input_box_point"] == (350, 530)


def test_window_unchanged_tolerance():
    base = (100, 200, 1100, 1000)
    assert window_unchanged(base, (100, 200, 1100, 1000))
    assert window_unchanged(base, (104, 197, 1100, 1003))  # 各维 <=6
    assert not window_unchanged(base, (120, 200, 1100, 1000))  # x 差 20


def test_yaml_round_trip():
    base_window = (100, 200, 1100, 1000)
    points = {"input_box_point": (300, 500), "send_button_point": (900, 800)}
    rects = {"ocr_chat_rect": (150, 250, 1000, 700)}
    anchor = build_anchor(base_window, points, rects)

    restored = from_yaml_dict(to_yaml_dict(anchor))
    assert isinstance(restored, CalibAnchor)
    assert restored.base_window == anchor.base_window
    assert restored.point_offsets == anchor.point_offsets
    assert restored.rect_offsets == anchor.rect_offsets


def test_from_yaml_dict_rejects_garbage():
    assert from_yaml_dict(None) is None
    assert from_yaml_dict({}) is None
    assert from_yaml_dict({"base_window": {"left": 0}}) is None  # 缺字段
    # 有 base_window 但无任何 offset → None（无可预测内容）
    assert from_yaml_dict({
        "base_window": {"left": 0, "top": 0, "right": 1, "bottom": 1},
        "point_offsets": {}, "rect_offsets": {},
    }) is None
