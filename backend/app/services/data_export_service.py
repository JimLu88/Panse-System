# -*- coding: utf-8 -*-
"""全类目 Excel 导出 + 存档轮转 (用户需求 2026-06-12)。

- 按 table_explorer.ENTITY_MODELS 每个类目一个 Sheet, 全行导出。
- 每个 Sheet 末列「异常批注」: 该行若有未处理(open)异常, 写进去 + 加单元格批注(Comment)。
- 导出后归档到「资料存档库」(ImportedFile kind=full_export); 超过 MAX_KEEP 份自动删最早(轮转)。
复用 exceptions_export_service 的源表键/取值助手, 不重复实现。
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.table_explorer import ENTITY_MODELS
from app.models.exception import DataException
from app.models.import_file import ImportedFile
from app.services import import_storage
from app.services.exceptions_export_service import (
    _SEVERITY_CN, _cell, _join_notes, _key_column,
)

MAX_KEEP = 30          # 资料存档库里全量导出最多留几份, 超出删最早 (用户拍板)
_INVALID_SHEET = re.compile(r"[\[\]:*?/\\]")

# 常见英文字段 → 中文表头兜底 (ENTITY_SCHEMAS 没覆盖的表/列用这个; 内容不翻, 仅表头)
_COMMON_HEADER_CN = {
    "id": "ID", "code": "编码", "name": "名称", "remark": "备注", "unit": "单位",
    "spec": "规格", "price": "单价", "amount": "金额", "qty": "数量", "status": "状态",
    "created_at": "创建时间", "updated_at": "更新时间", "warehouse": "仓库",
    "material_code": "物料编码", "material_name": "物料名称", "product_code": "产品编码",
    "product_name": "产品名称", "sku": "SKU", "sku_code": "SKU编码", "order_no": "订单号",
    "customer_name": "客户", "customer_phone": "电话", "platform": "平台",
    "order_date": "下单日期", "ship_date": "发货日期", "paid_amount": "实付金额",
    "tracking_no": "物流单号", "carrier": "快递公司", "is_custom": "是否定制",
    "is_refill": "是否补单", "lead_time_days": "提前期(天)", "priority": "优先级",
    "physical_qty": "物理库存", "locked_qty": "锁定库存", "safety_stock": "安全库存",
    "size_type": "尺寸类型", "is_discontinued": "已停产", "base_material_code": "基础物料",
    "primary_supplier_id": "主供应商", "alt_supplier_ids": "备选供应商",
    "area": "面积", "width_mm": "宽(mm)", "height_mm": "高(mm)",
    "sub_name": "副名称", "image_url": "图片链接", "category": "类目", "brand": "品牌",
    "listing_status": "上架状态", "main_material": "主材", "aux_material": "辅材",
    "import_job_id": "导入批次", "import_batch_id": "导入批次", "is_factory_provided": "工厂提供",
    "qty_required": "需求数量", "purchase_no": "采购单号", "self_delivered": "自送",
    "order_id": "订单ID", "factory_order_no": "工厂单号", "platform_order_no": "平台订单号",
    "transaction_no": "交易流水号", "transaction_time": "交易时间", "balance": "余额",
    "counterparty": "对方", "account": "账户", "bill_date": "账单日期", "service_type": "服务类型",
}


def _cn_header(entity_key: str, col: str) -> str:
    """英文字段名 → 中文表头: 优先取 ENTITY_SCHEMAS 字段 desc(去括号), 再兜底常用映射, 最后原样。"""
    from app.services.excel_schemas import ENTITY_SCHEMAS
    sch = ENTITY_SCHEMAS.get(entity_key)
    if sch and col in sch.get("fields", {}):
        desc = sch["fields"][col].get("desc")
        if desc:
            return re.split(r"[（(]", desc)[0].strip() or col
    return _COMMON_HEADER_CN.get(col, col)


def _safe_sheet_name(label: str, used: set[str]) -> str:
    """openpyxl Sheet 名: 去非法字符 []:*?/\\, ≤31 字符, 去重。"""
    name = _INVALID_SHEET.sub("·", (label or "表").strip())[:31] or "表"
    base = name
    i = 2
    while name in used:
        suffix = f"({i})"
        name = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def _exception_notes(db: Session, table_name: str) -> dict[str, list[str]]:
    """该源表所有 open 异常 → {source_pk字符串: [批注...]}。"""
    excs = db.execute(
        select(DataException).where(
            DataException.source_table == table_name,
            DataException.status == "open",
        )
    ).scalars().all()
    out: dict[str, list[str]] = {}
    for e in excs:
        if e.source_pk:
            out.setdefault(str(e.source_pk), []).append(
                f"[{_SEVERITY_CN.get(e.severity, e.severity)}] {e.description}")
    return out


def build_full_export_workbook(db: Session):
    """全类目工作簿: 每个 ENTITY_MODELS 一个 Sheet, 中文表头 + 全行 + 末列异常批注 + 配色。"""
    import openpyxl
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    # 配色: 表头深蓝白字, 异常列头红, 异常行浅黄高亮 (用户要求颜色区隔, 方便浏览)
    head_fill = PatternFill("solid", fgColor="1F4E79")
    head_font = Font(bold=True, color="FFFFFF", size=11)
    exc_head_fill = PatternFill("solid", fgColor="C0392B")
    exc_row_fill = PatternFill("solid", fgColor="FFF3CD")
    center = Alignment(horizontal="center", vertical="center")

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used: set[str] = set()

    for key, cfg in ENTITY_MODELS.items():
        model = cfg["model"]
        label = cfg.get("label", key)
        table_name = model.__tablename__
        ws = wb.create_sheet(_safe_sheet_name(label, used))
        cols = [c.key for c in model.__table__.columns]
        headers = [_cn_header(key, c) for c in cols] + ["异常批注"]
        ws.append(headers)

        notes = _exception_notes(db, table_name)
        key_col = _key_column(model)
        consumed: set[str] = set()
        exc_col_idx = len(cols) + 1

        for r in db.execute(select(model)).scalars().all():
            cand = {str(getattr(r, "id", "") or "")}
            if key_col != "id":
                cand.add(str(getattr(r, key_col, "") or ""))
            matched: list[str] = []
            for k in cand:
                if k and k in notes:
                    matched.extend(notes[k])
                    consumed.add(k)
            note = _join_notes(matched) if matched else None
            ws.append([_cell(getattr(r, c, None)) for c in cols] + [note])
            if note:
                rid = ws.max_row
                cell = ws.cell(row=rid, column=exc_col_idx)
                cell.comment = Comment(note, "异常中心")
                # 有异常的整行浅黄高亮, 一眼能看到
                for ci in range(1, exc_col_idx + 1):
                    ws.cell(row=rid, column=ci).fill = exc_row_fill

        # 表头样式 + 冻结首行 + 列宽
        for ci, h in enumerate(headers, start=1):
            c = ws.cell(row=1, column=ci)
            c.fill = exc_head_fill if ci == exc_col_idx else head_fill
            c.font = head_font
            c.alignment = center
            ws.column_dimensions[get_column_letter(ci)].width = min(max(len(str(h)) * 2 + 4, 10), 42)
        ws.freeze_panes = "A2"
        ws.row_dimensions[1].height = 20

        # 源表里定位不到行的异常 (行已删/键已变) 也别丢, 附在表尾
        orphans = [(k, ns) for k, ns in notes.items() if k not in consumed]
        if orphans:
            ws.append([])
            tip = ws.cell(row=ws.max_row + 1, column=1,
                          value="── 以下异常的关联行在本表已找不到 (行已删 / 业务键已变 / 异常已修复待复核) ──")
            tip.font = Font(bold=True, color="C0392B")
            for k, ns in orphans:
                ws.append([k] + [None] * (len(cols) - 1) + ["; ".join(ns)])

    if not wb.sheetnames:
        ws = wb.create_sheet("空")
        ws.append(["系统当前没有可导出的类目。"])
    return wb


def rotate_full_exports(db: Session, *, keep: int = MAX_KEEP) -> int:
    """资料存档库里 kind=full_export 只留最新 keep 份, 超出删最早(连磁盘文件)。返回删除数。"""
    recs = db.execute(
        select(ImportedFile).where(ImportedFile.kind == "full_export")
        .order_by(ImportedFile.id.desc())
    ).scalars().all()
    removed = 0
    for old in recs[keep:]:
        if import_storage.delete_record(db, old.id):
            removed += 1
    return removed


def run_full_export(db: Session, *, uploaded_by: Optional[str] = None) -> dict:
    """生成全类目 Excel → 存「资料存档库」→ 轮转(留≤30)。返回 {content, filename, ...}。"""
    wb = build_full_export_workbook(db)
    buf = io.BytesIO()
    wb.save(buf)
    content = buf.getvalue()
    today = date.today()
    filename = f"全量导出_{today.isoformat()}.xlsx"
    res = import_storage.archive(
        db, content=content, original_name=filename, kind="full_export",
        source="web", uploaded_by=uploaded_by,
        row_summary={"sheets": len(wb.sheetnames), "exported_at": today.isoformat()},
    )
    removed = rotate_full_exports(db)
    db.commit()
    return {
        "content": content, "filename": filename,
        "file_id": res.file.id, "sheets": len(wb.sheetnames), "rotated_removed": removed,
    }
