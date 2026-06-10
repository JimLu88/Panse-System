from __future__ import annotations

from pathlib import Path

from apps.core.automation.actions.driver import PhysicalDriver


def execute_send_image(driver: PhysicalDriver, image_path: str | Path) -> None:
    """将本地图片粘贴到当前输入框并发送（与文本发送同一物理线程）。"""
    driver.paste_image_file(Path(image_path))
    driver.press_enter()
