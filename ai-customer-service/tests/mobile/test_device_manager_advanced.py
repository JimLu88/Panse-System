"""
tests/mobile/test_device_manager_advanced.py
=============================================
DeviceManager 高级功能：
  - list_available_devices (adb 输出解析)
  - auto_reconnect (max_retries / 状态转换)
  - save_devices_config / load_devices_config (JSON 持久化)
  - connect_device 重连资源清理（防止 notif_thread 泄漏）
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from apps.mobile.device.device_manager import DeviceManager, DeviceState


class TestListAvailableDevices(unittest.TestCase):

    def _stub_adb(self, out: str):
        return patch("subprocess.check_output", return_value=out)

    def test_parses_emulator(self):
        out = "List of devices attached\n127.0.0.1:5555\tdevice\n"
        with self._stub_adb(out):
            devs = DeviceManager().list_available_devices()
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["type"], "emulator")
        self.assertEqual(devs[0]["device_id"], "127.0.0.1:5555")

    def test_parses_usb_and_wifi(self):
        out = (
            "List of devices attached\n"
            "25098PN5AC\tdevice\n"
            "192.168.1.10:5555\tdevice\n"
            "emulator-5554\tdevice\n"
        )
        with self._stub_adb(out):
            devs = DeviceManager().list_available_devices()
        types = {d["device_id"]: d["type"] for d in devs}
        self.assertEqual(types["25098PN5AC"], "usb")
        self.assertEqual(types["192.168.1.10:5555"], "wifi")
        self.assertEqual(types["emulator-5554"], "emulator")

    def test_skips_unauthorized(self):
        out = "List of devices attached\nfoo\tunauthorized\nbar\tdevice\n"
        with self._stub_adb(out):
            devs = DeviceManager().list_available_devices()
        ids = [d["device_id"] for d in devs]
        self.assertNotIn("foo", ids)
        self.assertIn("bar", ids)

    def test_adb_failure_returns_empty(self):
        with patch("subprocess.check_output", side_effect=FileNotFoundError("adb")):
            devs = DeviceManager().list_available_devices()
        self.assertEqual(devs, [])


class TestAutoReconnect(unittest.TestCase):

    def setUp(self):
        self.dm = DeviceManager()
        self.dm.add_device("dev1", "wifi", "x.yaml")

    def test_success_on_first_attempt(self):
        with patch.object(self.dm, "connect_device", return_value=True) as mock_conn, \
             patch("time.sleep"):
            ok = self.dm.auto_reconnect("dev1", max_retries=3)
        self.assertTrue(ok)
        self.assertEqual(mock_conn.call_count, 1)

    def test_success_on_third_attempt(self):
        results = [False, False, True]
        with patch.object(self.dm, "connect_device", side_effect=results) as mock_conn, \
             patch("time.sleep"):
            ok = self.dm.auto_reconnect("dev1", max_retries=3)
        self.assertTrue(ok)
        self.assertEqual(mock_conn.call_count, 3)

    def test_failure_after_max_retries(self):
        with patch.object(self.dm, "connect_device", return_value=False) as mock_conn, \
             patch("time.sleep"):
            ok = self.dm.auto_reconnect("dev1", max_retries=2)
        self.assertFalse(ok)
        self.assertEqual(mock_conn.call_count, 2)
        self.assertEqual(self.dm.get_record("dev1").state, DeviceState.ERROR)


class TestConfigPersistence(unittest.TestCase):

    def test_save_then_load_roundtrip(self):
        import apps.mobile.device.device_manager as dm_mod
        with tempfile.TemporaryDirectory() as td:
            fake_cfg = Path(td) / "mobile_devices.json"
            with patch.object(dm_mod, "_DEVICES_CFG", fake_cfg):
                dm1 = dm_mod.DeviceManager()
                dm1.add_device("emu1", "emulator", "shop_A.yaml")
                dm1.add_device("phone1", "wifi", "shop_B.yaml")
                dm1.save_devices_config()
                self.assertTrue(fake_cfg.exists())

                dm2 = dm_mod.DeviceManager()
                dm2.load_devices_config()
                records = {r.device_id: r for r in dm2.all_records()}
        self.assertIn("emu1", records)
        self.assertEqual(records["emu1"].device_type, "emulator")
        self.assertEqual(records["emu1"].shop_cfg_path, "shop_A.yaml")
        self.assertIn("phone1", records)
        self.assertEqual(records["phone1"].device_type, "wifi")

    def test_load_missing_file_is_noop(self):
        import apps.mobile.device.device_manager as dm_mod
        with patch.object(dm_mod, "_DEVICES_CFG", Path("/nonexistent/zzz.json")):
            dm = dm_mod.DeviceManager()
            dm.load_devices_config()
        self.assertEqual(dm.all_records(), [])

    def test_load_corrupted_file_does_not_raise(self):
        import apps.mobile.device.device_manager as dm_mod
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "mobile_devices.json"
            bad.write_text("not-json-at-all", encoding="utf-8")
            with patch.object(dm_mod, "_DEVICES_CFG", bad):
                dm = dm_mod.DeviceManager()
                try:
                    dm.load_devices_config()
                except Exception as exc:
                    self.fail(f"load_devices_config 不应抛出: {exc}")


class TestConnectDeviceResourceCleanup(unittest.TestCase):
    """验证 connect_device 在重连时会先 disconnect 旧 adapter（资源泄漏防护）。"""

    def test_reconnect_disconnects_old_adapter(self):
        dm = DeviceManager()
        rec = dm.add_device("dev1", "wifi", "x.yaml")

        old_adapter = MagicMock()
        rec.adapter = old_adapter

        new_adapter = MagicMock()
        new_adapter.connect.return_value = True

        with patch("apps.mobile.adapter.mobile_adapter.MobileQianniuAdapter",
                   return_value=new_adapter), \
             patch("apps.mobile.behavior.human_behavior.HumanBehavior"):
            dm.connect_device("dev1")

        old_adapter.disconnect.assert_called_once()
        self.assertIs(dm.get_record("dev1").adapter, new_adapter)

    def test_reconnect_tolerates_old_disconnect_exception(self):
        dm = DeviceManager()
        rec = dm.add_device("dev1", "wifi", "x.yaml")

        old = MagicMock()
        old.disconnect.side_effect = Exception("simulated stale device")
        rec.adapter = old

        new_adapter = MagicMock()
        new_adapter.connect.return_value = True

        with patch("apps.mobile.adapter.mobile_adapter.MobileQianniuAdapter",
                   return_value=new_adapter), \
             patch("apps.mobile.behavior.human_behavior.HumanBehavior"):
            ok = dm.connect_device("dev1")

        self.assertTrue(ok)
        self.assertEqual(dm.get_record("dev1").state, DeviceState.CONNECTED)


if __name__ == "__main__":
    unittest.main()
