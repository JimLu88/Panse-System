"""结算账单(微信/聚合 billDetail)导入 + 列表 + 汇总。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.prepay_ledger import PrepayLedger
from app.models.settlement import OrderSettlement
from app.services import (
    import_storage, order_reconciliation_service, prepay_import_service,
    recon_config_service, recon_diagnostics_service, settlement_import_service,
)

router = APIRouter(prefix="/api/settlements", tags=["settlements"])


@router.post("/import")
def import_settlements(
    file: UploadFile = File(...),
    source: str = Query("wechat", description="wechat(聚合) / alipay"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    content = file.file.read()
    arch = import_storage.archive(
        db, content=content, original_name=file.filename or "settlement.xlsx",
        kind="settlement", source="web", uploaded_by=getattr(_, "username", None),
    )
    result = settlement_import_service.import_bill(db, content, source=source)
    if isinstance(result, dict):
        import_storage.update_summary(db, arch.file.id, result)
        result = {**result, "archived_file_id": arch.file.id, "duplicate_upload": arch.is_duplicate}
    db.commit()
    from app.services import realtime_sync_service
    realtime_sync_service.trigger("import:settlement")
    return result


@router.get("/summary")
def get_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    return settlement_import_service.summary(db)


@router.post("/route-alipay")
def route_alipay(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """把支付宝企业号订单级分账(T200P: 货款/软件费/消费券代付扣回)路由进 order_settlements,
    让这些订单进入逐笔结算对账。幂等, 可重复跑 (日常自动跑, 这里供手动补一次)。"""
    result = settlement_import_service.route_alipay_flows(db)
    db.commit()
    return result


@router.get("/coupon-pending")
def coupon_pending(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """消费券应补未补 (低优提醒): 平台垫付消费券扣回 vs 已补回, 约2月分批到账; 不进利润, 纯现金时序。"""
    return settlement_import_service.coupon_pending_summary(db)


@router.get("")
def list_settlements(
    limit: int = Query(100, le=2000),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    rows = db.execute(
        select(OrderSettlement)
        .order_by(OrderSettlement.settle_time.desc().nulls_last(), OrderSettlement.id.desc())
        .limit(limit)
    ).scalars().all()
    return [{
        "id": r.id, "source": r.source, "pay_no": r.pay_no, "order_no": r.order_no,
        "settle_time": r.settle_time.isoformat() if r.settle_time else None,
        "entry_type": r.entry_type, "income": float(r.income or 0), "expense": float(r.expense or 0),
        "description": r.description,
    } for r in rows]


@router.get("/reconciliation/summary")
def reconciliation_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """逐笔对账全量汇总: 应付/实付/补贴/实收/2%税/软件费 合计 + 对账状态分布 + 到账覆盖率。"""
    return order_reconciliation_service.summary(db)


@router.get("/reconciliation/gap")
def reconciliation_gap(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """到账覆盖缺口诊断: 按月铺开覆盖率 + 待补金额, 指出最该补流水/账单的几个月。"""
    return order_reconciliation_service.coverage_gap(db)


@router.get("/reconciliation/gap/{period}")
def reconciliation_gap_detail(
    period: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """Plan L1: 某月 (YYYY-MM) 待补订单清单 + 缺什么证据 + 行动指引。"""
    return order_reconciliation_service.coverage_gap_detail(db, period)


@router.post("/prepay/import")
def import_prepay(
    category: str = Query(..., description="refill_commission / refill_express / aftersales"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """导入代付台账(补单佣金/补单快递/售后 实际打款), 作为这三类对账的进项来源。"""
    raw = file.file.read()
    kind = "aftersales" if category == "aftersales" else "refill"
    arch = import_storage.archive(
        db, content=raw, original_name=file.filename or f"{category}.csv", kind=kind, source="web",
    )
    from app.services import tabular
    text = tabular.to_csv_text(raw, file.filename)
    r = prepay_import_service.import_prepay_csv(db, text, category=category)
    import_storage.update_summary(db, arch.file.id, {
        "inserted": r.inserted, "skipped_duplicate": r.skipped_duplicate, "category": category,
    })
    db.commit()
    from app.services import realtime_sync_service
    realtime_sync_service.trigger("import:prepay")
    return {
        "inserted": r.inserted, "skipped_invalid": r.skipped_invalid,
        "skipped_duplicate": r.skipped_duplicate, "unmapped_columns": r.unmapped_columns,
        "errors": r.errors, "archived_file_id": arch.file.id, "duplicate_upload": arch.is_duplicate,
    }


@router.get("/prepay/summary")
def prepay_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    return prepay_import_service.summary(db)


@router.get("/prepay")
def list_prepay(
    category: str | None = Query(None),
    limit: int = Query(300, le=2000),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    stmt = select(PrepayLedger).order_by(PrepayLedger.pay_date.desc().nulls_last(), PrepayLedger.id.desc())
    if category:
        stmt = stmt.where(PrepayLedger.category == category)
    rows = db.execute(stmt.limit(limit)).scalars().all()
    return [{
        "id": r.id, "category": r.category, "pay_no": r.pay_no, "order_no": r.order_no,
        "pay_date": r.pay_date.isoformat() if r.pay_date else None,
        "amount": float(r.amount or 0), "payee": r.payee, "remark": r.remark,
    } for r in rows]


@router.get("/recon-config")
def get_recon_config(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """对账/利润口径配置: 容差 + 补贴税率 + 软件费率 (全局默认 + 按店铺覆盖)。"""
    return recon_config_service.get_config(db)


@router.put("/recon-config")
def update_recon_config(
    defaults: dict | None = Body(None),
    by_shop: dict | None = Body(None),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    cfg = recon_config_service.set_config(db, defaults=defaults, by_shop=by_shop)
    db.commit()
    return cfg


@router.get("/reconciliation/diagnostics")
def reconciliation_diagnostics(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """对账诊断: 账户余额钩稽 + 孤儿流水(没人认领的钱) + 各账户流水覆盖 (揭示对账缺口在哪)。"""
    return recon_diagnostics_service.diagnostics(db)


@router.get("/reconciliation/problem-flows")
def reconciliation_problem_flows(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """所有没对上的支付宝流水(含流水号 + 原因), 供页面展开核对。"""
    rows = recon_diagnostics_service.problem_flows(db)
    return {"count": len(rows), "rows": rows}


@router.get("/reconciliation/problem-flows.xlsx")
def reconciliation_problem_flows_xlsx(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """导出所有没对上的支付宝流水为 Excel: 账户/支付宝流水号/时间/类型/金额/对手方/订单号/归类/备注/原因。"""
    import io

    import openpyxl
    from fastapi.responses import StreamingResponse

    rows = recon_diagnostics_service.problem_flows(db)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "问题流水"
    headers = ["账户", "支付宝流水号", "交易时间", "类型", "金额",
               "对手方", "关联订单号", "归类", "备注", "原因"]
    ws.append(headers)
    for r in rows:
        ws.append([
            r["account"], r["transaction_no"], r["transaction_time"], r["transaction_type"],
            r["amount"], r["counterparty"], r["related_order_no"],
            r["reconciliation_type"], r["remark"], r["reason"],
        ])
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=problem_flows.xlsx"},
    )


@router.get("/reconciliation")
def reconciliation_list(
    limit: int = Query(200, le=2000),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="matched / diff / pending"),
    channel: str | None = Query(None, description="wechat / alipay / none"),
    q: str | None = Query(None, description="订单号 / 客户名 关键词"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """逐笔对账明细 (每单一行, 四方对账 + 2%补贴税)。"""
    return order_reconciliation_service.per_order(
        db, limit=limit, offset=offset, status=status, channel=channel, q=q,
    )
