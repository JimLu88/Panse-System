from __future__ import annotations
import sys, time, unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

@contextmanager
def _bridge_ctx(adapter=None, cooldown_s=90.0):
    if adapter is None:
        adapter = MagicMock()
        adapter.device_id = "test_dev"
    mock_aq = MagicMock()
    def _slow_get(timeout=1.0):
        time.sleep(min(float(timeout), 0.08))
        raise Exception("empty")
    mock_aq.get.side_effect = _slow_get
    mock_aq_cls  = MagicMock(return_value=mock_aq)
    mock_ep_cls  = MagicMock()
    mock_orch_cls = MagicMock()
    fake_aq   = MagicMock(); fake_aq.ActionQueue = mock_aq_cls
    fake_ep   = MagicMock(); fake_ep.EventPipeline = mock_ep_cls; fake_ep.PipelineOrchestrator = mock_orch_cls
    fake_rp   = MagicMock(); fake_rp.default_sqlite_db_path = MagicMock(return_value=Path("/tmp/t.db"))
    fake_iqg  = MagicMock()
    gr = MagicMock(); gr.action = "ok"; gr.rule_name = None
    fake_iqg.check_buyer_input.return_value = gr
    fake_mdl  = MagicMock()
    fake_rg   = MagicMock()
    fake_rg.is_echo_or_noise_buyer_text.return_value = False
    fake_rg.should_skip_duplicate_buyer.return_value = False
    fake_rg.normalize_buyer_digest.side_effect = lambda t: t.lower().strip()
    stubs = {
        "apps.core.orchestrator.action_queue":   fake_aq,
        "apps.core.orchestrator.event_pipeline": fake_ep,
        "apps.core.runtime_paths":               fake_rp,
        "apps.core.ai.input_quality_gate":       fake_iqg,
        "apps.core.orchestrator.models":         fake_mdl,
        "apps.core.orchestrator.reply_guards":   fake_rg,
    }
    with patch.dict(sys.modules, stubs, clear=False):
        import importlib, apps.mobile.orchestrator.mobile_brain_bridge as bbm
        importlib.reload(bbm)
        bridge = bbm.MobileBrainBridge(adapter=adapter, shop_cfg_path=Path("test.yaml"), cooldown_s=cooldown_s)
        mocks = {"orch": mock_orch_cls.return_value, "aq": mock_aq, "adapter": adapter,
                 "gate_mod": fake_iqg, "models_mod": fake_mdl, "rg_mod": fake_rg}
        try:
            yield bridge, mocks
        finally:
            bridge.shutdown()

def _sess(sid="s1", name="买家A"):
    s = MagicMock(); s.session_id = sid; s.buyer_name = name; return s

class TestHappy(unittest.TestCase):
    def test_normal_invokes_orch(self):
        with _bridge_ctx() as (b, m):
            b.handle_mobile_message(_sess(), "有货吗")
            m["orch"].handle_new_message_event.assert_called_once()
    def test_trigger_is_mobile(self):
        with _bridge_ctx() as (b, m):
            b.handle_mobile_message(_sess(), "发货多久")
            kw = m["models_mod"].NewMessageEvent.call_args.kwargs
            self.assertEqual(kw.get("trigger"), "mobile")
    def test_payload_buyer_text_and_name(self):
        with _bridge_ctx() as (b, m):
            b.handle_mobile_message(_sess(name="张三"), "包邮吗")
            p = m["models_mod"].NewMessageEvent.call_args.kwargs.get("payload", {})
            self.assertEqual(p.get("buyer_text"), "包邮吗")
            self.assertEqual(p.get("buyer_name"), "张三")
    def test_whitespace_stripped(self):
        with _bridge_ctx() as (b, m):
            b.handle_mobile_message(_sess(), "  运费  ")
            p = m["models_mod"].NewMessageEvent.call_args.kwargs.get("payload", {})
            self.assertEqual(p.get("buyer_text"), "运费")

class TestFiltering(unittest.TestCase):
    def test_empty_skipped(self):
        with _bridge_ctx() as (b, m):
            b.handle_mobile_message(_sess(), "")
            m["orch"].handle_new_message_event.assert_not_called()
    def test_whitespace_only_skipped(self):
        with _bridge_ctx() as (b, m):
            b.handle_mobile_message(_sess(), "   ")
            m["orch"].handle_new_message_event.assert_not_called()
    def test_discard_log_skips(self):
        with _bridge_ctx() as (b, m):
            gr = MagicMock(); gr.action = "discard_log"; gr.rule_name = "r"
            m["gate_mod"].check_buyer_input.return_value = gr
            b.handle_mobile_message(_sess(), "noise")
            m["orch"].handle_new_message_event.assert_not_called()
    def test_echo_skipped(self):
        with _bridge_ctx() as (b, m):
            m["rg_mod"].is_echo_or_noise_buyer_text.return_value = True
            b.handle_mobile_message(_sess(), "已读")
            m["orch"].handle_new_message_event.assert_not_called()
    def test_duplicate_skipped(self):
        with _bridge_ctx() as (b, m):
            m["rg_mod"].should_skip_duplicate_buyer.return_value = True
            b.handle_mobile_message(_sess(), "发货")
            m["orch"].handle_new_message_event.assert_not_called()
    def test_first_pass_second_skip(self):
        with _bridge_ctx(cooldown_s=9999) as (b, m):
            m["rg_mod"].should_skip_duplicate_buyer.return_value = False
            b.handle_mobile_message(_sess(), "有货吗")
            self.assertEqual(m["orch"].handle_new_message_event.call_count, 1)
            m["rg_mod"].should_skip_duplicate_buyer.return_value = True
            b.handle_mobile_message(_sess(), "有货吗")
            self.assertEqual(m["orch"].handle_new_message_event.call_count, 1)

class TestAnchorCheck(unittest.TestCase):
    def test_match_sends(self):
        with _bridge_ctx() as (b, m):
            a = m["adapter"]
            a.get_current_buyer_anchor.return_value = "买家A"
            a.send_text.return_value = True
            self.assertTrue(b.send_with_anchor_check("买家A", "好"))
            a.send_text.assert_called_once_with("好")
    def test_empty_anchor_sends(self):
        with _bridge_ctx() as (b, m):
            a = m["adapter"]
            a.get_current_buyer_anchor.return_value = ""
            a.send_text.return_value = True
            self.assertTrue(b.send_with_anchor_check("买家A", "好"))
    def test_mismatch_blocks(self):
        with _bridge_ctx() as (b, m):
            a = m["adapter"]
            a.get_current_buyer_anchor.return_value = "完全不同Z"
            self.assertFalse(b.send_with_anchor_check("买家A", "回复"))
            a.send_text.assert_not_called()
    def test_empty_expected_sends(self):
        with _bridge_ctx() as (b, m):
            a = m["adapter"]
            a.get_current_buyer_anchor.return_value = "任意买家"
            a.send_text.return_value = True
            self.assertTrue(b.send_with_anchor_check("", "你好"))
    def test_partial_match_sends(self):
        with _bridge_ctx() as (b, m):
            a = m["adapter"]
            a.get_current_buyer_anchor.return_value = "买家A（VIP）"
            a.send_text.return_value = True
            self.assertTrue(b.send_with_anchor_check("买家A", "好的"))

class TestLifecycle(unittest.TestCase):
    def test_executor_is_daemon(self):
        with _bridge_ctx() as (b, _):
            self.assertTrue(b._executor_thread.daemon)
    def test_executor_alive_after_init(self):
        with _bridge_ctx() as (b, _):
            self.assertTrue(b._executor_thread.is_alive())
    def test_shutdown_sets_stop(self):
        with _bridge_ctx() as (b, _):
            b.shutdown()
            self.assertTrue(b._executor_stop.is_set())

if __name__ == "__main__":
    unittest.main()
