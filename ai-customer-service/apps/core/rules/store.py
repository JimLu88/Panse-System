from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from apps.core.runtime_paths import configs_dir


def default_rules_path(project_root: Path | None = None) -> Path:
    if project_root is not None:
        return project_root / "configs" / "rules" / "reply_rules.yaml"
    return configs_dir() / "rules" / "reply_rules.yaml"


def load_rules_yaml_text(path: Path | None = None) -> str:
    p = path or default_rules_path()
    if not p.is_file():
        return ""
    return p.read_text(encoding="utf-8")


def save_rules_yaml_text(text: str, path: Path | None = None) -> None:
    p = path or default_rules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


def validate_rules_yaml(text: str) -> tuple[bool, str | None, Any | None]:
    """
    Returns (ok, error_message, parsed_or_none).
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return False, f"YAML 语法错误：{e}", None
    if data is None:
        return True, None, {}
    if not isinstance(data, dict):
        return False, "根节点必须是映射（例如 version / rules）", None
    rules = data.get("rules")
    if rules is not None and not isinstance(rules, list):
        return False, "字段 rules 必须是列表", None
    return True, None, data


def parse_rules_yaml(text: str) -> dict[str, Any]:
    ok, err, data = validate_rules_yaml(text)
    if not ok or data is None:
        raise ValueError(err or "invalid yaml")
    return data
