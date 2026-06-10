"""
tests/mobile/test_shadow.py
=============================
ShadowAdapter + shadow_diff 单元测试。

- ShadowAdapter.send_text 被拦截，写入 JSONL 而不真实发送
- 所有读操作（list_*, read_*, get_anchor, screenshot）正确委派给真实 adapter
- shadow_diff.load_records 按日期过滤
- shadow_diff.generate_report 生成 Markdown 报告
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from apps.mobile.adapter.base import Message, Session


class TestShadowAdapterDelegation(unittest.TestCase):
    """ShadowAdapter 的所有读操作应原样转发给真实 adapter。"""

    def setUp(self):
        from apps.mobile.orchestrator.shadow_mode import ShadowAdapter
        self.real = MagicMock()
        self.real.device_id = "wrapped_dev"
        self.shadow = ShadowAdapter(self.real)

    def test_device_id_delegates(self):
        self.assertEqual(self.shadow.device_id, "wrapped_dev")

    def test_connect_delegates(self):
        self.real.connect.return_value = True
        self.assertTrue(self.shadow.connect())
        self.real.connect.assert_called_once()

    def test_disconnect_delegates(self):
        self.shadow.disconnect()
        self.real.disconnect.assert_called_once()

    def test_is_alive_delegates(self):
        self.real.is_alive.return_value = True
        self.assertTrue(self.shadow.is_alive())

    def test_list_unread_sessions_delegates(self):
        s = Session(session_id="s1", buyer_name="A", unread=True, last_msg_preview="hi")
        self.real.list_unread_sessions.return_value = [s]
        result = self.shadow.list_unread_sessions()
        self.assertEqual(result, [s])

    def test_list_all_sessions_passes_limit(self):
        """Bug fix verification: list_all_sessions must forward limit kwarg."""
        self.real.list_all_sessions.return_value = []
        self.shadow.list_all_sessions(limit=20)
        # limit should be forwarded as positional arg
        self.real.list_all_sessions.assert_called_once_with(20)

    def test_switch_to_session_delegates(self):
        s = Session(session_id="s1", buyer_name="A", unread=True, last_msg_preview="")
        self.real.switch_to_session.return_value = True
        self.assertTrue(self.shadow.switch_to_session(s))

    def test_read_latest_messages_delegates_with_limit(self):
        m = Message(msg_type="text", text="hi", is_from_buyer=True, timestamp="")
        self.real.read_latest_messages.return_value = [m]
        result = self.shadow.read_latest_messages(limit=5)
        self.assertEqual(result, [m])
        self.real.read_latest_messages.assert_called_once_with(5)

    def test_get_current_buyer_anchor_delegates(self):
        self.real.get_current_buyer_anchor.return_value = "买家X"
        self.assertEqual(self.shadow.get_current_buyer_anchor(), "买家X")

    def test_start_notification_listener_delegates(self):
        cb = lambda s, t: None
        self.shadow.start_notification_listener(cb)
        self.real.start_notification_listener.assert_called_once_with(cb)

    def test_screenshot_bytes_delegates_when_available(self):
        self.real.screenshot_bytes = MagicMock(return_value=b"PNG")
        self.assertEqual(self.shadow.screenshot_bytes(), b"PNG")

    def test_screenshot_bytes_returns_none_when_unavailable(self):
        # Real adapter has no screenshot_bytes method
        real = object()  # no screenshot_bytes attr
        from apps.mobile.orchestrator.shadow_mode import ShadowAdapter
        # Have to wrap something QianniuAdapter-shaped via MagicMock
        bare = MagicMock(spec=[])  # spec=[] means no auto-attrs
        bare.device_id = "x"
        sh = ShadowAdapter(bare)
        self.assertIsNone(sh.screenshot_bytes())


class TestShadowAdapterSendIntercepted(unittest.TestCase):
    """send_text 必须被拦截，不调用真实 adapter，写入 JSONL。"""

    def setUp(self):
        import apps.mobile.orchestrator.shadow_mode as sm
        self.sm = sm
        self.real = MagicMock()
        self.real.device_id = "intercept_dev"
        self.shadow = sm.ShadowAdapter(self.real)

    def test_send_text_does_not_call_real(self):
        self.shadow.send_text("foo")
        self.real.send_text.assert_not_called()

    def test_send_text_returns_true(self):
        """让上层认为发送成功，避免重试。"""
        self.assertTrue(self.shadow.send_text("bar"))

    def test_send_text_writes_jsonl(self):
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            fake_file = Path(td) / "shadow_replies.jsonl"
            with patch.object(self.sm, "_SHADOW_FILE", fake_file):
                self.shadow.send_text("hello buyer")
                self.shadow.send_text("second msg")
            lines = fake_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        rec0 = json.loads(lines[0])
        self.assertEqual(rec0["text"], "hello buyer")
        self.assertEqual(rec0["device_id"], "intercept_dev")
        self.assertFalse(rec0["sent"])
        self.assertTrue(rec0["shadow"])

    def test_send_text_does_not_raise_on_write_failure(self):
        """文件不可写时 send_text 应静默失败、仍返回 True。"""
        with patch.object(self.sm, "_SHADOW_FILE", Path("/no/perm/zzz/shadow.jsonl")):
            try:
                ok = self.shadow.send_text("attempt")
            except Exception as exc:
                self.fail(f"send_text 不应抛出异常: {exc}")
        self.assertTrue(ok)


class TestShadowDiff(unittest.TestCase):
    """shadow_diff 报告生成器。"""

    def _write_records(self, tmp_path: Path, recs: list[dict]) -> Path:
        f = tmp_path / "shadow_replies.jsonl"
        lines = [json.dumps(r, ensure_ascii=False) for r in recs]
        f.write_text(chr(10).join(lines), encoding="utf-8")
        return f

    def test_load_records_filters_by_date(self):
        import apps.mobile.orchestrator.shadow_diff as sd
        import tempfile
        from datetime import datetime, timedelta
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            now = datetime.now()
            old = (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%S")
            recent = now.strftime("%Y-%m-%dT%H:%M:%S")
            self._write_records(p, [
                {"ts": old,    "device_id": "d1", "text": "old msg",    "sent": False, "shadow": True},
                {"ts": recent, "device_id": "d1", "text": "recent msg", "sent": False, "shadow": True},
            ])
            with patch.object(sd, "_SHADOW_FILE", p / "shadow_replies.jsonl"):
                recs = sd.load_records(days=1)
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["text"], "recent msg")

    def test_load_records_missing_file(self):
        import apps.mobile.orchestrator.shadow_diff as sd
        with patch.object(sd, "_SHADOW_FILE", Path("/nonexistent/xyz.jsonl")):
            recs = sd.load_records(days=7)
        self.assertEqual(recs, [])

    def test_generate_report_contains_count(self):
        import apps.mobile.orchestrator.shadow_diff as sd
        import tempfile
        from datetime import datetime
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            recs = [
                {"ts": now, "device_id": "d1", "text": f"msg{i}", "sent": False, "shadow": True}
                for i in range(5)
            ]
            self._write_records(p, recs)
            with patch.object(sd, "_SHADOW_FILE", p / "shadow_replies.jsonl"):
                report = sd.generate_report(days=1)
        # 报告应包含统计数 5
        self.assertIn("5", report)


if __name__ == "__main__":
    unittest.main()
