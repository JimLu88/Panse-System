from pathlib import Path

import yaml

from apps.core.configs.shop_yaml_calibration import apply_click_calibration


def test_apply_click_calibration_points(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(
        "\n".join(
            [
                "brand_id: b",
                "shop_code: c",
                "shop_display_name: d",
                "shop_id: sid",
                "qianniu:",
                "  main_window_name_contains: 千牛",
                "  input_box_point: {x: 1, y: 2}",
                "  send_button_point: {x: 0, y: 0}",
                "  chat_scroll_point: {x: 0, y: 0}",
                "ocr_chat_rect: {left: 0, top: 0, right: 10, bottom: 10}",
                "ocr_right_rect: {left: 0, top: 0, right: 10, bottom: 10}",
            ]
        ),
        encoding="utf-8",
    )
    apply_click_calibration(p, "input_box_point", 100, 200)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert raw["qianniu"]["input_box_point"] == {"x": 100, "y": 200}


def test_apply_click_calibration_rect_tl_defaults(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text(
        "\n".join(
            [
                "brand_id: b",
                "shop_code: c",
                "shop_display_name: d",
                "shop_id: sid",
                "qianniu:",
                "  main_window_name_contains: 千牛",
                "  input_box_point: {x: 0, y: 0}",
                "  send_button_point: {x: 0, y: 0}",
                "  chat_scroll_point: {x: 0, y: 0}",
                "ocr_chat_rect: {left: 0, top: 0, right: 0, bottom: 0}",
            ]
        ),
        encoding="utf-8",
    )
    apply_click_calibration(p, "ocr_chat_tl", 50, 60)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    r = raw["ocr_chat_rect"]
    assert r["left"] == 50 and r["top"] == 60
    assert r["right"] == 50 + 400 and r["bottom"] == 60 + 300
