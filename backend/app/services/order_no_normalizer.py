"""平台订单号还原 — 把支付宝各账户「关联订单号」里带前后缀/分隔符的值, 按多套规则
还原成淘宝平台订单号(19 位)。

设计 (按用户要求): 多规则、按序检索, 命中即停; 都不命中 → 由调用方报异常待补规则。
只收录「已用真实订单表验证过」的规则, 不用会错配的猜测规则 (如盲抓嵌入19位/任意字母前缀)。

已验证规则:
  1) 平台订单号列直给: 爱群号(9c)自带「平台订单号」列且为 19 位 → 直接用。
  2) 本身即19位: 关联订单号本身就是 19 位淘宝单号 → 直接用。
  3) 企业号T200P: 「T200P{中段数字} {尾段数字}」→ 去 T200P 与空格拼接。
     实测 9a 432 条符合, 418 条命中订单表。

未覆盖格式 (爱群号28位 / 佳宝号"日期 11位" / 主力号P长串 / HJCAEB== 等) 暂无可靠规则,
交由调用方报异常; 待用户确认含义后, 在 _RULES 里追加一条即可 (一行一规则, 易扩展)。
"""
from __future__ import annotations

import re
from typing import Callable, Optional

_RE_19 = re.compile(r"^\d{19}$")

# 每条规则: (规则名, fn(related: str, provided: Optional[str]) -> Optional[str])
RuleFn = Callable[[str, Optional[str]], Optional[str]]


def _rule_provided(related: str, provided: Optional[str]) -> Optional[str]:
    """爱群号等自带「平台订单号」列, 且为 19 位 → 直接采用。"""
    return provided if (provided and _RE_19.match(provided)) else None


def _rule_raw_19(related: str, provided: Optional[str]) -> Optional[str]:
    """关联订单号本身就是 19 位淘宝单号。"""
    return related if _RE_19.match(related) else None


def _rule_t200p(related: str, provided: Optional[str]) -> Optional[str]:
    """企业号: 'T200P{中段} {尾段}' → 去前缀 T200P 与空格, 拼成纯数字平台订单号。
    兼容已去空格的输入 (导入时关联订单号会先去全部空白)。"""
    if not related.startswith("T200P"):
        return None
    body = related[5:].replace(" ", "").strip()
    return body if (body.isdigit() and len(body) >= 15) else None


# 规则按序尝试, 命中即停。新格式确认后在此追加一条即可。
_RULES: list[tuple[str, RuleFn]] = [
    ("平台订单号列直给", _rule_provided),
    ("本身即19位", _rule_raw_19),
    ("企业号T200P", _rule_t200p),
]


def resolve_with_rule(
    related: object, provided: object = None,
) -> tuple[Optional[str], Optional[str]]:
    """返回 (还原出的平台订单号, 命中的规则名); 都不命中返回 (None, None)。"""
    rel = "" if related is None else str(related).strip()
    prov = None if provided is None else str(provided).strip()
    if not rel and not prov:
        return None, None
    for name, fn in _RULES:
        out = fn(rel, prov)
        if out:
            return out, name
    return None, None


def resolve_platform_order_no(related: object, provided: object = None) -> Optional[str]:
    """便捷版: 只返回还原出的平台订单号 (无则 None)。"""
    return resolve_with_rule(related, provided)[0]
