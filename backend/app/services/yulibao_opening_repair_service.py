"""Fail-closed repair for the missing 2026-07-31 YuLiBao opening balance.

This is intentionally narrower than a classifier.  It recognises exactly the
three production transfers verified during the 2026-09-01 incident review and
the one same-day YuLiBao purchase needed to construct the end-of-day checkpoint.
Any missing, extra, or changed row stops the repair without writing.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, AlipayFlow
from app.services import field_change_service, settings_service, yulibao_service


REPAIR_RECEIPT_KEY = "yulibao_opening_repair_20260731_v1"
SOURCE_ACCOUNT = "企业号"
CHECKPOINT_DATE = date(2026, 7, 31)
CHECKPOINT_BALANCE = Decimal("368064.15")
_LOCAL_TZ = ZoneInfo("Asia/Shanghai")

_EXPECTED_TRANSFER_SIGNATURES = {
    ("2026-07-31 06:47:14", Decimal("-100000.00"), Decimal("261447.82"), "其它", "转出到网商银行"),
    ("2026-07-31 06:47:48", Decimal("-200000.00"), Decimal("61447.82"), "其它", "转出到网商银行"),
    ("2026-07-31 06:48:05", Decimal("-60766.28"), Decimal("681.54"), "其它", "转出到网商银行"),
}
_EXPECTED_SAME_DAY_YULIBAO_SIGNATURE = (
    "2026-07-31 22:00:17",
    Decimal("-7297.87"),
    Decimal("698.04"),
    "其它",
    "余利宝-基金申购，支付宝转入",
)


class RepairScopeError(RuntimeError):
    """Raised when production rows no longer match the approved repair scope."""


def _local_second(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(_LOCAL_TZ).replace(tzinfo=None)
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _signature(flow: AlipayFlow) -> tuple:
    return (
        _local_second(flow.transaction_time),
        Decimal(str(flow.amount)).quantize(Decimal("0.01")),
        (
            Decimal(str(flow.balance)).quantize(Decimal("0.01"))
            if flow.balance is not None else None
        ),
        str(flow.transaction_type or ""),
        str(flow.remark or ""),
    )


def _public_flow(flow: AlipayFlow) -> dict:
    return {
        "id": flow.id,
        "transaction_time": _local_second(flow.transaction_time),
        "amount": str(Decimal(str(flow.amount)).quantize(Decimal("0.01"))),
        "balance": (
            str(Decimal(str(flow.balance)).quantize(Decimal("0.01")))
            if flow.balance is not None else None
        ),
        "transaction_type": flow.transaction_type,
        "remark": flow.remark,
        "reconciliation_type": flow.reconciliation_type,
    }


def _rows_containing(db: Session, marker: str) -> list[AlipayFlow]:
    marker_filter = or_(
        AlipayFlow.transaction_type.contains(marker),
        AlipayFlow.counterparty.contains(marker),
        AlipayFlow.counterparty_account.contains(marker),
        AlipayFlow.remark.contains(marker),
    )
    return db.execute(
        select(AlipayFlow)
        .where(AlipayFlow.account == SOURCE_ACCOUNT, marker_filter)
        .order_by(AlipayFlow.transaction_time.asc(), AlipayFlow.id.asc())
    ).scalars().all()


def _validated_scope(db: Session) -> tuple[list[AlipayFlow], AlipayFlow]:
    transfers = _rows_containing(db, "网商银行")
    actual_transfer_signatures = {_signature(flow) for flow in transfers}
    if (
        len(transfers) != len(_EXPECTED_TRANSFER_SIGNATURES)
        or actual_transfer_signatures != _EXPECTED_TRANSFER_SIGNATURES
    ):
        raise RepairScopeError(
            "网商银行历史流水与批准范围不一致；拒绝自动修正。"
            + json.dumps([_public_flow(flow) for flow in transfers], ensure_ascii=False)
        )
    invalid_types = [
        flow for flow in transfers
        if flow.reconciliation_type not in (None, "internal_transfer")
    ]
    if invalid_types:
        raise RepairScopeError(
            "目标流水已被归入其他业务类型；拒绝覆盖。"
            + json.dumps([_public_flow(flow) for flow in invalid_types], ensure_ascii=False)
        )

    same_day = [
        flow for flow in _rows_containing(db, "余利宝")
        if _local_second(flow.transaction_time)
        and _local_second(flow.transaction_time).startswith("2026-07-31 ")
    ]
    if len(same_day) != 1 or _signature(same_day[0]) != _EXPECTED_SAME_DAY_YULIBAO_SIGNATURE:
        raise RepairScopeError(
            "2026-07-31余利宝流水与期初计算依据不一致；拒绝自动修正。"
            + json.dumps([_public_flow(flow) for flow in same_day], ensure_ascii=False)
        )
    if yulibao_service._contribution(same_day[0])[0] != Decimal("7297.87"):
        raise RepairScopeError("2026-07-31余利宝申购方向无法确认；拒绝自动修正。")
    return transfers, same_day[0]


def _validated_checkpoint(db: Session) -> dict | None:
    checkpoint = yulibao_service.get_manual_checkpoint(db)
    if checkpoint and (
        checkpoint["as_of_date"] != CHECKPOINT_DATE
        or checkpoint["balance"] != CHECKPOINT_BALANCE
    ):
        raise RepairScopeError("已有余利宝人工基准与本次批准值冲突；拒绝覆盖。")
    return checkpoint


def inspect(db: Session) -> dict:
    transfers, same_day = _validated_scope(db)
    checkpoint = _validated_checkpoint(db)
    receipt = settings_service.get(db, REPAIR_RECEIPT_KEY, env_fallback=False)
    return {
        "ok": True,
        "repair_key": REPAIR_RECEIPT_KEY,
        "source_account": SOURCE_ACCOUNT,
        "transfer_count": len(transfers),
        "transfer_total": str(sum(-Decimal(str(row.amount)) for row in transfers).quantize(Decimal("0.01"))),
        "same_day_yulibao_flow_id": same_day.id,
        "checkpoint": {
            "balance": str(CHECKPOINT_BALANCE),
            "as_of_date": CHECKPOINT_DATE.isoformat(),
            "already_present": checkpoint is not None,
        },
        "already_classified": all(row.reconciliation_type == "internal_transfer" for row in transfers),
        "receipt_present": bool(receipt),
        "flow_ids": [row.id for row in transfers],
    }


def repair(db: Session, *, apply: bool) -> dict:
    scope = inspect(db)
    if not apply:
        return {**scope, "applied": False, "dry_run": True}

    transfers, _ = _validated_scope(db)
    checkpoint = _validated_checkpoint(db)
    existing_receipt = settings_service.get(db, REPAIR_RECEIPT_KEY, env_fallback=False)

    if existing_receipt:
        try:
            receipt_value = json.loads(existing_receipt)
        except json.JSONDecodeError as exc:
            raise RepairScopeError("既有修正回执损坏；拒绝重复执行。") from exc
        if not all(row.reconciliation_type == "internal_transfer" for row in transfers):
            raise RepairScopeError("已有修正回执但流水分类发生回退；拒绝重复执行。")
        if checkpoint is None:
            raise RepairScopeError("已有修正回执但余利宝基准缺失；拒绝重复执行。")
        return {
            **scope,
            "applied": False,
            "reused": True,
            "receipt": receipt_value,
        }

    changed_ids: list[int] = []
    for flow in transfers:
        if flow.reconciliation_type == "internal_transfer":
            continue
        field_change_service.record(
            db,
            table="alipay_flows",
            pk=flow.id,
            field="reconciliation_type",
            old=flow.reconciliation_type,
            new="internal_transfer",
            actor="02｜ERP审核与程序维护",
            source="repair_0901",
            row_label=f"企业号内部调拨 {flow.transaction_no}",
            field_label="核销类型",
        )
        flow.reconciliation_type = "internal_transfer"
        changed_ids.append(flow.id)

    if checkpoint is None:
        yulibao_service.set_manual_checkpoint(
            db,
            balance=CHECKPOINT_BALANCE,
            as_of_date=CHECKPOINT_DATE,
            note=(
                "2026-09-01审计修正：三笔转出到网商银行共360766.28元，"
                "加当日已明确余利宝申购7297.87元，形成2026-07-31日终基准。"
            ),
        )
        field_change_service.record(
            db,
            table="system_settings",
            pk=yulibao_service.KEY_MANUAL_CHECKPOINT,
            field="checkpoint",
            old=None,
            new=f"{CHECKPOINT_DATE.isoformat()}={CHECKPOINT_BALANCE}",
            actor="02｜ERP审核与程序维护",
            source="repair_0901",
            row_label="余利宝日终余额基准",
            field_label="人工确认基准",
        )

    refreshed = yulibao_service.refresh_estimated_balance(db, source_account=SOURCE_ACCOUNT)
    if not refreshed.get("ok"):
        raise RepairScopeError(f"余利宝基准写入后重算失败：{refreshed.get('reason')}")

    receipt = {
        "repair_key": REPAIR_RECEIPT_KEY,
        "applied_at": datetime.now(_LOCAL_TZ).isoformat(timespec="seconds"),
        "flow_ids": [row.id for row in transfers],
        "changed_flow_ids": changed_ids,
        "checkpoint_balance": str(CHECKPOINT_BALANCE),
        "checkpoint_as_of_date": CHECKPOINT_DATE.isoformat(),
        "estimated_balance": str(refreshed["balance"]),
        "estimated_as_of_date": str(refreshed["as_of_date"]),
        "source_count_after_checkpoint": refreshed.get("count"),
    }
    settings_service.set_value(
        db,
        REPAIR_RECEIPT_KEY,
        json.dumps(receipt, ensure_ascii=False, separators=(",", ":")),
        description="2026-07-31余利宝期初缺口一次性修正回执（不含凭据）",
    )
    db.flush()
    return {
        **scope,
        "applied": True,
        "reused": False,
        "receipt": receipt,
    }


def current_balance(db: Session) -> AccountBalance | None:
    return db.execute(
        select(AccountBalance)
        .where(AccountBalance.account_name == yulibao_service.YULIBAO_ACCOUNT_NAME)
        .order_by(AccountBalance.period_year.desc(), AccountBalance.period_month.desc())
    ).scalars().first()
