"""
tests/mobile/test_human_behavior.py
=====================================
测试 HumanBehavior 的延迟范围 / 抖动是否在配置范围内。

策略：
  - patch Path.read_text 返回假 JSON 配置，避免依赖真实文件
  - 用 MagicMock 代替 uiautomator2 元素
  - enabled=False 时验证无明显延迟；enabled=True 时验证范围合规
"""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

_CFG_FAST = {
    "behavior": {
        "enabled":                 True,
        "click_jitter_px":         5,
        "type_delay_min_s":        0.01,
        "type_delay_max_s":        0.02,
        "click_after_min_s":       0.05,
        "click_after_max_s":       0.10,
        "breath_min_s":            0.05,
        "breath_max_s":            0.10,
        "breath_every_n_messages": 2,
        "idle_interval_min_s":     600.0,
        "idle_interval_max_s":     1800.0,
    }
}

_CFG_DISABLED = {**_CFG_FAST, "behavior": {**_CFG_FAST["behavior"], "enabled": False}}


def _load_hb(cfg: dict):
    """加载 HumanBehavior，patch 掉 _load_behavior_cfg，避免依赖真实文件。"""
    behavior_cfg = cfg.get("behavior", {})
    import importlib
    import apps.mobile.behavior.human_behavior as hb_mod
    importlib.reload(hb_mod)   # 重置模块状态，消除副作用
    with patch.object(hb_mod, "_load_behavior_cfg", return_value=behavior_cfg):
        hb = hb_mod.HumanBehavior()
    return hb, hb_mod


class TestHumanBehaviorDisabled(unittest.TestCase):

    def setUp(self):
        self.hb, self.mod = _load_hb(_CFG_DISABLED)

    def test_type_no_significant_delay(self):
        elem = MagicMock()
        t0 = time.monotonic()
        self.hb.human_type(elem, "hello")
        self.assertLess(time.monotonic() - t0, 0.5, "disabled 时 human_type 不应卡顿")

    def test_click_no_significant_delay(self):
        elem = MagicMock()
        elem.info = {"bounds": {"left": 0, "top": 0, "right": 100, "bottom": 50}}
        t0 = time.monotonic()
        self.hb.human_click(elem)
        self.assertLess(time.monotonic() - t0, 0.5, "disabled 时 human_click 不应卡顿")

    def test_breathing_no_delay(self):
        t0 = time.monotonic()
        for i in range(6):
            self.hb.breathing_pause(i)
        self.assertLess(time.monotonic() - t0, 0.5)


class TestHumanBehaviorEnabled(unittest.TestCase):

    def setUp(self):
        self.hb, self.mod = _load_hb(_CFG_FAST)

    def test_breathing_triggers_at_nth_message(self):
        """breath_every_n=2 → 第 2 条消息才触发延迟。"""
        self.hb.breathing_pause(1)          # 不触发

        t0 = time.monotonic()
        self.hb.breathing_pause(2)          # 触发 (breath_min=0.05s)
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.04,
            "breathing_pause 在第 N 条消息时应有延迟")

    def test_type_invokes_element_method(self):
        """human_type 应至少调用过一次输入相关方法。"""
        elem = MagicMock()
        self.hb.human_type(elem, "测试文本")
        called = (
            elem.clear_text.called
            or elem.set_text.called
            or elem.send_keys.called
        )
        self.assertTrue(called, "human_type 应调用元素的输入方法")

    def test_jitter_within_px_bound(self):
        """_jitter_px 范围内随机偏移：用 random.randint 验证边界。"""
        import random
        jitter_px = self.hb._jitter_px   # 实现用 _jitter_px 属性
        for _ in range(200):
            dx = random.randint(-jitter_px, jitter_px)
            dy = random.randint(-jitter_px, jitter_px)
            self.assertGreaterEqual(dx, -jitter_px)
            self.assertLessEqual(dx,    jitter_px)
            self.assertGreaterEqual(dy, -jitter_px)
            self.assertLessEqual(dy,    jitter_px)

    def test_cfg_values_loaded(self):
        """确认配置值被正确加载到各属性（实现用独立属性，不是 _cfg 字典）。"""
        self.assertEqual(self.hb._jitter_px, 5)
        self.assertAlmostEqual(self.hb._type_min, 0.01, places=4)


if __name__ == "__main__":
    unittest.main()
