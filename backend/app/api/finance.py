from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.finance import AccountBalance, AlipayFlow
from app.services import (
    alipay_backfill_service,
    alipay_import,
    balance_service,
    bill_import_service,
    reconciliation_service,
    smart_matching_service,
)

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
    auto_tagged: dict[str, int] = {}
    auto_untouched: int = 0


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
    # plan §12.4: 导入完跑一次智能核销
    matched = smart_matching_service.run(db, account=account)
    db.commit()
    return AlipayImportResult(
        inserted=r.inserted,
        skipped_duplicate=r.skipped_duplicate,
        skipped_invalid=r.skipped_invalid,
        errors=r.errors,
        auto_tagged=matched.tagged,
        auto_untouched=matched.untouched,
    )


class SmartMatchResult(BaseModel):
    total_scanned: int
    tagged: dict[str, int]
    untouched: int


@router.post("/smart-match/rerun", response_model=SmartMatchResult)
def rerun_smart_match(account: Optional[str] = None, db: Session = Depends(get_db)):
    """重新核销: 对未打标流水按 关联订单号→工厂名→关键字 三段重新打 reconciliation_type.

    导入订单/工厂下单表后再触发, 可把之前没识别的货款/客户回款挂上。
    """
    r = smart_matching_service.run(db, account=account)
    db.commit()
    return SmartMatchResult(total_scanned=r.total_scanned, tagged=r.tagged, untouched=r.untouched)


# -------- 支付宝流水 → 订单 反向匹配回填 --------

class AlipayBackfillOut(BaseModel):
    total_flows: int
    matched_orders: int
    filled_flow_no: int
    ambiguous: int
    unmatched: int
    by_rule: dict[str, int]
    samples: list[dict]


def _backfill_to_out(r: "alipay_backfill_service.BackfillResult") -> AlipayBackfillOut:
    return AlipayBackfillOut(
        total_flows=r.total_flows,
        matched_orders=r.matched_orders,
        filled_flow_no=r.filled_flow_no,
        ambiguous=r.ambiguous,
        unmatched=r.unmatched,
        by_rule=r.by_rule,
        samples=r.samples,
    )


@router.get("/order-flow-match/analyze", response_model=AlipayBackfillOut)
def analyze_order_flow_match(account: Optional[str] = None, db: Session = Depends(get_db)):
    """只读: 自动从流水里找订单号规律, 预览能匹配多少订单 (不写库).

    返回各规律命中数 (exact/strip_prefix/tail_index) + 样本, 看清匹配逻辑。
    """
    return _backfill_to_out(alipay_backfill_service.analyze(db, account=account))


@router.post("/order-flow-match/backfill", response_model=AlipayBackfillOut)
def backfill_order_flow_no(
    account: Optional[str] = None, only_missing: bool = True, db: Session = Depends(get_db),
):
    """落库: 把匹配到的支付宝流水号回填到订单 Order.alipay_flow_no。

    自己找规律 (T200P 前缀 / 备注内订单号 / 尾号倒排), 歧义流水跳过交人工。
    """
    r = alipay_backfill_service.backfill(db, account=account, only_missing=only_missing)
    db.commit()
    return _backfill_to_out(r)


# -------- 万师傅 / 物流账单导入 (安装费 / 物流费对账数据源) --------

class BillImportResult(BaseModel):
    inserted: int
    skipped_invalid: int
    errors: list[str]


async def _read_csv(file: UploadFile) -> str:
    raw = await file.read()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


@router.post("/wanshifu-bills/import-csv", response_model=BillImportResult)
async def import_wanshifu(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """导入万师傅安装账单 CSV → 供安装费对账 (rule=install_fee) 当应付口径。"""
    text = await _read_csv(file)
    r = bill_import_service.import_wanshifu_csv(db, text)
    db.commit()
    return BillImportResult(inserted=r.inserted, skipped_invalid=r.skipped_invalid, errors=r.errors)


@router.post("/logistics-bills/import-csv", response_model=BillImportResult)
async def import_logistics(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """导入物流公司月结账单 CSV → 供物流费对账 (rule=logistics_fee) 当应付口径。"""
    text = await _read_csv(file)
    r = bill_import_service.import_logistics_csv(db, text)
    db.commit()
    return BillImportResult(inserted=r.inserted, skipped_invalid=r.skipped_invalid, errors=r.errors)


# -------- 推广记录 / 补单对账 / 账户余额 CSV 导入 --------

@router.post("/promotion-flows/import-csv", response_model=BillImportResult)
async def import_promotion_flows(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """导入推广记录 CSV (直通车/万相台充值+支出; 列名自动识别)。"""
    text = await _read_csv(file)
    r = bill_import_service.import_promotion_flows_csv(db, text)
    db.commit()
    return BillImportResult(inserted=r.inserted, skipped_invalid=r.skipped_invalid, errors=r.errors)


@router.post("/refill-records/import-csv", response_model=BillImportResult)
async def import_refill_records(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """导入补单对账 CSV (订单号必填)。"""
    text = await _read_csv(file)
    r = bill_import_service.import_refill_records_csv(db, text)
    db.commit()
    return BillImportResult(inserted=r.inserted, skipped_invalid=r.skipped_invalid, errors=r.errors)


@router.post("/accounts/import-csv", response_model=BillImportResult)
async def import_account_balances(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """导入账户余额 CSV (同账户同月 upsert; 账户名+年+月必填)。"""
    text = await _read_csv(file)
    r = bill_import_service.import_account_balances_csv(db, text)
    db.commit()
    return BillImportResult(inserted=r.inserted, skipped_invalid=r.skipped_invalid, errors=r.errors)


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
    unresolved_count: int = 0
    diffs: list[DiffOut]


def _to_out(r: reconciliation_service.ReconciliationResult) -> ReconciliationOut:
    return ReconciliationOut(
        rule=r.rule,
        total_diffs=r.total_diffs,
        ok_count=r.ok_count,
        warning_count=r.warning_count,
        error_count=r.error_count,
        unresolved_count=r.unresolved_count,
        diffs=[DiffOut(**d.__dict__) for d in r.diffs],
    )


@router.get("/reconciliation/{rule}", response_model=ReconciliationOut)
def run_one_rule(
    rule: str,
    period_start: Optional[date] = Query(None, description="账期起 (含)"),
    period_end: Optional[date] = Query(None, description="账期止 (含)"),
    db: Session = Depends(get_db),
):
    fn = reconciliation_service.RULES.get(rule)
    if fn is None:
        raise HTTPException(404, f"unknown rule {rule!r}; available: {list(reconciliation_service.RULES)}")
    r = fn(db, record_exceptions=False, period_start=period_start, period_end=period_end)
    return _to_out(r)


@router.get("/reconciliation", response_model=dict[str, ReconciliationOut])
def run_all_rules(
    period_start: Optional[date] = Query(None, description="账期起 (含)"),
    period_end: Optional[date] = Query(None, description="账期止 (含)"),
    db: Session = Depends(get_db),
):
    results = reconciliation_service.run_all(
        db, record_exceptions=False, period_start=period_start, period_end=period_end,
    )
    return {name: _to_out(r) for name, r in results.items()}
