"""对账诊断 — 只读, 揭示"对账缺口在哪": 账户余额钩稽 / 孤儿流水 / 各账户流水覆盖。

不改数据, 给老板"该补哪批流水、哪些钱没人认领、哪本余额表对不平"的体检报告。
对应需求的对账缺口: 多账户余额钩稽 + 反向钩稽(孤儿流水) + 流水覆盖。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, AlipayFlow

_CENT = Decimal("0.01")


def account_balance_check(db: Session, *, tol: Decimal = Decimal("1")) -> dict:
    """账户余额表内部钩稽: 期初 + 收入 − 支出 ?= 期末。对不平的行 = 录入有误/漏记。"""
    rows = db.execute(select(AccountBalance)).scalars().all()
    bad = []
    for r in rows:
        opening = Decimal(r.opening_balance or 0)
        income = Decimal(r.income or 0)
        expense = Decimal(r.expense or 0)
        closing = Decimal(r.closing_balance or 0)
        expected = opening + income - expense
        diff = (closing - expected).quantize(_CENT)
        if abs(diff) > tol:
            bad.append({
                "account_name": r.account_name,
                "period": f"{r.period_year:04d}-{r.period_month:02d}",
                "opening": float(opening), "income": float(income),
                "expense": float(expense), "closing": float(closing),
                "expected_closing": float(expected), "diff": float(diff),
            })
    return {"checked": len(rows), "unbalanced": len(bad), "rows": bad}


def orphan_flows(db: Session, *, limit: int = 50) -> dict:
    """孤儿流水: 既没关联订单、也没归类(reconciliation_type)、且仍 open 的支付宝流水。

    这些钱没人认领 = 漏记的收入 / 分类错的支出 / 异常, 最该先看。
    """
    flows = db.execute(select(AlipayFlow)).scalars().all()
    orphans = [
        f for f in flows
        if (f.reconciliation_status or "open") == "open"
        and not f.related_order_no
        and not f.reconciliation_type
    ]
    income = sum((f.amount for f in orphans if (f.amount or 0) > 0), Decimal("0"))
    expense = sum((-f.amount for f in orphans if (f.amount or 0) < 0), Decimal("0"))
    by_account: dict[str, int] = {}
    for f in orphans:
        by_account[f.account] = by_account.get(f.account, 0) + 1
    samples = [{
        "account": f.account, "transaction_no": f.transaction_no,
        "transaction_time": f.transaction_time.isoformat() if f.transaction_time else None,
        "transaction_type": f.transaction_type, "amount": float(f.amount or 0),
        "counterparty": f.counterparty, "remark": (f.remark or "")[:40],
    } for f in sorted(orphans, key=lambda x: abs(float(x.amount or 0)), reverse=True)[:limit]]
    return {
        "total_flows": len(flows), "orphan_count": len(orphans),
        "orphan_income": float(income), "orphan_expense": float(expense),
        "by_account": by_account, "samples": samples,
    }


def flow_coverage_by_account(db: Session) -> dict:
    """各账户流水覆盖: 总笔数 / 有订单号 / 已核销(matched) / 未归类(open且无type)。

    某账户"未归类"占比高 = 该账户流水没对/没打标, 对账自然一片红。
    """
    flows = db.execute(select(AlipayFlow)).scalars().all()
    acc: dict[str, dict] = {}
    for f in flows:
        a = acc.setdefault(f.account, {
            "account": f.account, "total": 0, "with_order": 0,
            "matched": 0, "unclassified": 0, "no_date": 0,
        })
        a["total"] += 1
        if f.related_order_no:
            a["with_order"] += 1
        if (f.reconciliation_status or "") == "matched":
            a["matched"] += 1
        if (f.reconciliation_status or "open") == "open" and not f.reconciliation_type:
            a["unclassified"] += 1
        if f.transaction_time is None:
            a["no_date"] += 1
    rows = sorted(acc.values(), key=lambda r: r["total"], reverse=True)
    for r in rows:
        r["matched_pct"] = round(r["matched"] / r["total"] * 100, 1) if r["total"] else 0.0
    return {"accounts": rows}


def diagnostics(db: Session) -> dict:
    """汇总三项对账诊断。"""
    return {
        "balance_check": account_balance_check(db),
        "orphan_flows": orphan_flows(db),
        "coverage": flow_coverage_by_account(db),
    }
