from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.exception import DataException
from app.schemas.exception import DataExceptionOut, DataExceptionResolve
from app.services import data_quality_service, exception_fix_service

router = APIRouter(prefix="/api/exceptions", tags=["exceptions"])


class FixPayload(BaseModel):
    fields: dict[str, Any]


@router.get("", response_model=list[DataExceptionOut])
def list_exceptions(
    status: Optional[str] = Query(None),
    source_table: Optional[str] = None,
    exception_type: Optional[str] = None,
    limit: int = Query(200, le=5000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(DataException)
    if status:
        stmt = stmt.where(DataException.status == status)
    if source_table:
        stmt = stmt.where(DataException.source_table == source_table)
    if exception_type:
        stmt = stmt.where(DataException.exception_type == exception_type)
    stmt = stmt.order_by(DataException.id.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.get("/export")
def export_exceptions(
    source_table: Optional[str] = Query(None, description="只导某张源表"),
    include_ignored: bool = Query(False),
    db: Session = Depends(get_db),
):
    """异常随源表导出 (用户需求): 每个来源表一个 sheet, 整行数据 + 末列「异常批注」。"""
    import io

    from fastapi.responses import StreamingResponse

    from app.services.exceptions_export_service import build_export_workbook
    wb = build_export_workbook(db, source_table=source_table, include_ignored=include_ignored)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=exceptions_annotated.xlsx"},
    )


@router.patch("/{exception_id}/resolve", response_model=DataExceptionOut)
def resolve_exception(
    exception_id: int,
    payload: DataExceptionResolve,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    if payload.status not in {"resolved", "ignored"}:
        raise HTTPException(400, "status must be resolved or ignored")
    exc = db.get(DataException, exception_id)
    if not exc:
        raise HTTPException(404, "exception not found")
    # 复核 (用户拍板 2026-06-12): 点「已处理」先检查问题是否真修好了;
    # 仍存在 → 拒绝销账并说明原因 (force=True 跳过, 慎用)。
    if payload.status == "resolved" and not payload.force:
        from app.services.exception_recheck_service import recheck
        reason = recheck(db, exc)
        if reason:
            raise HTTPException(409, f"复核未通过, 问题仍存在: {reason}")
    exc.status = payload.status
    exc.resolved_by = payload.resolved_by
    exc.resolved_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    db.refresh(exc)
    return exc


class ImportConflictResolveIn(BaseModel):
    choice: str  # "new" = 采用导入值, "old" = 保留现有值


@router.post("/{exception_id}/resolve-import-conflict", response_model=DataExceptionOut)
def resolve_import_conflict(
    exception_id: int,
    payload: ImportConflictResolveIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("admin", "operator")),
):
    """裁决表格导入冲突: choice=new 采用导入新值, choice=old 保留现有值."""
    if payload.choice not in ("new", "old"):
        raise HTTPException(400, "choice must be 'new' or 'old'")
    exc = db.get(DataException, exception_id)
    if not exc:
        raise HTTPException(404, "exception not found")
    if exc.exception_type != "import_conflict":
        raise HTTPException(400, "此异常不是表格导入冲突类型")

    if payload.choice == "new":
        ctx = exc.context or {}
        new_values: dict = ctx.get("new_values", {})
        source_table = exc.source_table
        source_pk = exc.source_pk
        if new_values and source_table and source_pk:
            _apply_import_conflict_new_values(db, source_table, source_pk, new_values)

    exc.status = "resolved"
    exc.resolved_by = current_user.username if hasattr(current_user, "username") else str(current_user.id)
    exc.resolved_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    db.refresh(exc)
    return exc


def _apply_import_conflict_new_values(db: Session, source_table: str, source_pk: str, new_values: dict) -> None:
    """按 source_table/source_pk 找到记录并应用 new_values."""
    from sqlalchemy import text as sa_text
    # 表名 → (SQLAlchemy 模型, PK 字段名)
    _TABLE_MAP = {
        "orders": ("app.models.order", "Order", "order_no"),
        "factory_orders": ("app.models.order", "FactoryOrder", "factory_order_no"),
        "alipay_flows": ("app.models.finance", "AlipayFlow", "transaction_no"),
        "products": ("app.models.product", "Product", "code"),
        "materials": ("app.models.material", "Material", "code"),
        "pricing_sku": ("app.models.pricing", "PricingSku", "sku_code"),
        "refill_records": ("app.models.finance", "RefillRecord", "order_no"),
        "factory_reconciliations": ("app.models.finance", "FactoryReconciliation", None),
        "outsourcing_expenses": ("app.models.marketing", "OutsourcingExpense", "alipay_flow_no"),
        "after_sales": ("app.models.marketing", "AfterSales", "platform_order_no"),
        "competitor_prices": ("app.models.competitor", "CompetitorPrice", None),
        "samples": ("app.models.marketing", "Sample", "sample_no"),
        "account_balances": ("app.models.finance", "AccountBalance", None),
        "product_inventory": ("app.models.inventory", "ProductInventory", None),
        "part_inventory": ("app.models.inventory", "PartInventory", None),
        "delivery_notes": ("app.models.supplier", "DeliveryNote", "note_no"),
    }
    entry = _TABLE_MAP.get(source_table)
    if not entry:
        return  # 不认识的表, 跳过
    module_path, class_name, pk_field = entry
    if pk_field is None:
        return  # 复合键表不支持自动应用, 用户手动处理
    try:
        import importlib
        mod = importlib.import_module(module_path)
        model_cls = getattr(mod, class_name)
    except (ImportError, AttributeError):
        return

    from sqlalchemy import select
    record = db.execute(
        select(model_cls).where(getattr(model_cls, pk_field) == source_pk)
    ).scalar_one_or_none()
    if record is None:
        return
    for field_name, value in new_values.items():
        if hasattr(record, field_name):
            setattr(record, field_name, value)
    db.flush()


@router.post("/{exception_id}/fix", response_model=DataExceptionOut)
def fix_exception(
    exception_id: int,
    payload: FixPayload,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """内联补填: 写回源表字段并解除异常."""
    try:
        exc = exception_fix_service.fix_exception(db, exception_id, payload.fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return exc


@router.post("/run-data-quality", response_model=dict)
def run_data_quality(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """触发全部数据完整性扫描, 返回各规则发现数."""
    results = data_quality_service.run_all(db)
    return results


@router.post("/recheck-all", response_model=dict)
def recheck_all(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """重新复核全部异常: 对「有检查器」的类型重跑判定, 把条件已不成立(已修复)的批量销账。
    没检查器的类型不动 (留人工)。返回 {总关闭数, 按类型}。"""
    from app.services.exception_recheck_service import bulk_close_resolved
    closed = bulk_close_resolved(db)
    return {"closed": sum(closed.values()), "by_type": closed}


@router.post("/refresh", response_model=dict)
def refresh_exceptions(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """异常刷新 (用户 2026-06-24): 一键 ①重新全量扫描, 把新异常纳入 ②把已解决的批量销账。
    = 扫描器(外键/库存/数值…) + 数据完整性(B 系列) + 复核销账, 等价于导入后的自动流程, 手动触发。"""
    from sqlalchemy import func, select as _select
    from app.models.exception import DataException as _DE
    from app.services import scanner_service
    from app.services.exception_recheck_service import bulk_close_resolved

    open_before = db.execute(_select(func.count(_DE.id)).where(_DE.status == "open")).scalar() or 0
    scanner_service.run_all(db)        # 外键断裂/负库存/数值范围 等扫描器
    data_quality_service.run_all(db)   # 数据完整性 B 系列检查
    db.flush()
    closed = bulk_close_resolved(db)   # 已解决的批量销账
    open_now = db.execute(_select(func.count(_DE.id)).where(_DE.status == "open")).scalar() or 0
    return {
        "open_before": open_before,
        "open_now": open_now,
        "new_found": max(0, open_now - open_before + sum(closed.values())),
        "closed": sum(closed.values()),
        "closed_by_type": closed,
    }


@router.get("/counts-by-type", response_model=dict)
def counts_by_type(
    status: str = Query("open"),
    db: Session = Depends(get_db),
):
    """每种 exception_type 的待处理数, 供顶栏健康度角标和对账页使用."""
    rows = (
        db.query(DataException.exception_type, func.count(DataException.id))
        .filter(DataException.status == status)
        .group_by(DataException.exception_type)
        .all()
    )
    return {r[0]: r[1] for r in rows}


@router.get("/summary", response_model=dict)
def exceptions_summary(
    status: str = Query("open"),
    db: Session = Depends(get_db),
):
    """一次性返回 按类型 / 按严重度 / 总数 的聚合 (GROUP BY, 不拉明细行).

    供异常页表头与各分组显示「准确总数」, 即使明细列表被分页/截断, 计数仍正确。
    """
    by_type = dict(
        db.query(DataException.exception_type, func.count(DataException.id))
        .filter(DataException.status == status)
        .group_by(DataException.exception_type)
        .all()
    )
    by_severity = dict(
        db.query(DataException.severity, func.count(DataException.id))
        .filter(DataException.status == status)
        .group_by(DataException.severity)
        .all()
    )
    return {
        "total": sum(by_type.values()),
        "by_type": by_type,
        "by_severity": by_severity,
    }


@router.get("/open-count", response_model=dict)
def open_count(
    exclude_info: bool = Query(
        True, description="排除 info 级 (如导入时自动打的「定制编码」标记等噪音)"),
    db: Session = Depends(get_db),
):
    """顶栏健康度角标用: 返回 {count: N}.

    默认排除 info 级 — 导入时每个定制编码 SKU 都会打一条 info 标记, 不是真问题,
    全算进红色角标会动辄上千条吓人。角标只统计真正要处理的 warning/critical。
    """
    q = db.query(func.count(DataException.id)).filter(DataException.status == "open")
    if exclude_info:
        q = q.filter(DataException.severity != "info")
    return {"count": q.scalar() or 0}


@router.post("/autofill/generate", response_model=dict)
def autofill_generate(
    dry_run: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """B5: 从订单反推生成工厂下单草稿 (支持 dry_run)."""
    from app.services import autofill_service
    result = autofill_service.run_all(db, dry_run=dry_run)
    if not dry_run:
        db.commit()
    return result
