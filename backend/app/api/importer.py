"""通用 Excel importer API (业务需求).

POST /api/importer/preview   上传 Excel → 返回 sheets + 列名 + 前 5 行 + AI 推荐 mapping
POST /api/importer/commit    提交 mapping + sheet → 入库 + 报告

支持: delivery_note (送货单) / factory_order (工厂订单)。
"""
from __future__ import annotations

import base64
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
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
    )
