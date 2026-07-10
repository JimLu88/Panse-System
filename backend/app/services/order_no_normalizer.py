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


def _rule_19_underscore(related: str, provided: Optional[str]) -> Optional[str]:
    """淘宝『订单号_子订单号』(企业号订单级结算行常见, 如 4502117053076082343_386256494716764259):
    取下划线前段, 为 19 位纯数字即采用 (用户 2026-06-24 确认: 前段就是平台订单号)。"""
    if "_" not in related:
        return None
    head = related.split("_", 1)[0].strip()
    return head if _RE_19.match(head) else None


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
    ("订单号_子订单号取前19位", _rule_19_underscore),
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


# ── 非淘宝订单引用识别 (2026-06-24, 用户 #7) ─────────────────────────────────
# 个人号(主力号/个体户私账等)的「关联订单号」列里, 大量值压根不是淘宝平台订单号, 而是各类
# 真实业务的对方流水号: 安装费(万师傅/闪装)、亲情卡(李爱群)、推广充值(阿里妈妈)、拼多多、
# 快递运费、银行提现、缴税、收钱码收款、员工代付等。它们【永远】还原不出 19 位淘宝单号,
# 不该被当成"格式未识别待补规则"刷异常。按对方/摘要/号码形态识别, 命中即视为"非订单引用"。
_NON_ORDER_COUNTERPARTY_KW = (
    "万师傅", "闪装", "阿里妈妈", "李爱群", "Klossy", "拼多多",
    "中通", "顺丰", "圆通", "韵达", "申通", "极兔", "邮政", "京东物流",
    "银行", "税务", "十足",
)
_NON_ORDER_REMARK_KW = (
    "亲情卡", "万相台", "扫码充值", "充值", "收钱码", "提现", "缴税", "缴费",
    "散单运费", "运费", "代付", "商户单号", "理财", "余额宝", "过户",
)


def is_non_order_reference(
    related: object, counterparty: object = None, remark: object = None,
) -> bool:
    """该「关联订单号」是否明显【不是】淘宝平台订单 (个人号的安装费/亲情卡/推广/拼多多/快递/
    提现/代付/缴税/收钱码等)。命中则不应计入"待补规则"异常。

    判据 (任一命中即 True):
      1) 对方公司名含已知非订单关键词 (万师傅/阿里妈妈/李爱群/拼多多/快递/银行/税务…);
      2) 摘要含已知非订单关键词 (亲情卡/万相台充值/收钱码/提现/缴税/运费/代付…);
      3) 号码形态非淘宝单号: 以 'P' 开头(万师傅/闪装单号) 或 含字母/+/=//(base64 类推广号);
      4) 支付宝内部号形态: 以日期(20xxMMDD)开头的 ≥25 位纯数字。
    纯数字但位数≠19 且不命中 4) 的不在此判 —— 交由调用方结合账户判断, 避免误伤个别 18 位真单。
    """
    cp = "" if counterparty is None else str(counterparty)
    rm = "" if remark is None else str(remark)
    rel = "" if related is None else str(related).strip()
    if any(k in cp for k in _NON_ORDER_COUNTERPARTY_KW):
        return True
    if any(k in rm for k in _NON_ORDER_REMARK_KW):
        return True
    if rel.startswith("P") or re.search(r"[A-Za-z=+/]", rel):
        return True
    # 4) 支付宝内部号 (2026-07-10): 日期开头的 ≥25 位纯数字 = 账务流水号(20260421 20004001…, 32位)
    # 或交易号(20260421 22001488…, 28位), 天生不是淘宝订单号(18-19位、不以日期开头)。爱群号/佳宝号
    # 个人账单的「商户订单号」列装的就是这类号, 655 条被僵尸提示"待补还原规则"——其实无规则可补。
    # ≥25 位下限保证 19 位真单号(即便碰巧 20 开头)绝不会被误伤。
    if re.fullmatch(r"20\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{17,}", rel):
        return True
    return False
