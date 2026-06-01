import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.finance import AccountBalance, AlipayFlow, LogisticsBill, RefillRecord, WanshifuBill
from app.services import (
    alipay_backfill_service,
    alipay_flow_router_service,
    alipay_import,
    balance_service,
    bill_import_service,
    email_import_service,
    factory_reconciliation_service,
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


class FactoryReconRebuildOut(BaseModel):
    periods: int
    created: int
    updated: int


@router.post("/factory-reconciliation/rebuild", response_model=FactoryReconRebuildOut)
def rebuild_factory_reconciliation(
    factory_name: Optional[str] = None, db: Session = Depends(get_db),
):
    """按自然月把工厂下单表汇总成工厂对账记录 (本期下单金额/账单金额/实付/差异)。

    导入工厂下单表后触发; 幂等, 可反复跑。
    """
    r = factory_reconciliation_service.rebuild_all_periods(db, factory_name=factory_name)
    db.commit()
    return FactoryReconRebuildOut(periods=r.periods, created=r.created, updated=r.updated)


class AlipayRouteResult(BaseModel):
    promotion_filled: int
    daily_filled: int
    outsourcing_filled: int
    purchases_created: int
    factory_flipped: int


@router.post("/alipay-flows/route", response_model=AlipayRouteResult)
def route_alipay_flows(rerun_classify: bool = True, db: Session = Depends(get_db)):
    """支付宝流水自动归类回填: 推广/日常/外包补流水号, 未分类支出建采购, 工厂翻已付款。

    rerun_classify=True 时先跑一遍 smart_matching 打 reconciliation_type。
    """
    if rerun_classify:
        smart_matching_service.run(db)
    r = alipay_flow_router_service.run_all(db)
    db.commit()
    return AlipayRouteResult(
        promotion_filled=r.promotion_filled, daily_filled=r.daily_filled,
        outsourcing_filled=r.outsourcing_filled, purchases_created=r.purchases_created,
        factory_flipped=r.factory_flipped,
    )


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


@router.get("/reconciliation/ai-diagnosis")
def reconciliation_ai_diagnosis(db: Session = Depends(get_db)):
    """对当前所有对账差异做 AI 诊断, 给出可能原因 + 建议处理优先级。

    静态路由注册在 /{rule} 之前, 避免被拦截。
    AI 未配置时返回 ai_available=false + diagnosis=None。
    """
    from app.services import ai_assistant as _ai
    from app.services.ai_assistant import collect_reconcile_findings as _cf
    findings = _cf(db)
    _log, ai = _ai.diagnose_reconciliation(db)
    return {
        "diagnosis": ai.text if ai else None,
        "findings_count": len(findings),
        "ai_available": ai is not None,
        "model": ai.model if ai else None,
    }


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


# -------- CSV 模板下载 --------

def _csv_template(headers: list[str], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # BOM for Excel
    csv.writer(buf).writerow(headers)
    return StreamingResponse(
        iter([buf.getvalue().encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# -------- 万师傅 / 物流账单 / 补单记录 列表 --------

class WanshifuBillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bill_date: Optional[date]
    order_no: Optional[str]
    service_type: Optional[str]
    amount: Decimal
    status: Optional[str]
    remark: Optional[str]


class LogisticsBillOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    bill_date: Optional[date]
    carrier: Optional[str]
    tracking_no: Optional[str]
    order_no: Optional[str]
    weight_kg: Optional[Decimal]
    freight_amount: Decimal
    remark: Optional[str]


class RefillRecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    order_no: str
    buyer_nick: Optional[str]
    refill_date: Optional[date]
    product_code: Optional[str]
    product_name: Optional[str]
    sku: Optional[str]
    qty: int
    order_amount: Optional[Decimal]
    refill_cost: Optional[Decimal]
    total_cost: Optional[Decimal]


@router.get("/wanshifu-bills", response_model=list[WanshifuBillOut])
def list_wanshifu_bills(
    year: Optional[int] = None,
    limit: int = Query(500, le=2000),
    db: Session = Depends(get_db),
):
    stmt = select(WanshifuBill)
    if year:
        from sqlalchemy import extract
        stmt = stmt.where(extract("year", WanshifuBill.bill_date) == year)
    stmt = stmt.order_by(WanshifuBill.bill_date.desc().nulls_last()).limit(limit)
    return db.execute(stmt).scalars().all()


@router.get("/logistics-bills", response_model=list[LogisticsBillOut])
def list_logistics_bills(
    carrier: Optional[str] = None,
    year: Optional[int] = None,
    limit: int = Query(500, le=2000),
    db: Session = Depends(get_db),
):
    stmt = select(LogisticsBill)
    if carrier:
        stmt = stmt.where(LogisticsBill.carrier == carrier)
    if year:
        from sqlalchemy import extract
        stmt = stmt.where(extract("year", LogisticsBill.bill_date) == year)
    stmt = stmt.order_by(LogisticsBill.bill_date.desc().nulls_last()).limit(limit)
    return db.execute(stmt).scalars().all()


@router.get("/refill-records", response_model=list[RefillRecordOut])
def list_refill_records(
    year: Optional[int] = None,
    limit: int = Query(500, le=2000),
    db: Session = Depends(get_db),
):
    stmt = select(RefillRecord)
    if year:
        from sqlalchemy import extract
        stmt = stmt.where(extract("year", RefillRecord.refill_date) == year)
    stmt = stmt.order_by(RefillRecord.refill_date.desc().nulls_last()).limit(limit)
    return db.execute(stmt).scalars().all()


@router.get("/alipay-flows/template.csv")
def alipay_template():
    """下载支付宝流水导入模板 (空白 CSV, 含正确列名)。"""
    return _csv_template(
        ["交易时间", "交易流水号", "交易类型", "交易对象", "收支金额", "关联订单号", "余额", "备注"],
        "alipay_flows_template.csv",
    )


@router.get("/wanshifu-bills/template.csv")
def wanshifu_template():
    """下载万师傅安装账单导入模板。"""
    return _csv_template(
        ["日期", "订单号", "服务类型", "金额", "状态", "备注"],
        "wanshifu_bills_template.csv",
    )


@router.get("/logistics-bills/template.csv")
def logistics_template():
    """下载物流费账单导入模板。"""
    return _csv_template(
        ["日期", "承运商", "运单号", "订单号", "重量(kg)", "运费", "备注"],
        "logistics_bills_template.csv",
    )


@router.get("/promotion-flows/template.csv")
def promotion_template():
    """下载推广记录导入模板。"""
    return _csv_template(
        ["日期", "类型", "金额", "支付宝流水号", "备注"],
        "promotion_flows_template.csv",
    )


@router.get("/refill-records/template.csv")
def refill_records_template():
    """下载补单对账导入模板。"""
    return _csv_template(
        ["订单号", "买家", "补单日期", "产品编码", "产品名", "SKU", "订单金额", "数量",
         "补单成本", "补发运费", "平台费", "佣金", "总成本"],
        "refill_records_template.csv",
    )


@router.get("/accounts/template.csv")
def account_balances_template():
    """下载账户余额导入模板。"""
    return _csv_template(
        ["账户名", "年", "月", "期初余额", "收入", "支出", "期末余额", "备注"],
        "account_balances_template.csv",
    )


# -------- 账户余额 CSV 预览/确认 (两步导入) --------

class CsvPreviewRow(BaseModel):
    row: int
    data: dict[str, Any]
    valid: bool
    reason: Optional[str] = None


class CsvPreviewResult(BaseModel):
    total: int
    valid_count: int
    invalid_count: int
    preview_rows: list[CsvPreviewRow]


def _parse_account_balances_preview(text: str) -> CsvPreviewResult:
    """解析账户余额 CSV, 返回预览行 (不写库)。"""
    from app.services.bill_import_service import _BALANCE_MAP, _decimal, _rows
    rows = _rows(text, _BALANCE_MAP)
    preview: list[CsvPreviewRow] = []
    valid = 0
    for i, rec in enumerate(rows, start=2):
        account_name = (rec.get("account_name") or "").strip()
        try:
            year = int(rec.get("period_year") or 0)
            month = int(rec.get("period_month") or 0)
        except (ValueError, TypeError):
            preview.append(CsvPreviewRow(row=i, data=rec, valid=False, reason="年/月不是数字"))
            continue
        if not account_name or not year or not month:
            preview.append(CsvPreviewRow(row=i, data=rec, valid=False,
                                         reason="账户名/年/月为必填项"))
            continue
        closing = _decimal(rec.get("closing_balance"))
        preview.append(CsvPreviewRow(
            row=i,
            data={
                "account_name": account_name, "year": year, "month": month,
                "opening_balance": str(_decimal(rec.get("opening_balance")) or ""),
                "closing_balance": str(closing or ""),
                "income": str(_decimal(rec.get("income")) or ""),
                "expense": str(_decimal(rec.get("expense")) or ""),
            },
            valid=True,
        ))
        valid += 1
    return CsvPreviewResult(
        total=len(preview),
        valid_count=valid,
        invalid_count=len(preview) - valid,
        preview_rows=preview,
    )


@router.post("/accounts/parse-csv", response_model=CsvPreviewResult)
async def parse_account_balances(file: UploadFile = File(...)):
    """第一步: 解析账户余额 CSV, 返回预览 (不写库)。
    确认后调 /accounts/confirm-csv 提交。"""
    text = await _read_csv(file)
    return _parse_account_balances_preview(text)


@router.post("/accounts/confirm-csv", response_model=BillImportResult)
async def confirm_account_balances(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """第二步: 正式导入账户余额 CSV (同账户同月 upsert)。"""
    text = await _read_csv(file)
    r = bill_import_service.import_account_balances_csv(db, text)
    db.commit()
    return BillImportResult(inserted=r.inserted, skipped_invalid=r.skipped_invalid, errors=r.errors)


# -------- 支付宝流水 CSV 预览 --------

class AlipayPreviewRow(BaseModel):
    row: int
    transaction_time: Optional[str]
    transaction_no: Optional[str]
    amount: Optional[str]
    counterparty: Optional[str]
    related_order_no: Optional[str]
    valid: bool
    reason: Optional[str] = None


class AlipayPreviewResult(BaseModel):
    total: int
    valid_count: int
    duplicate_count: int
    invalid_count: int
    preview_rows: list[AlipayPreviewRow]


@router.post("/alipay-flows/parse-csv", response_model=AlipayPreviewResult)
async def parse_alipay(
    account: str = Query(..., description="账户名"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """第一步: 解析支付宝流水 CSV, 返回预览 (不写库)。
    包含重复检测 (transaction_no 已存在的标注 duplicate)。"""
    import csv as _csv
    from io import StringIO
    from app.services.alipay_import import COLUMN_MAP, _decimal, _datetime

    text = await _read_csv(file)
    reader = _csv.DictReader(StringIO(text))
    field_map: dict[str, str] = {}
    for raw in (reader.fieldnames or []):
        norm = (raw or "").strip()
        if norm in COLUMN_MAP:
            field_map[raw] = COLUMN_MAP[norm]

    existing_nos: set[str] = set(
        db.execute(select(AlipayFlow.transaction_no)).scalars().all()
    )

    preview: list[AlipayPreviewRow] = []
    valid = dup = inv = 0
    for i, raw_row in enumerate(reader, start=2):
        rec: dict[str, Any] = {}
        for k, v in raw_row.items():
            fn = field_map.get(k)
            if fn:
                rec[fn] = v
        tx_no = (rec.get("transaction_no") or "").strip()
        amt = _decimal(rec.get("amount"))
        if not tx_no or amt is None:
            preview.append(AlipayPreviewRow(
                row=i, transaction_time=None, transaction_no=tx_no or None,
                amount=None, counterparty=None, related_order_no=None,
                valid=False, reason="流水号或金额缺失",
            ))
            inv += 1
            continue
        if tx_no in existing_nos:
            preview.append(AlipayPreviewRow(
                row=i,
                transaction_time=str(rec.get("transaction_time") or ""),
                transaction_no=tx_no,
                amount=str(amt),
                counterparty=rec.get("counterparty"),
                related_order_no=rec.get("related_order_no"),
                valid=False, reason="重复 (已存在)",
            ))
            dup += 1
            continue
        preview.append(AlipayPreviewRow(
            row=i,
            transaction_time=str(rec.get("transaction_time") or ""),
            transaction_no=tx_no,
            amount=str(amt),
            counterparty=rec.get("counterparty"),
            related_order_no=rec.get("related_order_no"),
            valid=True,
        ))
        valid += 1

    return AlipayPreviewResult(
        total=len(preview),
        valid_count=valid,
        duplicate_count=dup,
        invalid_count=inv,
        preview_rows=preview,
    )


# -------- 邮箱 IMAP 配置 & 手动触发 --------

class EmailConfigOut(BaseModel):
    host: str
    port: int
    ssl: bool
    user: str
    password_set: bool
    folder: str
    subject_filter: str
    sender_filter: str
    alipay_account: str


class EmailConfigIn(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    ssl: Optional[bool] = None
    user: Optional[str] = None
    password: Optional[str] = None
    folder: Optional[str] = None
    subject_filter: Optional[str] = None
    sender_filter: Optional[str] = None
    alipay_account: Optional[str] = None


class EmailPollResult(BaseModel):
    scanned: int
    imported: int
    skipped: int
    errors: list[str]


@router.get("/email-poll/config", response_model=EmailConfigOut)
def get_email_config(db: Session = Depends(get_db)):
    """查看邮箱 IMAP 轮询配置。"""
    return email_import_service.get_config(db)


@router.post("/email-poll/config", response_model=EmailConfigOut)
def update_email_config(payload: EmailConfigIn, db: Session = Depends(get_db)):
    """更新邮箱 IMAP 轮询配置 (只传需要修改的字段)。"""
    kwargs: dict[str, Any] = {}
    if payload.host is not None:
        kwargs["email_imap_host"] = payload.host
    if payload.port is not None:
        kwargs["email_imap_port"] = payload.port
    if payload.ssl is not None:
        kwargs["email_imap_ssl"] = payload.ssl
    if payload.user is not None:
        kwargs["email_username"] = payload.user
    if payload.password is not None:
        kwargs["email_password"] = payload.password
    if payload.folder is not None:
        kwargs["email_folder"] = payload.folder
    if payload.subject_filter is not None:
        kwargs["email_subject_filter"] = payload.subject_filter
    if payload.sender_filter is not None:
        kwargs["email_sender_filter"] = payload.sender_filter
    if payload.alipay_account is not None:
        kwargs["email_alipay_account"] = payload.alipay_account
    email_import_service.save_config(db, **kwargs)
    db.commit()
    return email_import_service.get_config(db)


@router.post("/email-poll/trigger", response_model=EmailPollResult)
def trigger_email_poll(db: Session = Depends(get_db)):
    """立即执行一次邮箱轮询 + 导入 (无需等调度器)。"""
    r = email_import_service.poll_and_import(db)
    return EmailPollResult(scanned=r.scanned, imported=r.imported,
                           skipped=r.skipped, errors=r.errors)


# -------- 对账差异 AI 诊断 --------

class ReconciliationDiagnosisOut(BaseModel):
    diagnosis: Optional[str]
    findings_count: int
    ai_available: bool
    model: Optional[str] = None
