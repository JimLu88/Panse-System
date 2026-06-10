"""Shadow 影子学习插件：观测模式（只读记录 + 规则演化），禁止 UIAutomation 改价类操作。"""

from __future__ import annotations

from apps.core.shadow.observer import ShadowObserver
from apps.core.shadow.rules_prompt import (
    clear_shadow_evolution_prompt_cache,
    load_shadow_evolution_prompt_block,
)
from apps.core.shadow.safety_guard import (
    PRICE_SENSITIVE_KEYWORDS,
    PriceSensitiveViolation,
    merge_evolution_rules_file,
    rule_passes_safety_filter,
)

__all__ = [
    "EvolveEngine",
    "ShadowObserver",
    "PRICE_SENSITIVE_KEYWORDS",
    "PriceSensitiveViolation",
    "clear_shadow_evolution_prompt_cache",
    "load_shadow_evolution_prompt_block",
    "merge_evolution_rules_file",
    "rule_passes_safety_filter",
]


def __getattr__(name: str):
    if name == "EvolveEngine":
        from apps.core.shadow.evolve import EvolveEngine

        return EvolveEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
