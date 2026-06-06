"""通用 Excel importer API (业务需求).

POST /api/importer/preview   上传 Excel → 返回 sheets + 列名 + 前 5 行 + AI 推荐 mapping
POST /api/importer/commit    提交 mapping + sheet → 入库 + 报告

支持: delivery_note (送货单) / factory_order (工厂订单)。
"""
from __future__ import annotations

import asyncio
import base64
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
import io

from app.database import get_db
from app.dependencies import require_role
from app.upload_guard import require_xlsx
from app.models.auth import User
from app.models.import_job import ImportJob
from app.services import excel_importer
from app.services.excel_schemas import ENTITY_SCHEMAS, list_entity_types

router = APIRouter(prefix="/api/importer", tags=["importer"])


# ----------------------------- 元数据 ---------------------------- #


class EntityFieldOut(BaseModel):
    name: str
    type: str
    required: bool
    desc: str
    aliases: list[str] = []


class EntityTypeOut(BaseModel):
    value: str
    label: str
    description: str
    fields: list[EntityFieldOut]


@router.get("/entity-types", response_model=list[EntityTypeOut])
def get_entity_types():
    """前端拉支持的 entity 类型 + 每个 entity 的字段定义 (展示用)."""
    out: list[EntityTypeOut] = []
    for meta in list_entity_types():
        schema = ENTITY_SCHEMAS[meta["value"]]
        fields = [
            EntityFieldOut(
                name=fn, type=f.get("type", "str"),
                required=f.get("required", False),
                desc=f.get("desc", ""), aliases=f.get("aliases", []),
            )
            for fn, f in schema["fields"].items()
        ]
        out.append(EntityTypeOut(
            value=meta["value"], label=meta["label"],
            description=meta["description"], fields=fields,
        ))
    return out


# ----------------------------- preview --------------------------- #


class SheetPreviewOut(BaseModel):
    sheet_name: str
    row_count: int
    column_names: list[str]
    sample_rows: list[list]
    suggested_entity: Optional[str] = None
    suggested_mapping: dict[str, str] = {}
    notes: list[str] = []


class PreviewOut(BaseModel):
    file_b64: str                # 前端 commit 时回传, 服务端不存中间状态
    sheets: list[SheetPreviewOut]


@router.post("/preview", response_model=PreviewOut)
async def preview(
    file: UploadFile = File(...),
    entity_type: Optional[str] = None,   # 不传 → 让 AI 自动判
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """上传 Excel, 解析每个 sheet + 调 AI 推荐 mapping. 不入库."""
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    require_xlsx(content)   # 按文件头校验, 拒绝伪造扩展名的非 Excel 文件
    # 业务需求: 历史对账单内嵌图片可能 100MB+, 上限放到 200MB
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(413, "文件超过 200MB, 请拆分或先剔除图片")

    try:
        previews = excel_importer.preview_excel(content)
    except excel_importer.ImporterError as e:
        raise HTTPException(400, str(e))

    # 每个 sheet 跑一次 AI 推断 (失败不抛, notes 里说明)
    for p in previews:
        if not p.column_names:
            continue
        excel_importer.infer_mapping(db, preview=p, entity_type=entity_type)

    db.commit()  # 任何 settings 缓存读不会脏写, 但保持事务整洁

    return PreviewOut(
        file_b64=base64.b64encode(content).decode("ascii"),
        sheets=[
            SheetPreviewOut(
                sheet_name=p.sheet_name, row_count=p.row_count,
                column_names=p.column_names, sample_rows=p.sample_rows,
                suggested_entity=p.suggested_entity,
                suggested_mapping=p.suggested_mapping,
                notes=p.notes,
            )
            for p in previews
        ],
    )


# ----------------------------- commit ---------------------------- #


class CommitIn(BaseModel):
    file_b64: str
    sheet_name: str
    entity_type: str = Field(..., pattern=r"^(delivery_note|factory_order|alipay_flow)$")
    mapping: dict[str, str]   # target_field -> excel_column
    auto_create_suppliers: bool = True
    auto_match_orders: bool = True
    dry_run: bool = False


class ImportReportOut(BaseModel):
    entity_type: str
    sheet_name: str
    total_rows: int
    inserted_parents: int
    inserted_children: int
    skipped_rows: int
    matched_lines: int
    auto_created_suppliers: list[str]
    errors: list[str]
    warnings: list[str]
    # on_conflict='ask' 下重导命中已有记录的差异 + 未映射列, 暴露给调用方避免"静默丢弃"
    conflicts: list[dict] = []
    unmapped_columns: list[str] = []


@router.post("/commit", response_model=ImportReportOut)
def commit_import(
    payload: CommitIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    try:
        file_bytes = base64.b64decode(payload.file_b64.encode("ascii"))
    except Exception as e:
        raise HTTPException(400, f"file_b64 解码失败: {e}")

    try:
        report = excel_importer.commit_sheet(
            db,
            file_bytes=file_bytes,
            sheet_name=payload.sheet_name,
            entity_type=payload.entity_type,
            mapping=payload.mapping,
            auto_create_suppliers=payload.auto_create_suppliers,
            auto_match_orders=payload.auto_match_orders,
            dry_run=payload.dry_run,
        )
    except excel_importer.ImporterError as e:
        raise HTTPException(400, str(e))
    if not payload.dry_run:
        db.commit()
    return ImportReportOut(
        entity_type=report.entity_type, sheet_name=report.sheet_name,
        total_rows=report.total_rows,
        inserted_parents=report.inserted_parents,
        inserted_children=report.inserted_children,
        skipped_rows=report.skipped_rows,
        matched_lines=report.matched_lines,
        auto_created_suppliers=report.auto_created_suppliers,
        errors=report.errors, warnings=report.warnings,
        conflicts=report.conflicts, unmapped_columns=report.unmapped_columns,
    )


# ----------------------------- 异步导入 (业务需求 6) ---------------- #


class CommitAsyncOut(BaseModel):
    job_id: int
    status: str
    sheet_name: str
    entity_type: str


@router.post("/commit-async", response_model=CommitAsyncOut, status_code=202)
def commit_import_async(
    payload: CommitIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """100MB 大文件用这个: 立刻返回 job_id, 后台 ThreadPoolExecutor 跑.

    前端调 GET /api/importer/jobs/{job_id} 轮询。
    """
    try:
        file_bytes = base64.b64decode(payload.file_b64.encode("ascii"))
    except Exception as e:
        raise HTTPException(400, f"file_b64 解码失败: {e}")

    from app.services import import_job_service
    job = import_job_service.submit_import(
        db,
        file_bytes=file_bytes,
        sheet_name=payload.sheet_name,
        entity_type=payload.entity_type,
        mapping=payload.mapping,
        user_id=getattr(user, "id", None),
        auto_create_suppliers=payload.auto_create_suppliers,
        auto_match_orders=payload.auto_match_orders,
    )
    return CommitAsyncOut(
        job_id=job.id, status=job.status,
        sheet_name=job.sheet_name, entity_type=job.entity_type,
    )


class ImportJobOut(BaseModel):
    id: int
    user_id: Optional[int]
    entity_type: str
    sheet_name: str
    status: str
    total_rows: int
    processed_rows: int
    progress_pct: float
    error: Optional[str]
    report: Optional[dict]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]


def _job_out(j) -> ImportJobOut:
    pct = (j.processed_rows / j.total_rows * 100) if j.total_rows else 0.0
    return ImportJobOut(
        id=j.id, user_id=j.user_id,
        entity_type=j.entity_type, sheet_name=j.sheet_name,
        status=j.status,
        total_rows=j.total_rows, processed_rows=j.processed_rows,
        progress_pct=round(pct, 1),
        error=j.error, report=j.report,
        created_at=j.created_at.isoformat(),
        started_at=j.started_at.isoformat() if j.started_at else None,
        completed_at=j.completed_at.isoformat() if j.completed_at else None,
    )


@router.get("/jobs/{job_id}", response_model=ImportJobOut)
def get_import_job(
    job_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    from app.services import import_job_service
    j = import_job_service.get_job(db, job_id)
    if j is None:
        raise HTTPException(404, "作业不存在")
    return _job_out(j)


@router.get("/jobs", response_model=list[ImportJobOut])
def list_import_jobs(
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    from app.services import import_job_service
    rows = import_job_service.list_jobs(db, limit=limit)
    return [_job_out(j) for j in rows]


@router.post("/jobs/{job_id}/cancel", response_model=ImportJobOut)
def cancel_import_job(
    job_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """请求取消作业. worker 在下一次 progress tick (50 行) 内退出.

    已结束的作业 (done/failed/cancelled) 调用是 no-op, 直接返回当前状态。
    """
    from app.services import import_job_service
    j = import_job_service.request_cancel(db, job_id)
    if j is None:
        raise HTTPException(404, "作业不存在")
    return _job_out(j)


@router.post("/jobs/{job_id}/rollback", response_model=dict)
def rollback_import_job(
    job_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),   # 删数据的破坏性操作, 收紧到仅 admin
):
    """回滚导入批次: 删除所有 import_job_id=job_id 的行, 并标记作业为 rolled_back."""
    from app.models.finance import AlipayFlow, FactoryReconciliation
    from app.models.order import FactoryOrder, Order
    from app.models.supplier import DeliveryNote, DeliveryNoteLine

    job = db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(404, "作业不存在")
    if job.status == "rolled_back":
        raise HTTPException(400, "该作业已回滚")
    if job.status not in ("done", "cancelled", "failed"):
        raise HTTPException(400, f"作业状态为 {job.status}, 无法回滚 (只能回滚已完成/失败/取消的作业)")

    deleted: dict[str, int] = {}
    rows_by_table: dict[str, list] = {}
    for model, label in [
        (DeliveryNoteLine, "delivery_note_lines"),
        (DeliveryNote, "delivery_notes"),
        (AlipayFlow, "alipay_flows"),
        (Order, "orders"),
        (FactoryOrder, "factory_orders"),
        (FactoryReconciliation, "factory_reconciliations"),
    ]:
        rows = db.execute(
            select(model).where(model.import_job_id == job_id)
        ).scalars().all()
        rows_by_table[label] = rows
        deleted[label] = len(rows)

    # 删前进回收站 (storage/recycle_bin/*.json), 防误回滚后数据不可恢复。
    # 写盘失败不阻断回滚 (用户已明确要回滚), 但明确告警, 不静默丢失安全网。
    from app.services import recycle_bin
    archive_path = None
    archive_warning = None
    try:
        archive_path = recycle_bin.archive(
            rows_by_table, batch_ref=f"import_job:{job_id}", reason=f"回滚导入作业 {job_id}")
    except Exception as e:
        import logging
        archive_warning = f"回收站快照写入失败 (仍继续回滚, 数据将不可恢复): {e}"
        logging.getLogger("panse.importer").warning(archive_warning)

    for rows in rows_by_table.values():
        for row in rows:
            db.delete(row)

    job.status = "rolled_back"
    db.commit()
    return {"job_id": job_id, "deleted": deleted, "total_deleted": sum(deleted.values()),
            "recycle_bin": archive_path, "recycle_bin_warning": archive_warning}


@router.get("/recycle-bin")
def list_recycle_bin(_: User = Depends(require_role("admin"))):
    """列出回收站快照 (回滚/删除前的数据 JSON 备份, 在 storage/recycle_bin/)."""
    from app.services import recycle_bin
    return {"items": recycle_bin.list_archives()}


@router.get("/recycle-bin/{filename}")
def get_recycle_bin_file(filename: str, _: User = Depends(require_role("admin"))):
    """查看/下载某个回收站快照的完整内容 (被回滚删除的数据, 可据此人工核对或重导)."""
    from app.services import recycle_bin
    data = recycle_bin.read_archive(filename)
    if data is None:
        raise HTTPException(404, "回收站文件不存在或文件名非法")
    return data


@router.post("/recycle-bin/{filename}/restore")
def restore_recycle_bin(filename: str, db: Session = Depends(get_db),
                        _: User = Depends(require_role("admin"))):
    """一键还原: 把回收站快照的数据重新插回各表 (保留原主键、保住外键; 已存在则跳过)."""
    from app.services import recycle_bin
    result = recycle_bin.restore(db, filename)
    if result.get("error") == "not_found":
        raise HTTPException(404, "回收站文件不存在或文件名非法")
    return result


# ----------------------------- 智能导入 (Phase 14) ----------------- #


@router.post("/smart-analyze")
async def smart_analyze(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """业务: 上传任意 Excel → AI 分析每个 sheet → 返回完整 plan.

    返回 {file_b64, sheets: [SheetAnalysis...]}.
    """
    import logging
    import time as _time
    _log = logging.getLogger("panse.smart_import")
    from app.services import smart_import_service
    content = await file.read()
    _log.info("[smart-analyze] 收到文件 %s, 大小 %.1f KB",
              getattr(file, "filename", "?"), len(content) / 1024)
    if not content:
        _log.warning("[smart-analyze] 空文件, 拒绝")
        raise HTTPException(400, "空文件")
    require_xlsx(content)   # 按文件头校验, 拒绝伪造扩展名的非 Excel 文件
    if len(content) > 200 * 1024 * 1024:
        _log.warning("[smart-analyze] 文件超 200MB, 拒绝")
        raise HTTPException(413, "文件超过 200MB")
    t0 = _time.monotonic()
    try:
        result = await asyncio.to_thread(smart_import_service.smart_analyze, db, content)
    except excel_importer.ImporterError as e:
        _log.warning("[smart-analyze] 解析失败 (ImporterError): %s", e)
        raise HTTPException(400, str(e))
    except Exception as e:
        # 关键: 任何其他异常都记完整 traceback, 否则前端只看到 500 不知道为什么
        _log.exception("[smart-analyze] 未预期异常: %s", e)
        raise HTTPException(500, f"分析失败: {type(e).__name__}: {e}")
    dur = (_time.monotonic() - t0) * 1000
    _log.info("[smart-analyze] 完成: %d 个 sheet, 耗时 %.0fms",
              len(result.sheets), dur)
    return {
        "file_b64": base64.b64encode(content).decode("ascii"),
        **smart_import_service.to_dict(result),
    }


class SmartCommitItem(BaseModel):
    sheet_name: str
    entity_type: str
    mapping: dict[str, str]
    header_row: int = 1
    dry_run: bool = False
    # 重导冲突策略: ask (默认, 记差异待裁决) / overwrite (采用新值) / keep (保留原值)
    on_conflict: str = "ask"
    # 支付宝流水: sheet 无账户列时, 用此账户名填充每行
    sheet_account: Optional[str] = None


class SmartCommitIn(BaseModel):
    file_b64: str
    plan: list[SmartCommitItem]


def _run_post_import_bg(summary: dict) -> None:
    """后台跑「导入后 AI 逻辑核查 + 运营分析」(独立 session), 不阻塞导入响应。

    结果(异常)照常写入异常中心, 用户稍后在异常页可见。核查失败绝不影响导入结果。
    """
    from app.database import SessionLocal
    from app.services import post_import_ai_service
    db = SessionLocal()
    try:
        post_import_ai_service.run_after_import(db, summary=summary)
        db.commit()
    except Exception:  # pragma: no cover — 核查失败绝不影响导入结果
        db.rollback()
    finally:
        db.close()


@router.post("/smart-commit")
def smart_commit(
    payload: SmartCommitIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """按用户在 UI 确认的 plan 一次导多个 sheet."""
    from app.services import smart_import_service
    try:
        file_bytes = base64.b64decode(payload.file_b64.encode("ascii"))
    except Exception as e:
        raise HTTPException(400, f"file_b64 解码失败: {e}")
    reports = smart_import_service.smart_commit(
        db, file_bytes=file_bytes,
        plan=[item.model_dump() for item in payload.plan],
    )
    # 每个 sheet 在 smart_commit 内已单独 commit; 此处确保任何残留事务也被提交
    try:
        db.commit()
    except Exception:
        db.rollback()

    # 导入后 AI 逻辑核查 + 运营分析 → 后台异步执行, 导入立即返回(不再阻塞 ~6s, 避免界面卡顿/误判崩溃)
    summary = {
        r.get("entity_type", "unknown"): r.get("inserted_parents", 0)
        for r in reports if not r.get("skipped") and not r.get("error")
    }
    background_tasks.add_task(_run_post_import_bg, summary)
    return {"reports": reports,
            "post_import": {"logic_issues": 0, "analysis": None, "ai_used": False, "scheduled": True}}


# ----------------------------- 校验导出 (Phase N+1) ----------------- #


@router.post("/validate-export")
async def validate_export(
    file: UploadFile = File(...),
    _: User = Depends(require_role("admin", "operator")),
):
    """上传 Excel → 规则校验所有 sheet → 返回带标注的 xlsx 文件.

    - 问题单元格标黄
    - 每行末尾追加「导入校验」列, 说明具体错误和修改建议
    - 可入库的行标注 ✅
    返回文件名: validated_<原文件名>.xlsx
    """
    content = await file.read()
    if not content:
        raise HTTPException(400, "空文件")
    require_xlsx(content)   # 按文件头校验, 拒绝伪造扩展名的非 Excel 文件
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(413, "文件超过 200MB")

    from app.services import excel_validate_service
    try:
        annotated_bytes, results = await asyncio.to_thread(
            excel_validate_service.validate_and_annotate, content
        )
    except Exception as e:
        raise HTTPException(500, f"校验失败: {type(e).__name__}: {e}")

    orig_name = getattr(file, "filename", "export") or "export"
    if orig_name.lower().endswith(".xlsx"):
        out_name = orig_name[:-5] + "_校验.xlsx"
    else:
        out_name = orig_name + "_校验.xlsx"

    # summary for response header
    total_issues = sum(r.issue_rows for r in results)
    total_rows = sum(r.total_data_rows for r in results)

    return StreamingResponse(
        io.BytesIO(annotated_bytes),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Validate-Total-Rows": str(total_rows),
            "X-Validate-Issue-Rows": str(total_issues),
        },
    )
