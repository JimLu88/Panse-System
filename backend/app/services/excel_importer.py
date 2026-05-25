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
# 返回 True 表示外部要求取消 (用户点了取消按钮); worker 据此抛 CancelledImport
CancelCallback = Callable[[], bool]


class CancelledImport(RuntimeError):
    """用户在 UI 点了取消按钮, worker 干净退出 (rollback)."""

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
    # 重导冲突: 行匹配到已有记录但字段值不同 (on_conflict != overwrite 时不自动覆盖)
    # [{source_table, source_pk, diffs: [{field, old, new}]}]
    conflicts: list[dict] = field(default_factory=list)


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
    file_bytes: Optional[bytes] = None,
    file_path: Optional[str] = None,
    sheet_name: str,
    entity_type: str,
    mapping: dict[str, str],          # target_field -> excel_column
    auto_create_suppliers: bool = True,
    auto_match_orders: bool = True,
    dry_run: bool = False,
    on_conflict: str = "overwrite",
    sheet_account: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_callback: Optional[CancelCallback] = None,
) -> ImportReport:
    """按 mapping 把一个 sheet 的所有行入库 (业务需求 6: 进度回调 + 取消).

    on_conflict: overwrite (默认, 直接覆盖已有记录) / keep (保留原值) /
                 ask (记录差异到 report.conflicts 但不覆盖, 等用户裁决).
    sheet_account: 支付宝流水专用 — 当 sheet 没有账户列时, 用这个账户名填充每行.
    """
    if file_bytes is None and file_path is None:
        raise ImporterError("必须提供 file_bytes 或 file_path 之一")
    if file_bytes is None:
        with open(file_path, "rb") as f:  # type: ignore[arg-type]
            file_bytes = f.read()

    schema = get_schema(entity_type)
    # 支付宝流水允许账户名走 sheet_account 注入, 不强制映射 account 列
    externally_satisfied = {"account"} if (entity_type == "alipay_flow" and sheet_account) else set()
    _validate_mapping(schema, mapping, satisfied=externally_satisfied)

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
            cancel_callback=cancel_callback,
        )
    elif entity_type == "factory_order":
        _commit_factory_orders(db, rows=rows, mapping=mapping, report=report,
                               progress_callback=progress_callback,
                               cancel_callback=cancel_callback)
    elif entity_type == "alipay_flow":
        _commit_alipay_flows(db, rows=rows, mapping=mapping, report=report,
                             sheet_account=sheet_account,
                             progress_callback=progress_callback,
                             cancel_callback=cancel_callback)
    elif entity_type in ("product", "material", "bom_line", "product_inventory",
                          "part_inventory", "order", "account_balance", "pricing_sku",
                          "refill_record", "factory_reconciliation",
                          "outsourcing_expense", "aftersales"):
        _commit_generic(
            db, rows=rows, mapping=mapping, entity_type=entity_type, report=report,
            on_conflict=on_conflict,
            progress_callback=progress_callback, cancel_callback=cancel_callback,
        )
    else:  # pragma: no cover
        raise ImporterError(f"暂不支持 {entity_type} 的入库")

    if progress_callback:
        progress_callback(len(rows), len(rows))

    if dry_run:
        db.rollback()
    else:
        db.flush()
    return report


def _validate_mapping(schema, mapping: dict[str, str], *, satisfied: set = frozenset()) -> None:
    missing = [
        fn for fn, f in schema["fields"].items()
        if f.get("required") and fn not in mapping and fn not in satisfied
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
    """row + mapping → {target_field: typed_value}. 收集行级错误.

    非必填字段转换失败时置 None (不杀整行);必填字段失败才加入 errors.
    """
    out: dict[str, Any] = {}
    errors: list[str] = []
    for target_field, excel_col in mapping.items():
        raw = row.get(excel_col)
        field_def = schema["fields"].get(target_field)
        if field_def is None:
            continue
        # 跳过 Excel 公式错误值 (#DIV/0! 等)
        if isinstance(raw, str) and raw.strip().startswith("#"):
            if field_def.get("required"):
                errors.append(f"{target_field} 含 Excel 错误值: {raw!r}")
            else:
                out[target_field] = None
            continue
        try:
            out[target_field] = _coerce(raw, field_def.get("type", "str"),
                                        label=target_field)
        except ImporterError as e:
            if field_def.get("required"):
                errors.append(str(e))
            else:
                out[target_field] = None  # 非必填字段转换失败 → null,不杀整行
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
    cancel_callback: Optional[CancelCallback] = None,
) -> None:
    """同 (supplier_name, note_no) 的行聚合为一张 DeliveryNote + 多 DeliveryNoteLine."""
    groups: dict[tuple[str, str], list[tuple[dict, dict]]] = {}
    # (parent_fields, line_fields) 按 group_by 聚合
    total = len(rows)
    for i, raw_row in enumerate(rows, start=1):
        if i % _PROGRESS_TICK == 0:
            if progress_callback:
                progress_callback(i, total)
            if cancel_callback and cancel_callback():
                raise CancelledImport(f"用户取消, 已处理 {i}/{total} 行")
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
    cancel_callback: Optional[CancelCallback] = None,
) -> None:
    schema = get_schema("factory_order")
    total = len(rows)
    for i, raw_row in enumerate(rows, start=1):
        if i % _PROGRESS_TICK == 0:
            if progress_callback:
                progress_callback(i, total)
            if cancel_callback and cancel_callback():
                raise CancelledImport(f"用户取消, 已处理 {i}/{total} 行")
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
    sheet_account: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_callback: Optional[CancelCallback] = None,
) -> None:
    """支付宝流水: (account, transaction_no) 唯一. 自动跑 smart_matching_service.run()
    给新进来的流水打标签 (factory_payment/promotion/etc).

    sheet_account: 当 sheet 没有账户列 (账户名写在 sheet 名/标题) 时, 用它填充每行账户。
    """
    schema = get_schema("alipay_flow")
    fresh_ids: list[int] = []
    total = len(rows)
    for i, raw_row in enumerate(rows, start=1):
        if i % _PROGRESS_TICK == 0:
            if progress_callback:
                progress_callback(i, total)
            if cancel_callback and cancel_callback():
                raise CancelledImport(f"用户取消, 已处理 {i}/{total} 行")
        projected, errs = _project(raw_row, mapping, schema)
        if errs:
            report.errors.append(f"第 {i + 1} 行: " + "; ".join(errs))
            report.skipped_rows += 1
            continue
        account = projected.get("account") or sheet_account
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


# ----------------------------- 通用 entity 入库 (扩展) ----------------- #


@dataclass
class _GenericCtx:
    """传给 generic handler 的上下文 (冲突策略 + 收集差异)."""
    report: ImportReport
    on_conflict: str = "overwrite"   # overwrite / keep / ask


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _diff_fields(existing, payload: dict) -> list[dict]:
    """对比已有记录与新值, 只返回真正变化的字段 [{field, old, new}]."""
    diffs: list[dict] = []
    for f, new in payload.items():
        if not hasattr(existing, f):
            continue
        old = getattr(existing, f)
        if isinstance(old, Decimal) and new is not None:
            try:
                if Decimal(str(new)) == old:
                    continue
            except (InvalidOperation, ValueError):
                pass
        if old == new:
            continue
        diffs.append({"field": f, "old": _jsonable(old), "new": _jsonable(new)})
    return diffs


def _apply_update(existing, payload: dict, ctx: Optional["_GenericCtx"],
                  source_table: str, source_pk) -> str:
    """upsert 命中已有记录时的统一处理: 比 diff + 按 on_conflict 决定覆盖/记冲突."""
    diffs = _diff_fields(existing, payload)
    if not diffs:
        return "skipped"   # 值完全相同, 不算更新
    if ctx is None or ctx.on_conflict == "overwrite":
        for f, v in payload.items():
            if hasattr(existing, f):
                setattr(existing, f, v)
        return "updated"
    if ctx.on_conflict == "keep":
        return "skipped"   # 保留原值, 不动
    # ask: 记录差异, 不覆盖, 等用户裁决
    ctx.report.conflicts.append({
        "source_table": source_table,
        "source_pk": str(source_pk) if source_pk is not None else None,
        "diffs": diffs,
    })
    return "conflict"


def _commit_generic(
    db: Session, *, rows: list[dict], mapping: dict[str, str],
    entity_type: str, report: ImportReport,
    on_conflict: str = "overwrite",
    progress_callback: Optional[ProgressCallback] = None,
    cancel_callback: Optional[CancelCallback] = None,
) -> None:
    """7 类简单 entity (产品/物料/BOM/库存/订单/账户余额) 统一入库.

    每行 = 一条记录, 按"唯一字段"去重 upsert.
    on_conflict 控制重导命中已有记录且值不同时的行为 (见 commit_sheet).
    """
    schema = get_schema(entity_type)
    total = len(rows)
    uniqueness_field = _UNIQUENESS_FIELD.get(entity_type)
    ctx = _GenericCtx(report=report, on_conflict=on_conflict)
    inserted, updated, skipped, conflicted = 0, 0, 0, 0
    for i, raw_row in enumerate(rows, start=1):
        if i % _PROGRESS_TICK == 0:
            if progress_callback:
                progress_callback(i, total)
            if cancel_callback and cancel_callback():
                raise CancelledImport(f"用户取消, 已处理 {i}/{total} 行")
        projected, errs = _project(raw_row, mapping, schema)
        if errs:
            report.errors.append(f"第 {i + 1} 行: " + "; ".join(errs))
            skipped += 1
            continue
        try:
            kind, did = _GENERIC_HANDLERS[entity_type](db, projected, uniqueness_field, ctx)
        except Exception as e:
            report.errors.append(f"第 {i + 1} 行: {type(e).__name__}: {e}")
            skipped += 1
            continue
        if did == "inserted":
            inserted += 1
        elif did == "updated":
            updated += 1
        elif did == "conflict":
            conflicted += 1
        else:
            skipped += 1
    report.inserted_parents += inserted
    report.skipped_rows += skipped + conflicted
    if updated > 0:
        report.warnings.append(f"{updated} 行更新已有记录")
    if conflicted > 0:
        report.warnings.append(f"{conflicted} 行与库内数据不同, 待人工确认 (见 conflicts)")


_UNIQUENESS_FIELD = {
    "product": "code",
    "material": "code",
    "product_inventory": None,         # (warehouse, product_code, sku)
    "part_inventory": None,             # (warehouse, material_code)
    "bom_line": None,                   # 无 upsert, 直接 add
    "order": "order_no",
    "account_balance": None,            # (account_name, year, month)
    "pricing_sku": "sku_code",
    "refill_record": "order_no",
    "factory_reconciliation": None,     # (factory_name, period_end)
    "outsourcing_expense": None,        # alipay_flow_no 去重
    "aftersales": "platform_order_no",
}


def _h_product(db, data, key_field, ctx=None):
    from app.models.product import Product
    code = data.get("code")
    if not code:
        raise ImporterError("缺产品编码")
    payload = {k: v for k, v in data.items() if v is not None}
    existing = db.execute(select(Product).where(Product.code == code)).scalar_one_or_none()
    if existing:
        return "product", _apply_update(existing, payload, ctx, "products", code)
    db.add(Product(**payload))
    return "product", "inserted"


def _h_material(db, data, key_field, ctx=None):
    from app.models.material import Material
    code = data.get("code")
    name = data.get("name") or code
    if not code:
        raise ImporterError("缺物料编码")
    existing = db.execute(select(Material).where(Material.code == code)).scalar_one_or_none()
    if existing:
        return "material", _apply_update(
            existing, {k: v for k, v in data.items() if v is not None}, ctx, "materials", code)
    # name UNIQUE: 重名加 code 后缀
    same_name = db.execute(select(Material).where(Material.name == name)).scalar_one_or_none()
    if same_name and same_name.code != code:
        name = f"{name} ({code})"
    payload = {k: v for k, v in data.items() if v is not None}
    payload["name"] = name
    db.add(Material(**payload))
    return "material", "inserted"


def _h_bom(db, data, key_field, ctx=None):
    from app.models.bom import BomLine
    from app.models.material import Material
    product_code = data.get("product_code")
    material_code = data.get("material_code")
    if not product_code or not material_code:
        raise ImporterError("缺 product_code 或 material_code")
    # 物料不存在 → 自动建占位
    if not db.execute(select(Material).where(Material.code == material_code)).scalar_one_or_none():
        db.add(Material(code=material_code, name=f"占位 ({material_code})"))
        db.flush()
    db.add(BomLine(**{k: v for k, v in data.items() if v is not None}))
    return "bom_line", "inserted"


def _h_product_inv(db, data, key_field, ctx=None):
    from app.models.inventory import ProductInventory
    warehouse = data.get("warehouse") or "江西仓库"
    product_code = data.get("product_code")
    sku = data.get("sku")
    if not product_code:
        raise ImporterError("缺 product_code")
    existing = db.execute(select(ProductInventory).where(
        ProductInventory.warehouse == warehouse,
        ProductInventory.product_code == product_code,
        ProductInventory.sku == sku,
    )).scalar_one_or_none()
    payload = {k: v for k, v in data.items() if v is not None}
    payload["warehouse"] = warehouse
    if existing:
        return "product_inv", _apply_update(
            existing, payload, ctx, "product_inventory",
            f"{warehouse}|{product_code}|{sku}")
    db.add(ProductInventory(**payload))
    return "product_inv", "inserted"


def _h_part_inv(db, data, key_field, ctx=None):
    from app.models.inventory import PartInventory
    from app.models.material import Material
    warehouse = data.get("warehouse") or "江西仓库"
    material_code = data.get("material_code")
    if not material_code:
        raise ImporterError("缺 material_code")
    if not db.execute(select(Material).where(Material.code == material_code)).scalar_one_or_none():
        db.add(Material(code=material_code, name=f"占位 ({material_code})"))
        db.flush()
    existing = db.execute(select(PartInventory).where(
        PartInventory.warehouse == warehouse,
        PartInventory.material_code == material_code,
    )).scalar_one_or_none()
    payload = {k: v for k, v in data.items() if v is not None}
    payload["warehouse"] = warehouse
    if existing:
        return "part_inv", _apply_update(
            existing, payload, ctx, "part_inventory", f"{warehouse}|{material_code}")
    db.add(PartInventory(**payload))
    return "part_inv", "inserted"


def _is_custom_code(db, sku_code, product_code) -> bool:
    """定制编码识别: SKU 尾部后缀 >= 阈值 (默认 90, 含 99/98/97)."""
    if not sku_code:
        return False
    from app.services import sku_utils
    threshold = db.info.setdefault("_custom_sku_threshold", sku_utils.get_threshold(db))
    return sku_utils.is_custom_sku_code(sku_code, product_code, threshold)


def _flag_custom(db, source_table: str, source_pk, sku_code) -> None:
    from app.services import exception_service
    exception_service.record(
        db,
        source_table=source_table,
        source_pk=str(source_pk) if source_pk is not None else None,
        exception_type="custom_sku_detected",
        severity="info",
        description=f"识别到定制编码 {sku_code} (后缀达定制阈值), 已自动标记定制, 请复核.",
        suggestion_action="view",
        context={"sku_code": sku_code},
    )


def _h_order(db, data, key_field, ctx=None):
    from app.models.order import Order
    order_no = data.get("order_no")
    if not order_no:
        raise ImporterError("缺 order_no")
    existing = db.execute(select(Order).where(Order.order_no == order_no)).scalar_one_or_none()
    if existing:
        return "order", "skipped"   # 已有订单不动 (避免覆盖状态)
    payload = {k: v for k, v in data.items() if v is not None}
    payload.setdefault("platform", "淘宝")
    payload.setdefault("qty", 1)
    payload.setdefault("status", "signed")
    payload.setdefault("is_historical", True)   # 通用导入默认标历史
    # 定制编码自动识别 (后缀 >= 阈值, 如 99/98/97)
    if _is_custom_code(db, payload.get("sku_code"), payload.get("product_code")):
        payload["is_custom"] = True
        _flag_custom(db, "orders", order_no, payload.get("sku_code"))
    db.add(Order(**payload))
    return "order", "inserted"


def _h_balance(db, data, key_field, ctx=None):
    from app.models.finance import AccountBalance
    _ = ctx
    name = data.get("account_name")
    year = data.get("year")
    month = data.get("month")
    if not (name and year and month):
        raise ImporterError("缺账户名/年/月")
    existing = db.execute(select(AccountBalance).where(
        AccountBalance.account_name == name,
        AccountBalance.period_year == year,
        AccountBalance.period_month == month,
    )).scalar_one_or_none()
    if existing:
        return "balance", "skipped"
    payload = {k: v for k, v in data.items() if v is not None}
    payload["period_year"] = payload.pop("year")
    payload["period_month"] = payload.pop("month")
    db.add(AccountBalance(**payload))
    return "balance", "inserted"


def _h_pricing_sku(db, data, key_field, ctx=None):
    from app.models.pricing import PricingSku
    sku_code = data.get("sku_code")
    if not sku_code:
        raise ImporterError("缺 sku_code")
    existing = db.execute(select(PricingSku).where(
        PricingSku.sku_code == sku_code,
    )).scalar_one_or_none()
    payload = {k: v for k, v in data.items() if v is not None}
    if existing:
        return "pricing_sku", _apply_update(existing, payload, ctx, "pricing_sku", sku_code)
    if _is_custom_code(db, sku_code, payload.get("product_code")):
        _flag_custom(db, "pricing_sku", sku_code, sku_code)
    db.add(PricingSku(**payload))
    return "pricing_sku", "inserted"


def _h_refill_record(db, data, key_field, ctx=None):
    from app.models.finance import RefillRecord
    order_no = data.get("order_no")
    if not order_no:
        raise ImporterError("缺 order_no")
    existing = db.execute(select(RefillRecord).where(RefillRecord.order_no == order_no)).scalar_one_or_none()
    payload = {k: v for k, v in data.items() if v is not None}
    if existing:
        return "refill_record", _apply_update(existing, payload, ctx, "refill_records", order_no)
    db.add(RefillRecord(**payload))
    return "refill_record", "inserted"


def _h_factory_reconciliation(db, data, key_field, ctx=None):
    from app.models.finance import FactoryReconciliation
    factory = data.get("factory_name")
    if not factory:
        raise ImporterError("缺 factory_name")
    period_end = data.get("period_end")
    existing = db.execute(
        select(FactoryReconciliation).where(
            FactoryReconciliation.factory_name == factory,
            FactoryReconciliation.period_end == period_end,
        )
    ).scalar_one_or_none() if period_end else None
    payload = {k: v for k, v in data.items() if v is not None}
    if existing:
        return "factory_reconciliation", _apply_update(
            existing, payload, ctx, "factory_reconciliations", f"{factory}|{period_end}")
    db.add(FactoryReconciliation(**payload))
    return "factory_reconciliation", "inserted"


def _h_outsourcing_expense(db, data, key_field, ctx=None):
    from app.models.marketing import OutsourcingExpense
    amount = data.get("amount")
    if amount is None:
        raise ImporterError("缺 amount")
    payload = {k: v for k, v in data.items() if v is not None}
    # OutsourcingExpense 无天然唯一键 → 按 alipay_flow_no 去重(若有)
    flow_no = payload.get("alipay_flow_no")
    if flow_no:
        existing = db.execute(
            select(OutsourcingExpense).where(OutsourcingExpense.alipay_flow_no == flow_no)
        ).scalar_one_or_none()
        if existing:
            return "outsourcing_expense", _apply_update(
                existing, payload, ctx, "outsourcing_expenses", flow_no)
    db.add(OutsourcingExpense(**payload))
    return "outsourcing_expense", "inserted"


def _h_aftersales(db, data, key_field, ctx=None):
    from app.models.marketing import AfterSales
    order_no = data.get("platform_order_no")
    if not order_no:
        raise ImporterError("缺 platform_order_no")
    existing = db.execute(
        select(AfterSales).where(AfterSales.platform_order_no == order_no)
    ).scalar_one_or_none()
    payload = {k: v for k, v in data.items() if v is not None}
    if existing:
        return "aftersales", _apply_update(existing, payload, ctx, "after_sales", order_no)
    db.add(AfterSales(**payload))
    return "aftersales", "inserted"


_GENERIC_HANDLERS = {
    "product": _h_product, "material": _h_material, "bom_line": _h_bom,
    "product_inventory": _h_product_inv, "part_inventory": _h_part_inv,
    "order": _h_order, "account_balance": _h_balance,
    "pricing_sku": _h_pricing_sku,
    "refill_record": _h_refill_record,
    "factory_reconciliation": _h_factory_reconciliation,
    "outsourcing_expense": _h_outsourcing_expense,
    "aftersales": _h_aftersales,
}
