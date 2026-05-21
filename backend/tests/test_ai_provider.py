"""ai_provider 抽象: anthropic + openai 兼容 分别能调通."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.services import ai_provider


def test_build_provider_anthropic():
    p = ai_provider.build_provider({"provider": "anthropic", "api_key": "k", "model": "m"})
    assert isinstance(p, ai_provider.AnthropicProvider)
    assert p.model == "m"


def test_build_provider_openai_default_base():
    p = ai_provider.build_provider({"provider": "openai", "api_key": "k", "model": "qwen"})
    assert isinstance(p, ai_provider.OpenAICompatibleProvider)
    assert p._endpoint() == "https://api.openai.com/v1/chat/completions"


def test_build_provider_openai_custom_base_strips_slash():
    p = ai_provider.build_provider({
        "provider": "openai", "api_key": "k", "model": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/",
    })
    assert p._endpoint() == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def test_build_provider_unknown_raises():
    with pytest.raises(ai_provider.AiUnavailable):
        ai_provider.build_provider({"provider": "gemini", "api_key": "k", "model": "m"})


def test_build_provider_missing_key_raises():
    with pytest.raises(ai_provider.AiUnavailable):
        ai_provider.build_provider({"provider": "anthropic", "api_key": "", "model": "m"})


def test_build_provider_missing_model_raises():
    with pytest.raises(ai_provider.AiUnavailable):
        ai_provider.build_provider({"provider": "openai", "api_key": "k", "model": ""})


def _fake_anthropic_resp(text="hi"):
    resp = MagicMock()
    resp.model = "claude-x"
    block = MagicMock(); block.type = "text"; block.text = text
    resp.content = [block]
    resp.usage = MagicMock(
        input_tokens=10, output_tokens=5,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    )
    return resp


def test_anthropic_chat_returns_text_and_usage():
    p = ai_provider.AnthropicProvider(api_key="k", model="claude-x")
    with patch.object(p, "_client") as mc:
        client = MagicMock()
        client.messages.create.return_value = _fake_anthropic_resp("好")
        mc.return_value = client
        r = p.chat(system="sys", user="hi", max_tokens=64)
    assert r.text == "好"
    assert r.input_tokens == 10 and r.output_tokens == 5
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_chat_with_image_sends_image_block():
    p = ai_provider.AnthropicProvider(api_key="k", model="claude-x")
    with patch.object(p, "_client") as mc:
        client = MagicMock()
        client.messages.create.return_value = _fake_anthropic_resp("ocr-result")
        mc.return_value = client
        r = p.chat_with_image(system="s", user="u", image_bytes=b"fakeimg", mime="image/png")
    assert r.text == "ocr-result"
    content = client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[1]["type"] == "text"


def test_openai_chat_parses_response():
    p = ai_provider.OpenAICompatibleProvider(api_key="k", model="qwen-vl-max",
                                             base_url="https://x.example/v1")
    fake = MagicMock(status_code=200)
    fake.json.return_value = {
        "model": "qwen-vl-max",
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8},
    }
    with patch("app.services.ai_provider.httpx.post", return_value=fake) as mp:
        r = p.chat(system="s", user="hi", max_tokens=64)
    assert r.text == "ok"
    assert r.input_tokens == 20
    assert r.output_tokens == 8
    body = json.loads(mp.call_args.kwargs["content"])
    assert body["model"] == "qwen-vl-max"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["content"] == "hi"


def test_openai_chat_with_image_sends_image_url_data_url():
    p = ai_provider.OpenAICompatibleProvider(api_key="k", model="qwen-vl-max")
    fake = MagicMock(status_code=200)
    fake.json.return_value = {
        "choices": [{"message": {"content": "ocr"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30},
    }
    with patch("app.services.ai_provider.httpx.post", return_value=fake) as mp:
        p.chat_with_image(system="s", user="u", image_bytes=b"\x89PNG\r\n", mime="image/png")
    body = json.loads(mp.call_args.kwargs["content"])
    user_msg = body["messages"][1]
    assert user_msg["role"] == "user"
    assert isinstance(user_msg["content"], list)
    parts = {p["type"] for p in user_msg["content"]}
    assert "text" in parts and "image_url" in parts
    img = next(p for p in user_msg["content"] if p["type"] == "image_url")
    assert img["image_url"]["url"].startswith("data:image/png;base64,")


def test_openai_chat_http_error_raises_ai_unavailable():
    p = ai_provider.OpenAICompatibleProvider(api_key="k", model="qwen-vl-max")
    fake = MagicMock(status_code=401, text='{"error":"unauthorized"}')
    with patch("app.services.ai_provider.httpx.post", return_value=fake):
        with pytest.raises(ai_provider.AiUnavailable) as ei:
            p.chat(system="", user="hi")
    assert "401" in str(ei.value)


def test_openai_chat_handles_content_blocks_list():
    """部分 provider (e.g. Doubao) 返回 content 是 list[dict] 而非 str."""
    p = ai_provider.OpenAICompatibleProvider(api_key="k", model="m")
    fake = MagicMock(status_code=200)
    fake.json.return_value = {
        "choices": [{"message": {"content": [{"type": "text", "text": "part1 "},
                                              {"type": "text", "text": "part2"}]}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    with patch("app.services.ai_provider.httpx.post", return_value=fake):
        r = p.chat(system="", user="x")
    assert r.text == "part1 part2"
