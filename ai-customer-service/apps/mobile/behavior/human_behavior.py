"""
apps/mobile/behavior/human_behavior.py
========================================
拟人化操作中间件。

所有对 Android 控件的点击 / 输入 / 滑动必须经过此层，
禁止 mobile_adapter 之外的代码直接调用 u2 原生方法。

可配置开关：configs/mobile/mobile_config.json → behavior.enabled
测试期可设 false 加速执行。
"""
from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path
from typing import Any

from apps.core.runtime_paths import configs_dir as _configs_dir

_log = logging.getLogger("apps.mobile.behavior")

# 通过 runtime_paths 解析，PyInstaller 打包和开发模式均正确指向项目根目录。
_CONFIG_PATH: Path = _configs_dir() / "mobile" / "mobile_config.json"


def _load_behavior_cfg() -> dict[str, Any]:
    try:
        raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return raw.get("behavior", {})
    except Exception:
        return {}


class HumanBehavior:
    """
    拟人化操作封装。

    - human_click(elem)      : 随机偏移点击 + 点后延迟
    - human_type(elem, text) : 逐字输入 + 字间延迟
    - breathing_pause(n)     : 每 N 条消息后随机空闲
    - random_idle_action(d)  : 随机上划列表等闲置动作

    enabled=False 时所有方法退化为原生直接调用，方便测试加速。
    """

    def __init__(self) -> None:
        cfg = _load_behavior_cfg()
        self.enabled: bool = bool(cfg.get("enabled", True))
        self._jitter_px: int = int(cfg.get("click_jitter_px", 5))
        self._type_min: float = float(cfg.get("type_delay_min_s", 0.08))
        self._type_max: float = float(cfg.get("type_delay_max_s", 0.25))
        self._click_after_min: float = float(cfg.get("click_after_min_s", 0.5))
        self._click_after_max: float = float(cfg.get("click_after_max_s", 1.5))
        self._breath_min: float = float(cfg.get("breath_min_s", 5.0))
        self._breath_max: float = float(cfg.get("breath_max_s", 15.0))
        self._breath_every_n: int = int(cfg.get("breath_every_n_messages", 4))
        self._idle_min_s: float = float(cfg.get("idle_interval_min_s", 600.0))
        self._idle_max_s: float = float(cfg.get("idle_interval_max_s", 1800.0))
        self._last_idle_at: float = 0.0

    def human_click(self, elem: Any) -> None:
        """控件范围内随机偏移 ±jitter_px 点击，点后随机延迟。"""
        if not self.enabled:
            elem.click()
            return
        try:
            bounds = elem.info.get("bounds", {})
            lx = bounds.get("left", 0)
            rx = bounds.get("right", lx + 10)
            ty = bounds.get("top", 0)
            by = bounds.get("bottom", ty + 10)
            cx = (lx + rx) // 2 + random.randint(-self._jitter_px, self._jitter_px)
            cy = (ty + by) // 2 + random.randint(-self._jitter_px, self._jitter_px)
            cx = max(lx + 2, min(cx, rx - 2))
            cy = max(ty + 2, min(cy, by - 2))
            elem.click(cx, cy)
        except Exception:
            elem.click()
        time.sleep(random.uniform(self._click_after_min, self._click_after_max))

    def human_type(self, elem: Any, text: str) -> None:
        """逐字输入，字间随机延迟；完成后等 1-3s。"""
        if not self.enabled:
            elem.set_text(text)
            return
        try:
            elem.clear_text()
            time.sleep(0.2)
            for ch in text:
                try:
                    elem.send_keys(ch)
                except Exception:
                    pass
                time.sleep(random.uniform(self._type_min, self._type_max))
        except Exception:
            elem.set_text(text)
        time.sleep(random.uniform(1.0, 3.0))

    def breathing_pause(self, msg_count: int) -> None:
        """每处理 breath_every_n 条消息后，随机空闲 breath_min ~ breath_max 秒。"""
        if not self.enabled:
            return
        if msg_count > 0 and msg_count % self._breath_every_n == 0:
            pause = random.uniform(self._breath_min, self._breath_max)
            _log.debug("呼吸暂停 %.1fs（已处理 %d 条）", pause, msg_count)
            time.sleep(pause)

    def random_idle_action(self, d: Any) -> None:
        """
        每 idle_interval_min ~ idle_interval_max 秒执行一次随机闲置动作。
        主循环中定期调用；方法内部判断时间间隔是否满足。
        """
        if not self.enabled:
            return
        now = time.monotonic()
        interval = random.uniform(self._idle_min_s, self._idle_max_s)
        if self._last_idle_at > 0 and (now - self._last_idle_at) < interval:
            return
        self._last_idle_at = now
        try:
            if random.random() < 0.5:
                d.swipe(540, 1200, 540, 800, duration=0.4)
                time.sleep(random.uniform(1.0, 2.5))
                d.swipe(540, 800, 540, 1200, duration=0.4)
                _log.debug("闲置动作：上划列表")
        except Exception as exc:
            _log.debug("闲置动作异常（忽略）: %r", exc)
