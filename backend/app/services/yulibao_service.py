"""余利宝资产估算。

支付宝普通账户余额接口不包含余利宝。当前应用未开通独立的
``mybank.finance.yulibao.account.query`` 权限时，按企业号资金账单中的
余利宝申购、赎回及收益记录重建余额。该金额属于资产余额，不属于经营
收入或支出；原流水仍由 ``internal_transfer`` 口径从经营收支中剔除。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, AlipayFlow


YULIBAO_ACCOUNT_NAME = "支付宝-企业账号-余利宝"
_Q = Decimal("0.01")

_REDEEM_WORDS = ("赎回", "转出", "提现")
_PURCHASE_WORDS = ("申购", "自动转入", "转入余利宝", "支付宝转入")
_REFUND_WORDS = ("撤销", "退款", "退回")
_PROFIT_WORDS = ("收益", "分红")


def _text(flow: AlipayFlow) -> str:
    return " ".join(
        str(value or "")
        for value in (
            flow.transaction_type,
            flow.counterparty,
            flow.counterparty_account,
            flow.remark,
        )
    )


def _contribution(flow: AlipayFlow) -> tuple[Decimal, str]:
    """Return this standard-account flow's contribution to YuLiBao assets.

    The sign in ``AlipayFlow.amount`` is from the ordinary Alipay account's
    point of view.  Therefore a negative purchase increases YuLiBao, while a
    positive redemption decreases it.  Yield is the exception: it increases
    YuLiBao even when reported as a positive amount.
    """
    amount = Decimal(str(flow.amount or 0))
    text = _text(flow)
    absolute = abs(amount)
    if any(word in text for word in _PROFIT_WORDS):
        return absolute, "profit"
    if any(word in text for word in _REFUND_WORDS) and any(
        word in text for word in _PURCHASE_WORDS
    ):
        return -absolute, "purchase_refund"
    if any(word in text for word in _REDEEM_WORDS):
        return -absolute, "redeem"
    if any(word in text for word in _PURCHASE_WORDS):
        return absolute, "purchase"
    # Unknown YuLiBao wording: the ordinary Alipay account sign is still an
    # auditable fallback (money out -> asset up; money in -> asset down).
    return -amount, "signed_fallback"


def estimate_from_flows(db: Session, *, source_account: str = "企业号") -> dict:
    """Rebuild the current YuLiBao balance from all imported source flows."""
    marker_filter = or_(
        AlipayFlow.transaction_type.contains("余利宝"),
        AlipayFlow.counterparty.contains("余利宝"),
        AlipayFlow.counterparty_account.contains("余利宝"),
        AlipayFlow.remark.contains("余利宝"),
    )
    rows = db.execute(
        select(AlipayFlow)
        .where(AlipayFlow.account == source_account, marker_filter)
        .order_by(AlipayFlow.transaction_time.asc(), AlipayFlow.id.asc())
    ).scalars().all()

    if not rows:
        return {
            "ok": False,
            "source_account": source_account,
            "reason": "no_yulibao_flows",
            "balance": None,
            "count": 0,
            "as_of_date": None,
        }

    total = Decimal("0")
    categories: dict[str, int] = {}
    latest_date: date | None = None
    for flow in rows:
        delta, category = _contribution(flow)
        total += delta
        categories[category] = categories.get(category, 0) + 1
        if flow.transaction_time is not None:
            flow_date = flow.transaction_time.date()
            if latest_date is None or flow_date > latest_date:
                latest_date = flow_date

    total = total.quantize(_Q)
    if total < 0:
        return {
            "ok": False,
            "source_account": source_account,
            "reason": "negative_estimate",
            "balance": str(total),
            "count": len(rows),
            "categories": categories,
            "as_of_date": latest_date,
        }

    return {
        "ok": True,
        "source_account": source_account,
        "balance": total,
        "count": len(rows),
        "categories": categories,
        "as_of_date": latest_date,
    }


def upsert_estimated_balance(
    db: Session,
    estimate: dict,
    *,
    account_name: str = YULIBAO_ACCOUNT_NAME,
) -> AccountBalance:
    """Write the estimate as a separate asset row so internal transfers are not double-counted."""
    if not estimate.get("ok"):
        raise ValueError(f"invalid YuLiBao estimate: {estimate.get('reason')}")

    as_of = estimate.get("as_of_date")
    if not isinstance(as_of, date):
        raise ValueError("YuLiBao estimate has no source date")

    period_year, period_month = as_of.year, as_of.month
    row = db.execute(
        select(AccountBalance).where(
            AccountBalance.account_name == account_name,
            AccountBalance.period_year == period_year,
            AccountBalance.period_month == period_month,
        )
    ).scalar_one_or_none()
    if row is None:
        prev = db.execute(
            select(AccountBalance)
            .where(AccountBalance.account_name == account_name)
            .order_by(AccountBalance.period_year.desc(), AccountBalance.period_month.desc())
        ).scalars().first()
        row = AccountBalance(
            account_name=account_name,
            period_year=period_year,
            period_month=period_month,
            account_no=(prev.account_no if prev else None),
            opening_balance=(prev.closing_balance if prev else Decimal("0")),
        )
        db.add(row)

    row.closing_balance = Decimal(str(estimate["balance"])).quantize(_Q)
    row.as_of_date = as_of
    row.remark = (
        "余利宝估算余额：按企业号资金账单中余利宝申购/赎回/收益净额累计；"
        f"共{estimate['count']}笔，最近流水{as_of}；"
        "不含尚未进入资金账单的当日转入及未入账收益。"
    )
    return row


def refresh_estimated_balance(db: Session, *, source_account: str = "企业号") -> dict:
    """Recalculate and stage the separate YuLiBao asset row (caller commits)."""
    estimate = estimate_from_flows(db, source_account=source_account)
    if not estimate.get("ok"):
        return estimate
    row = upsert_estimated_balance(db, estimate)
    db.flush()
    return {
        **estimate,
        "account": row.account_name,
        "balance": row.closing_balance,
        "as_of_date": row.as_of_date,
        "source": "flow_estimate",
    }
