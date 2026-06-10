"""从 evolution_rules.json 生成前台组句可用的策略块（再次过安检）。"""

from __future__ import annotations

import json
from pathlib import Path

from apps.core.runtime_paths import default_evolution_rules_path
from apps.core.shadow.safety_guard import rule_passes_safety_filter

_cache_key: tuple[str, float] | None = None
_cached_block: str = ""


def clear_shadow_evolution_prompt_cache() -> None:
    """测试或热重载时清空内存缓存。"""
    global _cache_key, _cached_block
    _cache_key, _cached_block = None, ""


def load_shadow_evolution_prompt_block(
    *,
    path: Path | None = None,
    max_rules: int = 16,
    max_chars: int = 4000,
) -> str:
    """
    读取 configs/shadow/evolution_rules.json，过滤后拼成一段 user 侧补充说明。
    无规则或读失败返回空串。按文件 mtime 做简单缓存。
    """
    global _cache_key, _cached_block
    p = path or default_evolution_rules_path()
    try:
        st = p.stat()
        key = (str(p.resolve()), float(st.st_mtime))
    except OSError:
        key = ("", 0.0)

    if _cache_key == key:
        return _cached_block

    if not p.is_file():
        _cache_key, _cached_block = key, ""
        return ""

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        _cache_key, _cached_block = key, ""
        return ""

    rules = raw.get("rules") if isinstance(raw, dict) else None
    if not isinstance(rules, list):
        _cache_key, _cached_block = key, ""
        return ""

    chunks: list[str] = []
    used = 0
    for r in rules:
        if used >= max_rules:
            break
        if not isinstance(r, dict):
            continue
        if not rule_passes_safety_filter(r):
            continue
        tr = str(r.get("trigger", "")).strip()
        stg = str(r.get("strategy", "")).strip()
        ex = str(r.get("few_shot_example", "")).strip()
        if not tr and not stg:
            continue
        lines: list[str] = []
        if tr:
            lines.append(f"- 触发：{tr}")
        if stg:
            lines.append(f"  策略：{stg}")
        if ex:
            lines.append(f"  示例语气：{ex}")
        chunk = "\n".join(lines)
        sep = "\n\n"
        projected = len(sep.join(chunks)) + len(chunk) + (len(sep) if chunks else 0)
        if projected > max_chars and chunks:
            break
        chunks.append(chunk)
        used += 1

    if not chunks:
        _cache_key, _cached_block = key, ""
        return ""

    header = (
        "【影子学习沉淀策略（仅作口吻与步骤偏好参考）】\n"
        "以下不是知识库事实。若与「知识库摘录」或商品信息冲突，一律以知识库为准；"
        "不得据此编造价格、库存、链接或承诺。"
    )
    block = header + "\n\n" + "\n\n".join(chunks)
    if len(block) > max_chars:
        block = block[: max_chars - 3].rstrip() + "…"
    _cache_key, _cached_block = key, block
    return block
