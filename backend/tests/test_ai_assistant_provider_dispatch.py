"""ai_assistant 在 db 里写了 ai_diagnose_* 时应走 provider 抽象, 不再走 _client.

不调真的 API — 用 patch build_provider 验证派发。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.models.exception import DataException
from app.services import ai_assistant, settings_service
from app.services.ai_provider import AiResponse


def test_diagnose_uses_provider_when_db_config_present(db_session):
    settings_service.set_value(db_session, "ai_diagnose_provider", "openai")
    settings_service.set_value(db_session, "ai_diagnose_api_key", "test-key")
    settings_service.set_value(db_session, "ai_diagnose_model", "qwen-vl-max")
    settings_service.set_value(db_session, "ai_diagnose_base_url",
                               "https://dashscope.aliyuncs.com/compatible-mode/v1")

    exc = DataException(
        source_table="orders", source_pk="O1",
        exception_type="dangling_product_code",
        severity="error", description="订单引用了不存在的产品编码 X",
    )
    db_session.add(exc)
    db_session.flush()

    fake_provider = MagicMock()
    fake_provider.chat.return_value = AiResponse(text="diagnose via openai", model="qwen-vl-max",
                                                 input_tokens=22, output_tokens=11)
    with patch.object(ai_assistant, "build_provider", return_value=fake_provider) as bp:
        log, ai = ai_assistant.diagnose_exception(db_session, exc.id)

    bp.assert_called_once()
    cfg = bp.call_args.args[0]
    assert cfg["provider"] == "openai"
    assert cfg["api_key"] == "test-key"
    assert cfg["model"] == "qwen-vl-max"
    assert ai is not None and ai.text == "diagnose via openai"
    assert log.model == "qwen-vl-max"
    assert log.input_tokens == 22


def test_diagnose_falls_back_to_client_when_db_empty(db_session):
    """没在 DB 里配置时, 应走旧的 _client 路径 (向后兼容)."""
    exc = DataException(
        source_table="orders", source_pk="O2",
        exception_type="dangling_product_code",
        severity="error", description="不同的描述 Y",
    )
    db_session.add(exc)
    db_session.flush()

    with patch.object(ai_assistant, "build_provider") as bp, \
         patch.object(ai_assistant, "_client") as mc:
        client = MagicMock()
        resp = MagicMock()
        resp.model = "claude-sonnet-4-6"
        b = MagicMock(); b.type = "text"; b.text = "ok"
        resp.content = [b]
        resp.usage = MagicMock(input_tokens=1, output_tokens=1,
                               cache_read_input_tokens=0, cache_creation_input_tokens=0)
        client.messages.create.return_value = resp
        mc.return_value = client

        log, ai = ai_assistant.diagnose_exception(db_session, exc.id)

    bp.assert_not_called()
    assert ai is not None and ai.text == "ok"


def test_is_configured_via_db(db_session, monkeypatch):
    monkeypatch.setattr(ai_assistant.settings, "anthropic_api_key", "")
    assert ai_assistant.is_configured(db_session) is False
    settings_service.set_value(db_session, "ai_diagnose_api_key", "x")
    assert ai_assistant.is_configured(db_session) is True
