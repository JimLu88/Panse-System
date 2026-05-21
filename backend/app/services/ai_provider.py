"""AI Provider 统一抽象 (业务需求扩展).

支持两种 provider:
    - anthropic         使用 anthropic SDK (含 prompt caching + 自定义 base_url 走代理)
    - openai            OpenAI 兼容协议 (通义 Qwen-VL / 智谱 GLM-4V / 豆包 / 本地 vLLM 全适用)

调用方:
    p = build_provider({"provider": "anthropic", "api_key": "...", "model": "...", "base_url": ""})
    resp = p.chat(system=..., user=..., max_tokens=800)
    resp = p.chat_with_image(system=..., user=..., image_bytes=..., mime="image/jpeg", max_tokens=1500)

返回 AiResponse: text + 用量统计 + model 名。
不抛 — 上层捕 AiUnavailable / RuntimeError 后写日志。
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Optional

import httpx


@dataclass
class AiResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


class AiUnavailable(RuntimeError):
    """provider 未配置 / key 缺失 / 调用上游失败。"""


class AiProvider:
    name = "base"

    def __init__(self, *, api_key: str, model: str, base_url: str = "") -> None:
        if not api_key:
            raise AiUnavailable(f"{self.name} 未配置: API Key 为空")
        if not model:
            raise AiUnavailable(f"{self.name} 未配置: 模型名为空")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/") if base_url else ""

    def chat(self, *, system: str, user: str, max_tokens: int = 1024,
             cache_system: bool = True) -> AiResponse:
        raise NotImplementedError

    def chat_with_image(self, *, system: str, user: str, image_bytes: bytes,
                        mime: str = "image/jpeg", max_tokens: int = 2048) -> AiResponse:
        raise NotImplementedError


# ----------------------------- Anthropic ---------------------------------- #


class AnthropicProvider(AiProvider):
    name = "anthropic"

    def _client(self):
        import anthropic
        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return anthropic.Anthropic(**kwargs)

    def _usage(self, u) -> dict:
        return dict(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )

    def chat(self, *, system: str, user: str, max_tokens: int = 1024,
             cache_system: bool = True) -> AiResponse:
        client = self._client()
        sys_blocks = [{"type": "text", "text": system}]
        if cache_system and system:
            sys_blocks[0]["cache_control"] = {"type": "ephemeral"}
        try:
            resp = client.messages.create(
                model=self.model, max_tokens=max_tokens, system=sys_blocks,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:
            raise AiUnavailable(f"anthropic 调用失败: {e}") from e
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return AiResponse(text=text, model=resp.model, **self._usage(resp.usage))

    def chat_with_image(self, *, system: str, user: str, image_bytes: bytes,
                        mime: str = "image/jpeg", max_tokens: int = 2048) -> AiResponse:
        client = self._client()
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
            {"type": "text", "text": user},
        ]
        try:
            resp = client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=[{"type": "text", "text": system}] if system else None,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as e:
            raise AiUnavailable(f"anthropic vision 调用失败: {e}") from e
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return AiResponse(text=text, model=resp.model, **self._usage(resp.usage))


# ----------------------------- OpenAI 兼容 -------------------------------- #


class OpenAICompatibleProvider(AiProvider):
    """走 OpenAI Chat Completions 协议. 适配:

    - 阿里通义 https://dashscope.aliyuncs.com/compatible-mode/v1
    - 智谱 GLM https://open.bigmodel.cn/api/paas/v4
    - 豆包    https://ark.cn-beijing.volces.com/api/v3
    - DeepSeek https://api.deepseek.com/v1
    - 本地 vLLM / Ollama OpenAI-compat
    - OpenAI 官方 https://api.openai.com/v1
    """
    name = "openai"

    def _endpoint(self) -> str:
        base = self.base_url or "https://api.openai.com/v1"
        return f"{base}/chat/completions"

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _post(self, payload: dict, timeout: float = 60.0) -> dict:
        try:
            r = httpx.post(self._endpoint(), headers=self._headers(),
                           content=json.dumps(payload), timeout=timeout)
        except httpx.HTTPError as e:
            raise AiUnavailable(f"openai 兼容接口请求失败: {e}") from e
        if r.status_code >= 400:
            raise AiUnavailable(f"openai 兼容接口 {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except Exception as e:
            raise AiUnavailable(f"openai 兼容接口返回非 JSON: {r.text[:200]}") from e

    def _from_response(self, data: dict) -> AiResponse:
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise AiUnavailable(f"openai 兼容接口缺少 choices: {data}") from e
        if isinstance(text, list):  # 部分 provider 返回 content blocks
            text = "".join(b.get("text", "") for b in text if isinstance(b, dict))
        u = data.get("usage") or {}
        return AiResponse(
            text=text, model=data.get("model") or self.model,
            input_tokens=u.get("prompt_tokens", 0) or 0,
            output_tokens=u.get("completion_tokens", 0) or 0,
        )

    def chat(self, *, system: str, user: str, max_tokens: int = 1024,
             cache_system: bool = True) -> AiResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        return self._from_response(self._post({
            "model": self.model, "messages": messages, "max_tokens": max_tokens,
        }))

    def chat_with_image(self, *, system: str, user: str, image_bytes: bytes,
                        mime: str = "image/jpeg", max_tokens: int = 2048) -> AiResponse:
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        # OpenAI vision content blocks
        user_content = [
            {"type": "text", "text": user},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content})
        return self._from_response(self._post({
            "model": self.model, "messages": messages, "max_tokens": max_tokens,
        }, timeout=120.0))


# ----------------------------- 工厂 --------------------------------------- #


def build_provider(cfg: dict) -> AiProvider:
    """cfg = {"provider": "...", "api_key": "...", "model": "...", "base_url": "..."}"""
    p = (cfg.get("provider") or "anthropic").lower()
    if p == "anthropic":
        return AnthropicProvider(
            api_key=cfg.get("api_key") or "",
            model=cfg.get("model") or "",
            base_url=cfg.get("base_url") or "",
        )
    if p in ("openai", "openai-compat", "openai_compatible"):
        return OpenAICompatibleProvider(
            api_key=cfg.get("api_key") or "",
            model=cfg.get("model") or "",
            base_url=cfg.get("base_url") or "",
        )
    raise AiUnavailable(f"未知 provider: {p}")


SUPPORTED_PROVIDERS = (
    {"value": "anthropic", "label": "Anthropic Claude (官方 / 代理)",
     "model_hint": "claude-opus-4-7 / claude-sonnet-4-6 / claude-haiku-4-5-20251001",
     "base_url_hint": "留空走官方 https://api.anthropic.com — 代理填代理地址"},
    {"value": "openai", "label": "OpenAI 兼容 (Qwen / GLM / 豆包 / DeepSeek / 本地 vLLM)",
     "model_hint": "qwen-vl-max / glm-4v-plus / doubao-vision / gpt-4o-mini",
     "base_url_hint": "如 https://dashscope.aliyuncs.com/compatible-mode/v1"},
)
