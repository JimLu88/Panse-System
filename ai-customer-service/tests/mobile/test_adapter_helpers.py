"""
tests/mobile/test_adapter_helpers.py
======================================
Pure-function helpers from apps.mobile.adapter.mobile_adapter:
  - _is_meta: time/status string detector used while parsing session rows
"""
from __future__ import annotations

import unittest

from apps.mobile.adapter.mobile_adapter import _is_meta


class TestIsMeta(unittest.TestCase):

    def test_time_hhmm_is_meta(self):
        for s in ["09:30", "23:59", "0:05", "12:00"]:
            self.assertTrue(_is_meta(s), f"{s!r} should be meta")

    def test_yesterday_terms_are_meta(self):
        for s in ["昨天", "前天"]:
            self.assertTrue(_is_meta(s))

    def test_date_strings_are_meta(self):
        for s in ["1月5日", "12月31日", "2026-05-26"]:
            self.assertTrue(_is_meta(s))

    def test_weekday_is_meta(self):
        for s in ["星期一", "星期日", "星期六"]:
            self.assertTrue(_is_meta(s))

    def test_status_tags_are_meta(self):
        for s in ["[已读]", "[未读]", ""]:
            self.assertTrue(_is_meta(s))

    def test_buyer_names_are_not_meta(self):
        for s in ["张三", "小明123", "买家A", "alice", "Test User"]:
            self.assertFalse(_is_meta(s), f"{s!r} should NOT be meta")

    def test_normal_message_text_is_not_meta(self):
        for s in ["你好", "请问有货吗", "Hello world"]:
            self.assertFalse(_is_meta(s))


if __name__ == "__main__":
    unittest.main()
