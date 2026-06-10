"""
Shadow 插件安全边界：只读观测；禁止将「改价、收款」等策略写入 evolution_rules。
本包不 import uiautomation，不执行任何模拟点击。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from apps.core.runtime_paths import default_shadow_security_log

# 订单改价、小额收款等：规则文本命中即整段丢弃
PRICE_SENSITIVE_KEYWORDS = frozenset(
    (
        "改价",
        "订单金额",
        "优惠券",
        "收款码",
        "代付",
        "小额收款",
        "补差价",
        "退款",
        "实付",
        "修改金额",
        "改金额",
        "uiautomation",
        ".click(",
        "模拟点击",
        "自动点击",
    )
)


class PriceSensitiveViolation(Exception):
    """观测/演化流水线中检测到价格敏感语义。"""


def _combined_rule_text(rule: dict[str, Any]) -> str:
    parts: list[str] = []
    for k in ("trigger", "strategy", "few_shot_example"):
        v = rule.get(k)
        if isinstance(v, str):
            parts.append(v)
    return "\n".join(parts).lower()


def rule_passes_safety_filter(rule: dict[str, Any]) -> bool:
    """若规则含价格敏感或自动化点击指令，返回 False（不得写入 evolution_rules）。"""
    blob = _combined_rule_text(rule)
    if not blob.strip():
        return False
    for kw in PRICE_SENSITIVE_KEYWORDS:
        if kw.lower() in blob:
            return False
    return True


def append_security_log(message: str) -> None:
    p = default_shadow_security_log()
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}\n"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def merge_evolution_rules_file(*, path: Path, new_rules: list[dict[str, Any]]) -> int:
    """
    将通过安检的规则合并进 evolution_rules.json；返回实际写入条数。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(
            '{"version":1,"rules":[]}',
            encoding="utf-8",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {"version": 1, "rules": []}
    if not isinstance(raw, dict):
        raw = {"version": 1, "rules": []}
    existing = raw.get("rules")
    if not isinstance(existing, list):
        existing = []
    triggers = {
        str((r or {}).get("trigger", "")).strip()
        for r in existing
        if isinstance(r, dict)
    }
    added = 0
    for rule in new_rules:
        if not isinstance(rule, dict):
            continue
        if not rule_passes_safety_filter(rule):
            append_security_log(f"DROP_RULE safety_filter trigger={rule.get('trigger')!r}")
            continue
        tr = str(rule.get("trigger", "")).strip()
        if tr and tr in triggers:
            continue
        existing.append(
            {
                "trigger": str(rule.get("trigger", ""))[:400],
                "strategy": str(rule.get("strategy", ""))[:1200],
                "few_shot_example": str(rule.get("few_shot_example", ""))[:800],
            }
        )
        if tr:
            triggers.add(tr)
        added += 1
    raw["rules"] = existing
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return added
