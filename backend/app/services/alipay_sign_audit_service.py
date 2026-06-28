# -*- coding: utf-8 -*-
"""支付宝流水「历史符号脏数据」只读审计 (用户 2026-06-28, 配件 epic 点4)。

背景: 系统约定 AlipayFlow.amount 正=收入/负=支出。企业号走 import_alipay_bill 自动合成符号永远对;
但手动 Excel / 标准 CSV 等"透传"路径信任源符号 —— 历史上有些账户(尤其爱群号, finance.py 注释自认
"丢符号:支出应为负")把支出存成了正数。这些已躺在库里的错符号行 = "历史符号脏数据"。

本模块只做【只读审计】, 不改任何数据: 捞出"金额为正、但交易类型/对手方/备注像支出"的可疑行,
按账户汇总 + 给样例, 供人工确认。真正的修正(原地 UPDATE 翻符号 + 重算 sync_key)是另一步、需人工先确认
清单(amount 是唯一键+sync_key 的一部分, 绝不能删后重导=会双计)。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow

# 强信号: 交易类型明确含"支出"却存成正数 → 几乎必为错符号。
# 弱信号: 类型无方向词, 但对手方/备注像"付款/采购/手续费/提现"等支出 → 列出待人工判, 不自动断定。
_EXPENSE_TEXT_KW = (
    "支出", "付款", "采购", "货款", "手续费", "服务费", "提现", "转账",
    "代付", "充值", "运费", "缴税", "缴费", "工资", "报销",
)


def _d_abs(v) -> Decimal:
    return Decimal(str(abs(v))) if v is not None else Decimal("0")


def _is_expense_signal(f: AlipayFlow) -> tuple[bool, bool]:
    """返回 (是否疑似支出, 是否强信号)。强信号=交易类型明确含'支出'。"""
    ttype = (f.transaction_type or "")
    if "支出" in ttype:
        return True, True
    text = f"{ttype} {f.counterparty or ''} {f.remark or ''}"
    if any(k in text for k in _EXPENSE_TEXT_KW):
        return True, False
    return False, False


def audit_wrong_sign(db: Session, *, account: Optional[str] = None,
                     sample_limit: int = 8) -> dict:
    """只读: 找 amount>0 但疑似支出的流水, 按账户汇总。绝不改库。

    返回 {by_account: [{account, suspect_total, strong, weak, suspect_amount, samples:[...]}], total_*}。
    strong = 交易类型明确"支出"却为正(高置信错符号); weak = 仅文本像支出(待人工判)。
    """
    stmt = select(AlipayFlow).where(AlipayFlow.amount > 0)
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    rows = db.execute(stmt).scalars().all()

    by_acc: dict[str, dict] = defaultdict(
        lambda: {"strong": 0, "weak": 0, "suspect_amount": Decimal("0"), "samples": []})
    g_strong = g_weak = 0
    for f in rows:
        suspect, strong = _is_expense_signal(f)
        if not suspect:
            continue
        a = by_acc[f.account or "(未知)"]
        if strong:
            a["strong"] += 1
            g_strong += 1
        else:
            a["weak"] += 1
            g_weak += 1
        a["suspect_amount"] += _d_abs(f.amount)
        if len(a["samples"]) < sample_limit:
            a["samples"].append({
                "transaction_no": f.transaction_no,
                "transaction_type": f.transaction_type,
                "counterparty": f.counterparty,
                "amount": float(f.amount),
                "remark": (f.remark or "")[:40],
                "signal": "强(交易类型=支出)" if strong else "弱(文本疑似)",
            })
    out = []
    for acc, a in sorted(by_acc.items(), key=lambda kv: -(kv[1]["strong"] + kv[1]["weak"])):
        out.append({
            "account": acc,
            "suspect_total": a["strong"] + a["weak"],
            "strong": a["strong"], "weak": a["weak"],
            "suspect_amount": float(a["suspect_amount"].quantize(Decimal("0.01"))),
            "samples": a["samples"],
        })
    return {
        "by_account": out,
        "total_suspect": g_strong + g_weak,
        "total_strong": g_strong,
        "total_weak": g_weak,
        "note": ("strong=交易类型明确支出却存成正数(高置信错符号, 可批量原地翻符号); "
                 "weak=仅文本疑似(需人工逐条判)。本审计只读不改; 修正需先人工确认清单。"),
    }
