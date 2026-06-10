"""固定话术与策略文案（与 Jim 介入流程对齐）。

兜底话术现支持通过 configs/query_rewrite.yaml → fallback_phrases 节在线编辑。
下面的常量仅作为 yaml 缺失时的硬编码兜底。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# ---------- 硬编码默认值（yaml 缺失时兜底） ----------

# 转人工前必发（由 SOOTHE_WAIT 或 SEND_TEXT 发送）
HANDOFF_SOOTHE_LINE = "我帮您确认看看，您稍等呢~"

# 补单客户默认（若无 kb_entries entry_type=replenish）
DEFAULT_REPLENISH_REPLY = "老客户回购这边给您优先安排～需要同款直接说尺寸规格，我帮您备注。"

# 欢迎语默认
DEFAULT_WELCOME_GREETING = "您好，在的呢～"

# 多客户批量安抚
DEFAULT_BATCH_SOOTHE = "亲，稍等一下哦～"

# v1.6.14：买家分享商品卡片时的固定话术（不强行 OCR 认型号，避免认错；
# 具体型号/尺寸的精确回答留给 v1.6.15 的「点卡片读URL的商品id→对应product_code」）
DEFAULT_PRODUCT_CARD_INQUIRY = "这款产品最近卖的挺好的呢，您想要了解什么呢？"

# v1.6.27：泛问材质/尺寸/颜色/规格但未指定产品、知识库又匹配不到时，反问澄清是哪款（不转人工）
DEFAULT_SPEC_CLARIFY = "您说的哪一款呢？方便发下产品链接么？或者说一下产品名称，我来帮您看下呢~"


# ---------- 运行时加载 / 保存 ----------

@dataclass
class FallbackPhrases:
    """通用兜底话术（与 yaml fallback_phrases 节一一对应）。"""
    welcome_greeting: str = DEFAULT_WELCOME_GREETING
    handoff_soothe: str = HANDOFF_SOOTHE_LINE
    replenish_reply: str = DEFAULT_REPLENISH_REPLY
    batch_soothe: str = DEFAULT_BATCH_SOOTHE
    product_card_inquiry: str = DEFAULT_PRODUCT_CARD_INQUIRY
    spec_clarify: str = DEFAULT_SPEC_CLARIFY


def _yaml_path() -> Path:
    from apps.core.runtime_paths import configs_dir
    return configs_dir() / "query_rewrite.yaml"


def load_fallback_phrases() -> FallbackPhrases:
    """从 query_rewrite.yaml → fallback_phrases 读取；缺失键用硬编码默认值。"""
    try:
        import yaml
        raw = yaml.safe_load(_yaml_path().read_text(encoding="utf-8")) or {}
    except Exception:
        return FallbackPhrases()
    fp = raw.get("fallback_phrases") if isinstance(raw.get("fallback_phrases"), dict) else {}
    return FallbackPhrases(
        welcome_greeting=(fp.get("welcome_greeting") or "").strip() or DEFAULT_WELCOME_GREETING,
        handoff_soothe=(fp.get("handoff_soothe") or "").strip() or HANDOFF_SOOTHE_LINE,
        replenish_reply=(fp.get("replenish_reply") or "").strip() or DEFAULT_REPLENISH_REPLY,
        batch_soothe=(fp.get("batch_soothe") or "").strip() or DEFAULT_BATCH_SOOTHE,
        product_card_inquiry=(fp.get("product_card_inquiry") or "").strip() or DEFAULT_PRODUCT_CARD_INQUIRY,
        spec_clarify=(fp.get("spec_clarify") or "").strip() or DEFAULT_SPEC_CLARIFY,
    )


def save_fallback_phrases(phrases: FallbackPhrases) -> None:
    """将兜底话术写回 query_rewrite.yaml（保留其他键、注释）。"""
    path = _yaml_path()
    try:
        from ruamel.yaml import YAML
        ry = YAML()
        ry.preserve_quotes = True
        data = ry.load(path.read_text(encoding="utf-8")) or {}
        data["fallback_phrases"] = {
            "welcome_greeting": phrases.welcome_greeting,
            "handoff_soothe": phrases.handoff_soothe,
            "replenish_reply": phrases.replenish_reply,
            "batch_soothe": phrases.batch_soothe,
            "product_card_inquiry": phrases.product_card_inquiry,
            "spec_clarify": phrases.spec_clarify,
        }
        with open(path, "w", encoding="utf-8") as f:
            ry.dump(data, f)
    except ImportError:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        raw["fallback_phrases"] = {
            "welcome_greeting": phrases.welcome_greeting,
            "handoff_soothe": phrases.handoff_soothe,
            "replenish_reply": phrases.replenish_reply,
            "batch_soothe": phrases.batch_soothe,
        }
        path.write_text(yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
