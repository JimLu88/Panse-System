from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# 后续：从 SQLite risk_dictionaries 读品牌/店铺级词表
_DEFAULT_FLUFF: tuple[str, ...] = (
    "亲爱的客户",
    "感谢您的耐心等待",
    "很高兴为您服务",
    "竭诚为您服务",
)

# LLM 输出后复检（瞎报价格 / 极限承诺等），命中则拦截整段发送并转人工
_LLM_BLOCK_PATTERNS = (
    r"假一罚万",
    r"必定退款",
    r"百分百正品",
)

# 买家原话命中则立即转人工（收窄：仅资金/私下收款/改实付等，不把普通议价算入）
_MONEY_HARD_BLOCK_PATTERNS = (
    r"收款码",
    r"私下转(?:账|款)?",
    r"改(?:一下)?实付",
    r"代付",
    r"(?:支付宝|微信).*?(?:收款|转账|码)",
    r"银行卡号",
    r"对公账户",
)


def check_money_hard_block_buyer(text: str) -> RiskCheckResult:
    t = (text or "").strip()
    if not t:
        return RiskCheckResult(allowed=True, reason=None)
    for pat in _MONEY_HARD_BLOCK_PATTERNS:
        if re.search(pat, t, flags=re.I):
            return RiskCheckResult(allowed=False, reason=f"资金绝对禁区：{pat}")
    return RiskCheckResult(allowed=True, reason=None)

# 买家原话命中则立即转人工（收窄：仅资金/私下结算类，不把一般议价算在内）
_MONEY_HARD_BLOCK = (
    r"收款码",
    r"私下转[账帐]",
    r"改.*实付",
    r"代付",
    r"支付宝(账号|收款)",
    r"微信(收款|转账).*码",
    r"银行卡号",
)


@dataclass(frozen=True, slots=True)
class RiskCheckResult:
    allowed: bool
    reason: str | None = None


def load_phrase_blacklist(conn: sqlite3.Connection, *, brand_id: str, shop_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT phrase FROM risk_dictionaries
        WHERE brand_id = ? AND shop_id = ? AND dict_type = 'phrase_blacklist' AND enabled = 1
        """,
        (brand_id, shop_id),
    ).fetchall()
    out = [str(r[0]).strip() for r in rows if r and str(r[0]).strip()]
    for x in _DEFAULT_FLUFF:
        if x not in out:
            out.append(x)
    return out


def format_blacklist_for_prompt(phrases: list[str]) -> str:
    if not phrases:
        return ""
    return "\n".join(f"- {p}" for p in phrases[:80])


def check_outbound_text(
    text: str,
    *,
    brand_id: str = "",
    shop_code: str = "",
    extra_banned_phrases: tuple[str, ...] | list[str] | None = None,
) -> RiskCheckResult:
    """
    发送前最后一道闸：默认客套黑名单；若传入 extra_banned_phrases 则仅用该列表（已含 DB 合并结果）。
    """
    t = (text or "").strip()
    if not t:
        return RiskCheckResult(allowed=True, reason=None)
    if extra_banned_phrases is not None:
        banned = list(extra_banned_phrases)
    else:
        banned = list(_DEFAULT_FLUFF)
    for w in banned:
        if w and w in t:
            return RiskCheckResult(allowed=False, reason=f"命中客套/黑名单用语：{w}")
    return RiskCheckResult(allowed=True, reason=None)


def check_money_hard_block_buyer(text: str) -> RiskCheckResult:
    """买家消息：资金绝对禁区（收窄版），命中则禁止自动回复路径。"""
    t = (text or "").strip()
    if not t:
        return RiskCheckResult(allowed=True, reason=None)
    for pat in _MONEY_HARD_BLOCK:
        if re.search(pat, t):
            return RiskCheckResult(allowed=False, reason=f"资金硬拦：{pat}")
    return RiskCheckResult(allowed=True, reason=None)


def check_llm_segments(segments: list[str]) -> RiskCheckResult:
    """LLM JSON 分段发送前的底线扫描。"""
    joined = "\n".join(s for s in segments if s)
    for pat in _LLM_BLOCK_PATTERNS:
        if re.search(pat, joined):
            return RiskCheckResult(allowed=False, reason=f"LLM 话术命中底线模式：{pat}")
    return RiskCheckResult(allowed=True, reason=None)
