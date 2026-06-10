from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from apps.core.automation.win_clipboard import set_clipboard_text
from apps.core.automation.win_input import press_ctrl_combo, press_vk, VK_RETURN


class PhysicalDriver:
    """
    Abstraction over physical operations (window focus / clipboard paste / keypress).

    IMPORTANT: Implementations must only be called from SequentialExecutor thread.
    """

    def sleep(self, seconds: float) -> None:
        time.sleep(float(seconds))

    def paste_text(self, text: str) -> None:  # pragma: no cover (interface)
        raise NotImplementedError

    def press_enter(self) -> None:  # pragma: no cover (interface)
        raise NotImplementedError

    def paste_image_file(self, path: Path) -> None:  # pragma: no cover (interface)
        raise NotImplementedError


@dataclass(slots=True)
class DryRunDriver(PhysicalDriver):
    """
    Driver used for MVP testing without touching real windows.
    """

    def paste_text(self, text: str) -> None:
        # no-op
        return

    def press_enter(self) -> None:
        # no-op
        return

    def paste_image_file(self, path: Path) -> None:
        return


@dataclass(slots=True)
class ActiveWindowSendInputDriver(PhysicalDriver):
    """
    Real driver: paste+enter to the current foreground window.

    Safety:
    - This does NOT try to focus any specific window.
    - Use only when you intentionally put the correct target window in foreground.
    """

    def paste_text(self, text: str) -> None:
        set_clipboard_text(text)
        # Ctrl+V
        press_ctrl_combo("v")

    def press_enter(self) -> None:
        press_vk(VK_RETURN)

    def paste_image_file(self, path: Path) -> None:
        from apps.core.automation.win_clipboard_image import set_clipboard_dib_from_image_path

        set_clipboard_dib_from_image_path(path)
        press_ctrl_combo("v")

