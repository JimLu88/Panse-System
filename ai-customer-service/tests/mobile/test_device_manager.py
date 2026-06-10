"""
tests/mobile/test_device_manager.py
======================================
测试 DeviceManager 的纯 Python 逻辑：
  - add / remove / get / all_records
  - 状态转换（DISCONNECTED → CONNECTED → RUNNING → DISCONNECTED）
  - pause / stop 语义
  - _devices 字典并发安全（_lock 保护）

不依赖 uiautomator2 / ADB；connect_device 被 mock 掉硬件依赖。
"""
from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from apps.mobile.device.device_manager import DeviceManager, DeviceState


class TestDeviceManagerCRUD(unittest.TestCase):

    def setUp(self):
        self.dm = DeviceManager()

    def test_add_and_get(self):
        rec = self.dm.add_device("127.0.0.1:5555", "emulator", "configs/shops/test.yaml")
        self.assertEqual(rec.device_id, "127.0.0.1:5555")
        self.assertEqual(rec.device_type, "emulator")
        self.assertEqual(rec.state, DeviceState.DISCONNECTED)
        self.assertIs(self.dm.get_record("127.0.0.1:5555"), rec)

    def test_add_duplicate_overwrites(self):
        self.dm.add_device("127.0.0.1:5555", "emulator", "a.yaml")
        self.dm.add_device("127.0.0.1:5555", "wifi",     "b.yaml")
        rec = self.dm.get_record("127.0.0.1:5555")
        self.assertEqual(rec.device_type, "wifi")
        self.assertEqual(rec.shop_cfg_path, "b.yaml")

    def test_remove(self):
        self.dm.add_device("127.0.0.1:5555", "emulator", "a.yaml")
        self.dm.remove_device("127.0.0.1:5555")
        self.assertIsNone(self.dm.get_record("127.0.0.1:5555"))

    def test_remove_nonexistent_is_noop(self):
        self.dm.remove_device("ghost")   # 不应抛异常

    def test_all_records_empty(self):
        self.assertEqual(self.dm.all_records(), [])

    def test_all_records_multiple(self):
        self.dm.add_device("dev1", "emulator", "a.yaml")
        self.dm.add_device("dev2", "wifi",     "b.yaml")
        ids = {r.device_id for r in self.dm.all_records()}
        self.assertEqual(ids, {"dev1", "dev2"})

    def test_get_nonexistent_returns_none(self):
        self.assertIsNone(self.dm.get_record("ghost"))


class TestDeviceManagerStateMachine(unittest.TestCase):

    def setUp(self):
        self.dm = DeviceManager()
        self.dm.add_device("dev1", "emulator", "a.yaml")

    def test_initial_state_is_disconnected(self):
        self.assertEqual(self.dm.get_record("dev1").state, DeviceState.DISCONNECTED)

    def test_pause_sets_paused(self):
        self.dm.pause_device("dev1")
        self.assertEqual(self.dm.get_record("dev1").state, DeviceState.PAUSED)

    def test_stop_sets_disconnected(self):
        self.dm.pause_device("dev1")
        self.dm.stop_device("dev1")
        self.assertEqual(self.dm.get_record("dev1").state, DeviceState.DISCONNECTED)

    def test_connect_success_sets_connected(self):
        mock_adapter = MagicMock()
        mock_adapter.connect.return_value = True

        with patch("apps.mobile.adapter.mobile_adapter.MobileQianniuAdapter",
                   return_value=mock_adapter), \
             patch("apps.mobile.behavior.human_behavior.HumanBehavior"):
            ok = self.dm.connect_device("dev1")

        self.assertTrue(ok)
        mock_adapter.connect.assert_called_once()
        self.assertEqual(self.dm.get_record("dev1").state, DeviceState.CONNECTED)

    def test_connect_failure_sets_error(self):
        mock_adapter = MagicMock()
        mock_adapter.connect.return_value = False

        with patch("apps.mobile.adapter.mobile_adapter.MobileQianniuAdapter",
                   return_value=mock_adapter), \
             patch("apps.mobile.behavior.human_behavior.HumanBehavior"):
            ok = self.dm.connect_device("dev1")

        self.assertFalse(ok)
        self.assertEqual(self.dm.get_record("dev1").state, DeviceState.ERROR)

    def test_start_requires_connected_or_paused(self):
        """DISCONNECTED 状态下不应能启动。"""
        bridge = MagicMock()
        ok = self.dm.start_device("dev1", bridge)
        self.assertFalse(ok)

    def test_start_after_connect_launches_thread(self):
        """CONNECTED 状态下 start_device 应成功并启动 daemon 线程。"""
        mock_adapter = MagicMock()
        mock_adapter.connect.return_value = True
        mock_adapter.is_alive.return_value = True
        mock_adapter.list_unread_sessions.return_value = []

        with patch("apps.mobile.adapter.mobile_adapter.MobileQianniuAdapter",
                   return_value=mock_adapter), \
             patch("apps.mobile.behavior.human_behavior.HumanBehavior"):
            self.dm.connect_device("dev1")

        bridge = MagicMock()
        ok = self.dm.start_device("dev1", bridge)
        self.assertTrue(ok)
        rec = self.dm.get_record("dev1")
        self.assertEqual(rec.state, DeviceState.RUNNING)
        self.assertIsNotNone(rec._thread)
        self.assertTrue(rec._thread.daemon)

        # 清理：停止线程
        self.dm.stop_device("dev1")


class TestDeviceManagerConcurrency(unittest.TestCase):
    """并发读写 _devices 不应崩溃（验证 _lock 保护）。"""

    def test_concurrent_add_and_read(self):
        dm = DeviceManager()
        errors: list[Exception] = []

        def adder():
            try:
                for i in range(40):
                    dm.add_device(f"dev{i}", "wifi", f"{i}.yaml")
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                for _ in range(80):
                    _ = dm.all_records()
                    time.sleep(0)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=adder),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"并发操作出现异常: {errors}")


if __name__ == "__main__":
    unittest.main()
