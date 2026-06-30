"""AI 服务用 mock，不真打 Anthropic API。"""
from unittest.mock import MagicMock, patch

import pytest

import importlib.util as _ilu

from app.models.ai import AiChatLog
from app.models.exception import DataException
from app.services import ai_assistant

# "无 key" 路径需 anthropic 已安装才走到"缺 key"分支(docker 镜像有; 本机为可选依赖, 缺则优雅跳过)
_skip_no_anthropic = pytest.mark.skipif(
    _ilu.find_spec("anthropic") is None,
    reason="anthropic 未安装(可选依赖, docker 镜像有)")


def _fake_anthropic_response(text="【发生了什么】测试。", in_tok=100, out_tok=50, cache_read=80):
    """构造一个 anthropic.messages.create() 的返回。"""
    resp = MagicMock()
    resp.model = "claude-sonnet-4-6"
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp.content = [block]
    resp.usage = MagicMock(
        input_tokens=in_tok, output_tokens=out_tok,
        cache_read_input_tokens=cache_read, cache_creation_input_tokens=0,
    )
    return resp


def test_diagnose_writes_log_and_back_to_exception(db_session):
    exc = DataException(
        source_table="orders", source_pk="O1",
        exception_type="dangling_product_code",
        severity="error",
        description="订单引用了不存在的产品编码 X",
    )
    db_session.add(exc)
    db_session.flush()

    with patch.object(ai_assistant, "_client") as mocked_client:
        client = MagicMock()
        client.messages.create.return_value = _fake_anthropic_response(text="diagnose ok")
        mocked_client.return_value = client

        log, ai = ai_assistant.diagnose_exception(db_session, exc.id)

    assert ai is not None
    assert ai.text == "diagnose ok"
    assert log.action_type == "diagnose"
    assert log.related_exception_id == exc.id
    assert log.input_tokens == 100
    assert log.output_tokens == 50
    assert log.cache_read_tokens == 80
    # 回写到 exception
    db_session.refresh(exc)
    assert exc.ai_analysis == "diagnose ok"


@_skip_no_anthropic
def test_diagnose_no_api_key_returns_log_with_error(db_session, monkeypatch):
    monkeypatch.setattr(ai_assistant.settings, "anthropic_api_key", "")
    exc = DataException(
        source_table="materials", source_pk="AC-1001",
        exception_type="custom_material_missing_price",
        severity="warning", description="缺价",
    )
    db_session.add(exc)
    db_session.flush()
    log, ai = ai_assistant.diagnose_exception(db_session, exc.id)
    assert ai is None
    assert log.error is not None
    assert "ANTHROPIC_API_KEY" in log.error


def test_diagnose_missing_exception_raises(db_session):
    with pytest.raises(ValueError):
        ai_assistant.diagnose_exception(db_session, 999_999)


def test_chat_success(db_session):
    with patch.object(ai_assistant, "_client") as mocked_client:
        client = MagicMock()
        client.messages.create.return_value = _fake_anthropic_response(text="chat answer")
        mocked_client.return_value = client

        log, ai = ai_assistant.chat(db_session, user_message="问个问题", session_id="s1")

    assert ai is not None
    assert ai.text == "chat answer"
    assert log.action_type == "chat"
    assert log.session_id == "s1"
    assert log.input_tokens == 100


@_skip_no_anthropic
def test_chat_no_api_key(db_session, monkeypatch):
    monkeypatch.setattr(ai_assistant.settings, "anthropic_api_key", "")
    log, ai = ai_assistant.chat(db_session, user_message="hi")
    assert ai is None
    assert "ANTHROPIC_API_KEY" in log.error


def test_chat_handles_upstream_error(db_session):
    with patch.object(ai_assistant, "_client") as mocked_client:
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("api blew up")
        mocked_client.return_value = client

        log, ai = ai_assistant.chat(db_session, user_message="hi")

    assert ai is None
    assert "api blew up" in log.error


def test_call_claude_uses_cached_system_prompt(db_session):
    with patch.object(ai_assistant, "_client") as mocked_client:
        client = MagicMock()
        client.messages.create.return_value = _fake_anthropic_response()
        mocked_client.return_value = client

        ai_assistant.chat(db_session, user_message="x")
        args, kwargs = client.messages.create.call_args
        system = kwargs["system"]
        # 第一块是缓存的系统提示
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert "畔色孚格 ERP" in system[0]["text"]
        assert kwargs["model"] == "claude-sonnet-4-6"


def test_is_configured(monkeypatch):
    monkeypatch.setattr(ai_assistant.settings, "anthropic_api_key", "")
    assert ai_assistant.is_configured() is False
    monkeypatch.setattr(ai_assistant.settings, "anthropic_api_key", "sk-ant-test")
    assert ai_assistant.is_configured() is True
