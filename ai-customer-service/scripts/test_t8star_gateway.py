"""
对 T8Star 类中转进行 LiteLLM 探活（需环境变量 T8STAR_API_KEY 或各厂商 KEY）。

用法（项目根目录）:
  set T8STAR_API_KEY=你的钥
  python scripts/test_t8star_gateway.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 包根：项目根
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from apps.core.ai.llm_client import litellm_completion_text  # noqa: E402
from apps.core.configs.base_settings import (  # noqa: E402
    SUGGESTED_LLM_API_BASE,
    BaseSettings,
)


def _key() -> str:
    return (
        os.environ.get("T8STAR_API_KEY", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        or os.environ.get("GEMINI_API_KEY", "").strip()
    )


def main() -> int:
    k = _key()
    if not k:
        print(
            "未设置密钥：请设置环境变量 T8STAR_API_KEY（或 OPENAI_API_KEY / "
            "ANTHROPIC_API_KEY / GEMINI_API_KEY）后重试。"
        )
        return 2

    base = os.environ.get("LLM_API_BASE", SUGGESTED_LLM_API_BASE).strip()
    st = BaseSettings(
        openai_api_key=k,
        anthropic_api_key=k,
        gemini_api_key=k,
        llm_api_base=base,
    )

    cases = [
        ("openai/gpt-4o-mini", "OpenAI 兼容路径"),
        ("openai/deepseek-v4-flash", "中转要求的 DeepSeek 模型名"),
        ("openai/claude-sonnet-4-6-thinking", "Claude 经 OpenAI 兼容中转"),
    ]

    for model, note in cases:
        print(f"\n--- {model} ({note}) api_base={base!r} ---")
        try:
            text = litellm_completion_text(
                settings=st,
                model=model,
                system="只回复单词 OK。",
                user="ping",
                max_tokens=32,
                temperature=0.0,
                deep_analysis=model.startswith("anthropic/"),
            )
            print("OK:", repr(text[:200]))
        except Exception as e:
            print("FAIL:", type(e).__name__, e)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
