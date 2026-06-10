from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import yaml

# （内部值, 界面显示给操作员看的说明）
MATCH_OPTIONS: list[tuple[str, str]] = [
    ("customization_in_scope", "客户在商品允许的定制范围内（可直接答复可以做）"),
    ("customization_not_in_scope", "客户诉求超出允许的定制范围（需转工厂/技术确认）"),
]


def match_label_for_type(internal: str) -> str:
    for key, label in MATCH_OPTIONS:
        if key == internal:
            return label
    return internal


def match_type_from_label(label: str) -> str | None:
    for key, l in MATCH_OPTIONS:
        if l == label:
            return key
    return None


@dataclass
class TemplateRow:
    template_id: str
    """英文代号，对应 YAML templates_ref 的键。"""
    body: str
    """发给客户的完整话术。"""


@dataclass
class RuleRow:
    rule_id: str
    enabled: bool
    description: str
    match_type: str
    product_field: str
    reply_template_id: str


@dataclass
class RulesBundle:
    version: int = 1
    templates: list[TemplateRow] = field(default_factory=list)
    rules: list[RuleRow] = field(default_factory=list)


def default_bundle() -> RulesBundle:
    """首次打开或文件为空时的内置示例。"""
    return RulesBundle(
        version=1,
        templates=[
            TemplateRow(
                "tpl_custom_ok",
                "可以的亲，您说的这项在我们支持定制范围内，细节我帮您备注～",
            ),
            TemplateRow(
                "tpl_custom_escalate",
                "您的定制需求比较高呢，我需要和工厂技术确认后再回复您，您稍等～",
            ),
        ],
        rules=[
            RuleRow(
                rule_id="customization-in-scope",
                enabled=True,
                description="定制在允许范围内时，用可直接答复的话术",
                match_type="customization_in_scope",
                product_field="customizable_tags",
                reply_template_id="tpl_custom_ok",
            ),
            RuleRow(
                rule_id="customization-out-of-scope",
                enabled=True,
                description="超出允许范围时，用语术转工厂确认",
                match_type="customization_not_in_scope",
                product_field="customizable_tags",
                reply_template_id="tpl_custom_escalate",
            ),
        ],
    )


def parse_bundle_yaml(text: str) -> RulesBundle:
    raw = (text or "").strip()
    if not raw:
        return default_bundle()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return default_bundle()
    version = int(data.get("version") or 1)
    tr = data.get("templates_ref") or {}
    templates: list[TemplateRow] = []
    if isinstance(tr, dict):
        for k, v in tr.items():
            templates.append(TemplateRow(str(k), str(v or "")))
    rules_out: list[RuleRow] = []
    rlist = data.get("rules") or []
    if isinstance(rlist, list):
        for i, r in enumerate(rlist):
            if not isinstance(r, dict):
                continue
            mid = r.get("match") or {}
            if not isinstance(mid, dict):
                mid = {}
            rules_out.append(
                RuleRow(
                    rule_id=str(r.get("id") or f"rule-{i + 1}"),
                    enabled=bool(r.get("enabled", True)),
                    description=str(r.get("description") or ""),
                    match_type=str(mid.get("type") or "customization_in_scope"),
                    product_field=str(mid.get("product_field") or "customizable_tags"),
                    reply_template_id=str(r.get("reply_template_id") or ""),
                )
            )
    if not templates and not rules_out:
        return default_bundle()
    return RulesBundle(version=version, templates=templates, rules=rules_out)


def serialize_bundle_yaml(bundle: RulesBundle) -> str:
    """生成与引擎兼容的 YAML（无文件头注释，便于校验）。"""
    templates_ref = {t.template_id: t.body for t in bundle.templates}
    rules: list[dict] = []
    for r in bundle.rules:
        rules.append(
            {
                "id": r.rule_id,
                "enabled": r.enabled,
                "description": r.description,
                "match": {
                    "type": r.match_type,
                    "product_field": r.product_field or "customizable_tags",
                },
                "reply_template_id": r.reply_template_id,
            }
        )
    data = {
        "version": bundle.version,
        "templates_ref": templates_ref,
        "rules": rules,
    }
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def new_template_id(prefix: str = "tpl") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def new_rule_id() -> str:
    return f"rule-{uuid.uuid4().hex[:8]}"
