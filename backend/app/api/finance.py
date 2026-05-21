from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.finance import AccountBalance, AlipayFlow
from app.services import alipay_import, balance_service, reconciliation_service

router = APIRouter(prefix="/api/finance", tags=["finance"])


# -------- Alipay flows --------

class AlipayFlowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account: str
    transaction_no: str
    transaction_time: Optional[datetime]
    transaction_type: Optional[str]
    counterparty: Optional[str]
    amount: Decimal
    related_order_no: Optional[str]
    balance: Optional[Decimal]
    reconciliation_status: str
    reconciliation_type: Optional[str]
    remark: Optional[str]


@router.get("/alipay-flows", response_model=list[AlipayFlowOut])
def list_alipay(
    account: Optional[str] = None,
    recon_type: Optional[str] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(AlipayFlow)
    if account:
        stmt = stmt.where(AlipayFlow.account == account)
    if recon_type:
        stmt = stmt.where(AlipayFlow.reconciliation_type == recon_type)
    stmt = stmt.order_by(AlipayFlow.transaction_time.desc().nulls_last(), AlipayFlow.id.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


class AlipayImportResult(BaseModel):
    inserted: int
    skipped_duplicate: int
    skipped_invalid: int
    errors: list[str]


@router.post("/alipay-flows/import-csv", response_model=AlipayImportResult)
async def import_alipay(
    account: str = Query(..., description="账户名 (企业号 / 私账 / 主力号 …)"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    r = alipay_import.import_alipay_csv(db, text, account=account)
    return AlipayImportResult(
        inserted=r.inserted,
        skipped_duplicate=r.skipped_duplicate,
        skipped_invalid=r.skipped_invalid,
        errors=r.errors,
    )


# -------- Account balances --------

class AccountBalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    account_name: str
    period_year: int
    period_month: int
    opening_balance: Decimal
    income: Decimal
    expense: Decimal
    closing_balance: Decimal


@router.get("/accounts", response_model=list[AccountBalanceOut])
def list_balances(
    account_name: Optional[str] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
):
    stmt = select(AccountBalance)
    if account_name:
        stmt = stmt.where(AccountBalance.account_name == account_name)
    if year:
        stmt = stmt.where(AccountBalance.period_year == year)
    stmt = stmt.order_by(AccountBalance.account_name, AccountBalance.period_year.desc(), AccountBalance.period_month.desc())
    return db.execute(stmt).scalars().all()


class RecomputeIn(BaseModel):
    account_name: str
    year: int
    month: int
    opening_balance: Optional[Decimal] = None


@router.post("/accounts/recompute", response_model=AccountBalanceOut)
def recompute(payload: RecomputeIn, db: Session = Depends(get_db)):
    try:
        row = balance_service.recompute_month(
            db,
            account=payload.account_name,
            year=payload.year,
            month=payload.month,
            opening_balance=payload.opening_balance,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(row)
    return row


# -------- Reconciliation --------

class DiffOut(BaseModel):
    key: str
    expected: Optional[Decimal]
    actual: Optional[Decimal]
    diff: Optional[Decimal]
    severity: str
    message: str


class ReconciliationOut(BaseModel):
    rule: str
    total_diffs: int
    ok_count: int
    warning_count: int
    error_count: int
    diffs: list[DiffOut]


def _to_out(r: reconciliation_service.ReconciliationResult) -> ReconciliationOut:
    return ReconciliationOut(
        rule=r.rule,
        total_diffs=r.total_diffs,
        ok_count=r.ok_count,
        warning_count=r.warning_count,
        error_count=r.error_count,
        diffs=[DiffOut(**d.__dict__) for d in r.diffs],
    )


@router.get("/reconciliation/{rule}", response_model=ReconciliationOut)
def run_one_rule(rule: str, db: Session = Depends(get_db)):
    fn = reconciliation_service.RULES.get(rule)
    if fn is None:
        raise HTTPException(404, f"unknown rule {rule!r}; available: {list(reconciliation_service.RULES)}")
    r = fn(db, record_exceptions=False)
    return _to_out(r)


@router.get("/reconciliation", response_model=dict[str, ReconciliationOut])
def run_all_rules(db: Session = Depends(get_db)):
    results = reconciliation_service.run_all(db, record_exceptions=False)
    return {name: _to_out(r) for name, r in results.items()}
