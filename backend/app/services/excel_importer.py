"""通用 Excel importer (业务需求扩展).

流程:
    1) preview_excel(file_bytes)  → 解析每个 sheet 的 header + 前 5 行
    2) infer_mapping(preview, entity_type)  → 调 AI 推断列映射 (后台可改 mapping)
    3) commit(rows, mapping, entity_type, ...)  → 按 mapping 批量入库 + 自动跑订单匹配

未知供应商默认自动创建 (supplier_type=other), 用户可事后到供应商页补类型 / 关键字。
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

# (done_rows, total_rows) -> None; 调用方用来更新 ImportJob.processed_rows
ProgressCallback = Callable[[int, int], None]

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.order import FactoryOrder
from app.models.supplier import DeliveryNote, DeliveryNoteLine, Supplier
from app.services import delivery_matcher, settings_service
from app.services.ai_provider import AiUnavailable, build_provider
from app.services.excel_schemas import ENTITY_SCHEMAS, get_schema


# ----------------------------- 数据结构 -------------------------- #


@dataclass
class SheetPreview:
    sheet_name: str
    row_count: int                 # 总行数 (不含 header)
    column_names: list[str]        # header
    sample_rows: list[list[Any]]   # 前 5 行原值
    suggested_entity: Optional[str] = None
    suggested_mapping: dict[str, str] = field(default_factory=dict)
    # mapping: target_field -> excel_column
    notes: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    entity_type: str
    sheet_name: str
    total_rows: int
    inserted_parents: int
    inserted_children: int          # 仅 delivery_note 有子项
    skipped_rows: int
    auto_created_suppliers: list[str] = field(default_factory=list)
    matched_lines: int = 0           # delivery_note 自动匹配命中数
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ImporterError(RuntimeError):
    """importer 调用方传参错误 (前端可直接显示给用户)."""


# ----------------------------- preview --------------------------- #


def preview_excel(file_bytes: bytes, *, sample_rows: int = 5) -> list[SheetPreview]:
    """解析 Excel, 每个 sheet 返回 header + 前 N 行."""
    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as e:
        raise ImporterError(f"无法解析 Excel: {e}") from e

    previews: list[SheetPreview] = []
    for ws in wb.worksheets:
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            previews.append(SheetPreview(
                sheet_name=ws.title, row_count=0, column_names=[], sample_rows=[],
                notes=["空 sheet, 已跳过"],
            ))
            continue
        column_names = [str(c).strip() if c is not None else f"col{i + 1}"
                        for i, c in enumerate(header_row)]
        # 过滤全空列名 (Excel 尾部 None)
        last_nonempty = 0
        for i, n in enumerate(column_names):
            if n and not n.startswith("col"):
                last_nonempty = i + 1
        column_names = column_names[:last_nonempty]
        sample: list[list[Any]] = []
        total = 0
        for r in rows_iter:
            total += 1
            if len(sample) < sample_rows:
                sample.append([_clean_value(c) for c in (r or [])][:last_nonempty])
        previews.append(SheetPreview(
            sheet_name=ws.title,
            row_count=total,
            column_names=column_names,
            sample_rows=sample,
        ))
    wb.close()
    return previews


def _clean_value(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


# ----------------------------- AI 推断 mapping ------------------- #


_AI_SYSTEM_PROMPT = """你是 Excel → ERP 字段映射助手。给你一个 sheet 的列名 + 前几行数据,
请输出严格 JSON, 包括:
{
  "entity_type": "供给的 supported_entities 中选一个 key, 拿不准填 unknown",
  "mapping": { "目标字段名": "Excel 列名", ... },
  "skipped_columns": ["完全不需要的列名", ...],
  "warnings": ["数据脏的提示", ...]
}
规则:
- 只在提供的 "支持的目标字段" 里选 target 字段名
- 同义词应识别 (如 "供应商" → supplier_name, "送货日期" → delivery_date)
- 没法对应的 Excel 列扔进 skipped_columns
- 必填字段没找到对应列 → 写进 warnings
- 仅输出 JSON, 不要任何解释文字"""


def infer_mapping(
    db: Session, *, preview: SheetPreview, entity_type: Optional[str] = None,
) -> SheetPreview:
    """让 AI 给一个 sheet 推荐 entity_type + column mapping. 失败时返回原 preview."""
    cfg = settings_service.get_ai_config(db, "diagnose")
    try:
        provider = build_provider(cfg)
    except AiUnavailable:
        preview.notes.append("AI 未配置, 跳过推断 — 请在 UI 手动配 mapping")
        return preview

    if entity_type and entity_type != "auto":
        schemas = {entity_type: get_schema(entity_type)}
    else:
        schemas = ENTITY_SCHEMAS

    schema_doc = {
        et: {
            "label": s["label"],
            "fields": {
                fn: {
                    "required": f.get("required", False),
                    "desc": f.get("desc", ""),
                    "aliases": f.get("aliases", []),
                }
                for fn, f in s["fields"].items()
            },
        }
        for et, s in schemas.items()
    }
    user_msg = json.dumps({
        "supported_entities": schema_doc,
        "sheet": {
            "name": preview.sheet_name,
            "columns": preview.column_names,
            "sample_rows": preview.sample_rows,
        },
    }, ensure_ascii=False)

    try:
        resp = provider.chat(system=_AI_SYSTEM_PROMPT, user=user_msg, max_tokens=800)
    except AiUnavailable as e:
        preview.notes.append(f"AI 调用失败, 跳过推断: {e}")
        return preview

    try:
        data = _extract_json(resp.text)
    except ValueError as e:
        preview.notes.append(f"AI 返回无法解析: {e}")
        return preview

    et = data.get("entity_type")
    if et in ENTITY_SCHEMAS:
        preview.suggested_entity = et
    elif entity_type and entity_type != "auto":
        preview.suggested_entity = entity_type
    mapping = data.get("mapping") or {}
    if isinstance(mapping, dict):
        # 过滤: 只保留目标字段在 schema 里, Excel 列在 columns 里
        if preview.suggested_entity:
            valid_fields = set(get_schema(preview.suggested_entity)["fields"])
            preview.suggested_mapping = {
                k: v for k, v in mapping.items()
                if k in valid_fields and v in preview.column_names
            }
    for w in data.get("warnings") or []:
        preview.notes.append(f"AI 提示: {w}")
    return preview


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> dict:
    cleaned = _FENCE_RE.sub("", text).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"不是 JSON: {text[:200]}")
    return json.loads(cleaned[start: end + 1])


# ----------------------------- 提交入库 -------------------------- #


def commit_sheet(
    db: Session,
    *,
    file_bytes: bytes,
    sheet_name: str,
    entity_type: str,
    mapping: dict[str, str],          # target_field -> excel_column
    auto_create_suppliers: bool = True,
    auto_match_orders: bool = True,
    dry_run: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> ImportReport:
    """按 mapping 把一个 sheet 的所有行入库 (业务需求 6: 进度回调)."""
    schema = get_schema(entity_type)
    _validate_mapping(schema, mapping)

    rows = _read_all_rows(file_bytes, sheet_name)
    report = ImportReport(
        entity_type=entity_type, sheet_name=sheet_name,
        total_rows=len(rows), inserted_parents=0, inserted_children=0, skipped_rows=0,
    )
    if not rows:
        if progress_callback:
            progress_callback(0, 0)
        return report
    if progress_callback:
        progress_callback(0, len(rows))

    if entity_type == "delivery_note":
        _commit_delivery_notes(
            db, rows=rows, mapping=mapping, schema=schema, report=report,
            auto_create_suppliers=auto_create_suppliers,
            auto_match_orders=auto_match_orders,
            progress_callback=progress_callback,
        )
    elif entity_type == "factory_order":
        _commit_factory_orders(db, rows=rows, mapping=mapping, report=report,
                               progress_callback=progress_callback)
    elif entity_type == "alipay_flow":
        _commit_alipay_flows(db, rows=rows, mapping=mapping, report=report,
                             progress_callback=progress_callback)
    else:  # pragma: no cover
        raise ImporterError(f"暂不支持 {entity_type} 的入库")

    if progress_callback:
        progress_callback(len(rows), len(rows))

    if dry_run:
        db.rollback()
    else:
        db.flush()
    return report


def _validate_mapping(schema, mapping: dict[str, str]) -> None:
    missing = [
        fn for fn, f in schema["fields"].items()
        if f.get("required") and fn not in mapping
    ]
    if missing:
        raise ImporterError(
            f"必填字段未映射: {missing}. 请到前端把每个必填字段都选一个 Excel 列。"
        )


def _read_all_rows(file_bytes: bytes, sheet_name: str) -> list[dict[str, Any]]:
    """读完整 sheet 转为 [{column_name: value, ...}, ...]."""
    wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        wb.close()
        raise ImporterError(f"sheet {sheet_name!r} 不存在; 可选: {wb.sheetnames}")
    ws = wb[sheet_name]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        wb.close()
        return []
    columns = [str(c).strip() if c is not None else f"col{i + 1}"
               for i, c in enumerate(header_row)]
    rows: list[dict[str, Any]] = []
    for r in rows_iter:
        if r is None:
            continue
        row_dict = {col: r[i] if i < len(r) else None for i, col in enumerate(columns)}
        if all(v is None or v == "" for v in row_dict.values()):
            continue
        rows.append(row_dict)
    wb.close()
    return rows


# ----------------------------- type coercion --------------------- #


def _coerce(value: Any, field_type: str, *, label: str) -> Any:
    if value is None or value == "":
        return None
    if field_type == "str":
        return str(value).strip()
    if field_type == "int":
        if isinstance(value, (int,)):
            return value
        try:
            return int(float(str(value).replace(",", "").strip()))
        except (ValueError, InvalidOperation) as e:
            raise ImporterError(f"{label} 不是整数: {value!r}") from e
    if field_type == "decimal":
        if isinstance(value, (int, float, Decimal)):
            return Decimal(str(value))
        try:
            s = str(value).replace(",", "").replace("¥", "").replace("元", "").strip()
            return Decimal(s) if s else None
        except InvalidOperation as e:
            raise ImporterError(f"{label} 不是数字: {value!r}") from e
    if field_type == "date":
        if isinstance(value, date) and not isinstance(value, datetime):
            return value
        if isinstance(value, datetime):
            return value.date()
        s = str(value).strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(s.replace("年", "-").replace("月", "-")
                                          .replace("日", "").replace("/", "-")
                                          .replace(".", "-"), "%Y-%m-%d").date()
            except ValueError:
                continue
        raise ImporterError(f"{label} 日期格式无法识别: {value!r}")
    if field_type == "datetime":
        if isinstance(value, datetime):
            return value
        return None
    if field_type == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("是", "true", "yes", "1", "y")
    return value


def _project(
    row: dict[str, Any], mapping: dict[str, str], schema,
) -> tuple[dict[str, Any], list[str]]:
    """row + mapping → {target_field: typed_value}. 收集行级错误."""
    out: dict[str, Any] = {}
    errors: list[str] = []
    for target_field, excel_col in mapping.items():
        raw = row.get(excel_col)
        field_def = schema["fields"].get(target_field)
        if field_def is None:
            continue
        try:
            out[target_field] = _coerce(raw, field_def.get("type", "str"),
                                        label=target_field)
        except ImporterError as e:
            errors.append(str(e))
    return out, errors


# ----------------------------- delivery_note --------------------- #


def _get_or_create_supplier(
    db: Session, name: str, *, auto_create: bool,
    created_log: list[str],
) -> Optional[Supplier]:
    s = db.execute(
        select(Supplier).where(Supplier.name == name)
    ).scalar_one_or_none()
    if s is not None:
        return s
    if not auto_create:
        return None
    s = Supplier(name=name, supplier_type="other", is_active=True)
    db.add(s)
    db.flush()
    created_log.append(name)
    return s


_PROGRESS_TICK = 50   # 每 N 行向 callback 报一次进度


def _commit_delivery_notes(
    db: Session, *, rows: list[dict], mapping: dict[str, str], schema,
    report: ImportReport, auto_create_suppliers: bool, auto_match_orders: bool,
    progress_callback: Optional[ProgressCallback] = None,
) -> None:
    """同 (supplier_name, note_no) 的行聚合为一张 DeliveryNote + 多 DeliveryNoteLine."""
    groups: dict[tuple[str, str], list[tuple[dict, dict]]] = {}
    # (parent_fields, line_fields) 按 group_by 聚合
    total = len(rows)
    for i, raw_row in enumerate(rows, start=1):
        if progress_callback and i % _PROGRESS_TICK == 0:
            progress_callback(i, total)
        projected, errs = _project(raw_row, mapping, schema)
        if errs:
            report.errors.append(f"第 {i + 1} 行: " + "; ".join(errs))
            report.skipped_rows += 1
            continue
        supplier_name = projected.get("supplier_name") or ""
        if not supplier_name:
            report.skipped_rows += 1
            report.errors.append(f"第 {i + 1} 行: 供应商名为空")
            continue
        note_no = projected.get("note_no") or f"__NO_NOTE_NO__{i}"
        key = (supplier_name, str(note_no))
        parent = {k: projected.get(k) for k in schema["parent_fields"]}
        line = {k: v for k, v in projected.items() if k not in schema["parent_fields"]}
        groups.setdefault(key, []).append((parent, line))

    for (sup_name, note_no), entries in groups.items():
        supplier = _get_or_create_supplier(
            db, sup_name, auto_create=auto_create_suppliers,
            created_log=report.auto_created_suppliers,
        )
        if supplier is None:
            report.skipped_rows += len(entries)
            report.errors.append(f"供应商 {sup_name!r} 不存在 (未启用自动创建)")
            continue
        # parent 取第一行的 (delivery_date / total_amount / status / remark)
        first_parent = entries[0][0]
        # 重复单号 → 跳过 (避免 idempotency 问题)
        existing = db.execute(select(DeliveryNote).where(
            DeliveryNote.supplier_id == supplier.id,
            DeliveryNote.note_no == (note_no if not note_no.startswith("__NO_NOTE_NO__") else None),
        )).scalar_one_or_none() if not note_no.startswith("__NO_NOTE_NO__") else None
        if existing is not None:
            report.warnings.append(f"已存在 单号 {note_no} ({sup_name}), 跳过")
            report.skipped_rows += len(entries)
            continue

        n = DeliveryNote(
            supplier_id=supplier.id,
            note_no=None if note_no.startswith("__NO_NOTE_NO__") else note_no,
            delivery_date=first_parent.get("delivery_date"),
            total_amount=first_parent.get("total_amount"),
            status=first_parent.get("status") or "confirmed",  # 历史数据默认已确认
            remark=first_parent.get("remark"),
            ocr_warnings=[], ocr_model="excel_import",
        )
        db.add(n)
        db.flush()
        report.inserted_parents += 1

        line_total = Decimal("0")
        for j, (_, line_d) in enumerate(entries, start=1):
            qty = line_d.get("qty") or Decimal("0")
            unit_price = line_d.get("unit_price")
            amount = line_d.get("amount")
            if amount is None and unit_price is not None and qty:
                amount = (unit_price * qty).quantize(Decimal("0.01"))
            line = DeliveryNoteLine(
                delivery_note_id=n.id, line_no=j,
                item_name=line_d.get("item_name") or "",
                spec=line_d.get("spec") or "",
                unit=line_d.get("unit") or "",
                qty=qty,
                unit_price=unit_price,
                amount=amount,
                ocr_raw_text="", ocr_warnings=[],
            )
            if auto_match_orders:
                try:
                    cands = delivery_matcher.match_line(
                        db, item_name=line.item_name, spec=line.spec, qty=qty,
                        delivery_date=n.delivery_date,
                        enable_ai_tiebreaker=False,  # 导入量大时跳 AI 省钱
                    )
                    delivery_matcher.apply_candidates_to_line(line, cands)
                    if line.matched_order_no:
                        report.matched_lines += 1
                except Exception as e:  # pragma: no cover
                    line.remark = f"匹配失败: {e}"
            db.add(line)
            report.inserted_children += 1
            if amount is not None:
                line_total += amount
        # 没读到 total_amount 的话, 用行金额求和
        if n.total_amount is None and line_total > 0:
            n.total_amount = line_total


# ----------------------------- factory_order --------------------- #


def _commit_factory_orders(
    db: Session, *, rows: list[dict], mapping: dict[str, str], report: ImportReport,
    progress_callback: Optional[ProgressCallback] = None,
) -> None:
    schema = get_schema("factory_order")
    total = len(rows)
    for i, raw_row in enumerate(rows, start=1):
        if progress_callback and i % _PROGRESS_TICK == 0:
            progress_callback(i, total)
        projected, errs = _project(raw_row, mapping, schema)
        if errs:
            report.errors.append(f"第 {i + 1} 行: " + "; ".join(errs))
            report.skipped_rows += 1
            continue
        fo_no = projected.get("factory_order_no")
        if not fo_no:
            report.skipped_rows += 1
            report.errors.append(f"第 {i + 1} 行: 工厂订单号为空")
            continue
        existing = db.execute(
            select(FactoryOrder).where(FactoryOrder.factory_order_no == fo_no)
        ).scalar_one_or_none()
        if existing is not None:
            report.warnings.append(f"已存在 工厂订单号 {fo_no}, 跳过")
            report.skipped_rows += 1
            continue
        fo = FactoryOrder(
            factory_order_no=fo_no,
            platform_order_no=projected.get("platform_order_no"),
            factory_name=projected.get("factory_name"),
            order_date=projected.get("order_date"),
            expected_delivery=projected.get("expected_delivery"),
            actual_delivery=projected.get("actual_delivery"),
            product_code=projected.get("product_code"),
            sku=projected.get("sku"),
            qty=int(projected.get("qty") or 1),
            unit_price=projected.get("unit_price"),
            factory_bill_amount=projected.get("factory_bill_amount"),
            payment_method=projected.get("payment_method"),
            payment_status=projected.get("payment_status") or "unpaid",
            remark=projected.get("remark"),
        )
        db.add(fo)
        report.inserted_parents += 1


# ----------------------------- alipay_flow ----------------------- #


def _commit_alipay_flows(
    db: Session, *, rows: list[dict], mapping: dict[str, str], report: ImportReport,
    progress_callback: Optional[ProgressCallback] = None,
) -> None:
    """支付宝流水: (account, transaction_no) 唯一. 自动跑 smart_matching_service.run()
    给新进来的流水打标签 (factory_payment/promotion/etc)."""
    schema = get_schema("alipay_flow")
    fresh_ids: list[int] = []
    total = len(rows)
    for i, raw_row in enumerate(rows, start=1):
        if progress_callback and i % _PROGRESS_TICK == 0:
            progress_callback(i, total)
        projected, errs = _project(raw_row, mapping, schema)
        if errs:
            report.errors.append(f"第 {i + 1} 行: " + "; ".join(errs))
            report.skipped_rows += 1
            continue
        account = projected.get("account")
        tx_no = projected.get("transaction_no")
        amount = projected.get("amount")
        if not account or not tx_no or amount is None:
            report.skipped_rows += 1
            report.errors.append(f"第 {i + 1} 行: 账户/流水号/金额 任一为空")
            continue
        existing = db.execute(
            select(AlipayFlow).where(
                AlipayFlow.account == account, AlipayFlow.transaction_no == tx_no,
            )
        ).scalar_one_or_none()
        if existing is not None:
            report.warnings.append(f"已存在 {account} 流水 {tx_no}, 跳过")
            report.skipped_rows += 1
            continue
        flow = AlipayFlow(
            account=account, transaction_no=tx_no,
            transaction_time=projected.get("transaction_time"),
            transaction_type=projected.get("transaction_type"),
            counterparty=projected.get("counterparty"),
            counterparty_account=projected.get("counterparty_account"),
            amount=amount,
            balance=projected.get("balance"),
            related_order_no=projected.get("related_order_no"),
            remark=projected.get("remark"),
            reconciliation_status="open",
        )
        db.add(flow)
        db.flush()
        fresh_ids.append(flow.id)
        report.inserted_parents += 1

    # 入完一次性跑智能标签 (factory_payment / promotion / logistics / salary)
    if fresh_ids:
        try:
            from app.services.smart_matching_service import run as smart_tag
            tag_result = smart_tag(db)
            tagged_total = sum(tag_result.tagged.values())
            if tagged_total > 0:
                report.warnings.append(
                    f"已为 {tagged_total} 条新流水自动打标签: "
                    + ", ".join(f"{k}={v}" for k, v in tag_result.tagged.items())
                )
        except Exception as e:  # pragma: no cover
            report.warnings.append(f"自动打标签失败 (不影响入库): {e}")
