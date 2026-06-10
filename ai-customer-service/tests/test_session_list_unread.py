import numpy as np

from apps.core.channels.qianniu import session_list_unread as slu


def test_yellow_mask_detects_bright_yellow() -> None:
    rgb = np.zeros((10, 10, 3), dtype=np.uint8)
    rgb[3:7, 3:7, 0] = 255
    rgb[3:7, 3:7, 1] = 220
    rgb[3:7, 3:7, 2] = 40
    m = slu._yellow_mask_rgb(rgb)
    assert m[5, 5]
    assert not m[0, 0]


def test_pick_yellow_row_topmost_vs_max() -> None:
    rows = np.zeros(400, dtype=np.float64)
    rows[10] = 50.0
    rows[339] = 80.0
    top_row, _ = slu.pick_yellow_row(rows, threshold=15.0, pick_mode="topmost")
    max_row, _ = slu.pick_yellow_row(rows, threshold=15.0, pick_mode="max", max_row_index=30)
    assert top_row == 10
    assert max_row == 10


def test_pick_yellow_row_respects_max_row_index() -> None:
    rows = np.zeros(400, dtype=np.float64)
    rows[5] = 50.0
    rows[200] = 80.0
    row, _ = slu.pick_yellow_row(
        rows, threshold=15.0, pick_mode="topmost", max_row_index=30
    )
    assert row == 5


def test_red_badge_only_scans_top_rows() -> None:
    """row=24 且 redPx=8 的误检应被顶部窗口与阈值过滤。"""
    h, w = 200, 120
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[5, 70:100, 0] = 220
    rgb[5, 70:100, 1] = 50
    rows = slu._row_sums(slu._red_badge_mask(rgb[:56]))
    hits = np.flatnonzero(rows >= 18.0)
    assert hits.size > 0
    assert int(hits[0]) <= 10
