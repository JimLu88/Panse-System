"""
tests/mobile/test_state_reader.py
====================================
测试 apps/web_dashboard/ipc/state_reader.py 的 IPC 层：
  - 文件缺失时返回安全默认值
  - 正常 JSON 被正确解析
  - 损坏 JSON 不崩溃，返回默认值
  - write_control_signal 创建文件并写入正确 action
  - read_recent_msgs 的 limit 参数生效
  - 写入再读取的 roundtrip

不依赖真实磁盘路径：用 patch.object 把 _STATE_DIR 替换成 pytest tmp_path。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import apps.web_dashboard.ipc.state_reader as sr


def _patch_state_dir(tmp_path: Path):
    """返回一个 context-manager，把 state_reader._STATE_DIR 重定向到 tmp_path。"""
    return patch.object(sr, "_STATE_DIR", tmp_path)


class TestReadOverview(unittest.TestCase):

    def test_missing_file_returns_default(self):
        with _patch_state_dir(Path("/nonexistent_dir_that_cannot_exist_xyz")):
            result = sr.read_overview()
        self.assertEqual(result["total_today"], 0)
        self.assertEqual(result["active_devices"], 0)
        self.assertEqual(result["error_devices"], 0)
        self.assertFalse(result["paused"])

    def test_valid_file_parsed(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            data = {
                "total_today": 42,
                "active_devices": 3,
                "error_devices": 1,
                "paused": True,
                "updated_at": "2026-05-26T12:00:00",
            }
            (p / "overview.json").write_text(json.dumps(data), encoding="utf-8")
            with _patch_state_dir(p):
                result = sr.read_overview()
        self.assertEqual(result["total_today"], 42)
        self.assertEqual(result["active_devices"], 3)
        self.assertTrue(result["paused"])

    def test_corrupted_json_returns_default(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "overview.json").write_text("{not valid json!!!}", encoding="utf-8")
            with _patch_state_dir(p):
                result = sr.read_overview()
        # 损坏文件 → 默认值，不崩溃
        self.assertEqual(result["total_today"], 0)


class TestReadDevices(unittest.TestCase):

    def test_missing_file_returns_empty_list(self):
        with _patch_state_dir(Path("/nonexistent_dir_xyz2")):
            result = sr.read_devices()
        self.assertEqual(result, [])

    def test_parses_list(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            devices = [
                {"device_id": "dev1", "state": "running", "today_count": 5},
                {"device_id": "dev2", "state": "paused",  "today_count": 2},
            ]
            (p / "devices.json").write_text(json.dumps(devices), encoding="utf-8")
            with _patch_state_dir(p):
                result = sr.read_devices()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["device_id"], "dev1")
        self.assertEqual(result[1]["state"], "paused")

    def test_corrupted_returns_empty_list(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "devices.json").write_text("<<<garbage>>>", encoding="utf-8")
            with _patch_state_dir(p):
                result = sr.read_devices()
        self.assertEqual(result, [])


class TestReadRecentMsgs(unittest.TestCase):

    def test_missing_file_returns_empty_list(self):
        with _patch_state_dir(Path("/nonexistent_dir_xyz3")):
            result = sr.read_recent_msgs()
        self.assertEqual(result, [])

    def test_limit_truncates_to_last_n(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            msgs = [{"text": f"msg{i}"} for i in range(100)]
            (p / "recent_msgs.json").write_text(json.dumps(msgs), encoding="utf-8")
            with _patch_state_dir(p):
                result = sr.read_recent_msgs(limit=10)
        self.assertEqual(len(result), 10)
        # 应取最后 10 条：msg90..msg99
        self.assertEqual(result[0]["text"], "msg90")
        self.assertEqual(result[-1]["text"], "msg99")

    def test_limit_larger_than_list_returns_all(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            msgs = [{"text": f"msg{i}"} for i in range(5)]
            (p / "recent_msgs.json").write_text(json.dumps(msgs), encoding="utf-8")
            with _patch_state_dir(p):
                result = sr.read_recent_msgs(limit=50)
        self.assertEqual(len(result), 5)


class TestWriteControlSignal(unittest.TestCase):

    def test_creates_file_with_pause_action(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            with _patch_state_dir(p):
                sr.write_control_signal("pause_all")
            sig = json.loads((p / "control_signal.json").read_text(encoding="utf-8"))
        self.assertEqual(sig["action"], "pause_all")

    def test_creates_file_with_resume_action(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            with _patch_state_dir(p):
                sr.write_control_signal("resume_all")
            sig = json.loads((p / "control_signal.json").read_text(encoding="utf-8"))
        self.assertEqual(sig["action"], "resume_all")

    def test_does_not_raise_on_permission_error(self):
        """不可写路径下 write_control_signal 应静默失败，不上抛。"""
        with _patch_state_dir(Path("/root/cannot_write_here_xyz")):
            try:
                sr.write_control_signal("pause_all")
            except Exception as exc:  # pragma: no cover
                self.fail(f"write_control_signal 不应抛出异常：{exc}")


class TestWriteReadRoundtrip(unittest.TestCase):

    def test_overview_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            payload = {
                "total_today": 99,
                "active_devices": 4,
                "error_devices": 0,
                "paused": False,
                "updated_at": "2026-05-26T08:00:00",
            }
            (p / "overview.json").write_text(json.dumps(payload), encoding="utf-8")
            with _patch_state_dir(p):
                result = sr.read_overview()
        self.assertEqual(result["total_today"], 99)
        self.assertEqual(result["updated_at"], "2026-05-26T08:00:00")

    def test_control_signal_roundtrip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            with _patch_state_dir(p):
                sr.write_control_signal("none")
                sig_file = p / "control_signal.json"
                self.assertTrue(sig_file.exists())
                sig = json.loads(sig_file.read_text(encoding="utf-8"))
        self.assertEqual(sig["action"], "none")


if __name__ == "__main__":
    unittest.main()
