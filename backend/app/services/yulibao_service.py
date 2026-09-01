"""余利宝资产估算。

支付宝普通账户余额接口不包含余利宝。当前应用未开通独立的
``mybank.finance.yulibao.account.query`` 权限时，按企业号资金账单中的
余利宝申购、赎回及收益记录重建余额。该金额属于资产余额，不属于经营
收入或支出；原流水仍由 ``internal_transfer`` 口径从经营收支中剔除。
"""
from __future__ import annotations

import json
from datetime import date
from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, AlipayFlow
from app.services import settings_service


YULIBAO_ACCOUNT_NAME = "支付宝-企业账号-余利宝"
KEY_MANUAL_CHECKPOINT = "yulibao_manual_checkpoint_v1"
_Q = Decimal("0.01")

_REDEEM_WORDS = ("赎回", "转出", "提现")
_PURCHASE_WORDS = ("申购", "自动转入", "转入余利宝", "支付宝转入")
_REFUND_WORDS = ("撤销", "退款", "退回")
_PROFIT_WORDS = ("收益", "分红")


def get_manual_checkpoint(db: Session) -> dict | None:
    """Return the operator-confirmed YuLiBao end-of-day balance, if any."""
    raw = settings_service.get(db, KEY_MANUAL_CHECKPOINT, env_fallback=False)
    if not raw:
        return None
    try:
        value = json.loads(raw)
        balance = Decimal(str(value["balance"])).quantize(_Q)
        as_of = date.fromisoformat(str(value["as_of_date"]))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if balance < 0:
        return None
    return {
        **value,
        "balance": balance,
        "as_of_date": as_of,
    }


def set_manual_checkpoint(
    db: Session,
    *,
    balance: Decimal,
    as_of_date: date,
    note: str | None = None,
) -> dict:
    """Persist a confirmed balance without letting later refreshes overwrite it.

    The checkpoint is an end-of-day balance.  Automatic estimates add only
    YuLiBao flows dated after ``as_of_date``.  A short history is retained in
    the same setting so a mistaken manual edit remains auditable.
    """
    confirmed = Decimal(str(balance)).quantize(_Q)
    if confirmed < 0:
        raise ValueError("余利宝人工基准不能为负数")

    previous_raw = settings_service.get(
        db, KEY_MANUAL_CHECKPOINT, env_fallback=False,
    )
    history: list[dict] = []
    previous: dict | None = None
    if previous_raw:
        try:
            previous = json.loads(previous_raw)
            history = list(previous.get("history") or [])
        except (TypeError, json.JSONDecodeError):
            previous = None
    if previous and (
        str(previous.get("balance")) != str(confirmed)
        or str(previous.get("as_of_date")) != as_of_date.isoformat()
    ):
        history.append({
            "balance": previous.get("balance"),
            "as_of_date": previous.get("as_of_date"),
            "note": previous.get("note"),
            "replaced_at": datetime.now().isoformat(timespec="seconds"),
        })

    value = {
        "balance": str(confirmed),
        "as_of_date": as_of_date.isoformat(),
        "note": (note or "").strip() or None,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "history": history[-20:],
    }
    settings_service.set_value(
        db,
        KEY_MANUAL_CHECKPOINT,
        json.dumps(value, ensure_ascii=False),
        description="余利宝人工确认余额基准；自动估算仅叠加基准日之后流水",
    )
    return {
        **value,
        "balance": confirmed,
        "as_of_date": as_of_date,
    }


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
    """Estimate from the manual checkpoint plus later imported source flows."""
    checkpoint = get_manual_checkpoint(db)
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

    if checkpoint:
        rows = [
            flow for flow in rows
            if flow.transaction_time is not None
            and flow.transaction_time.date() > checkpoint["as_of_date"]
        ]

    if not rows and not checkpoint:
        return {
            "ok": False,
            "source_account": source_account,
            "reason": "no_yulibao_flows",
            "balance": None,
            "count": 0,
            "as_of_date": None,
        }

    # A funds statement can begin in the middle of an account's lifetime.  In
    # that case summing the visible YuLiBao rows from zero yields a plausible
    # but incomplete asset balance.  Never publish that partial number as a
    # successful estimate: a confirmed end-of-day checkpoint is the boundary
    # that proves the opening balance is complete.
    if not checkpoint:
        dated_rows = [flow for flow in rows if flow.transaction_time is not None]
        latest_date = (
            max(flow.transaction_time.date() for flow in dated_rows)
            if dated_rows else None
        )
        earliest_date = (
            min(flow.transaction_time.date() for flow in dated_rows)
            if dated_rows else None
        )
        return {
            "ok": False,
            "source_account": source_account,
            "reason": "missing_opening_checkpoint",
            "balance": None,
            "count": len(rows),
            "earliest_date": earliest_date,
            "as_of_date": latest_date,
        }

    total = Decimal(str(checkpoint["balance"]))
    categories: dict[str, int] = {}
    latest_date: date | None = checkpoint["as_of_date"]
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
        "checkpoint": checkpoint,
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
        "余利宝估算余额："
        + (
            f"以人工确认的{estimate['checkpoint']['as_of_date']}余额"
            f"{estimate['checkpoint']['balance']}元为基准，"
        )
        + "叠加之后的余利宝申购/赎回/收益净额；"
        f"本段共{estimate['count']}笔，统计至{as_of}；"
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
        "source": "manual_checkpoint_plus_flows" if estimate.get("checkpoint") else "flow_estimate",
    }
