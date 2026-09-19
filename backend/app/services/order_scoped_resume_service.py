"""Explicit operator recovery from verified imported files, not a fake pull terminal.

No full import, parent regeneration, historical voiding, global pipeline success,
or browser action. The ordinary freshness gate remains unchanged.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.services import import_storage, order_line_delivery_service as lines
from app.services import order_sheet_archive_service as sheets
from app.services import taobao_order_import as importer


def _read_evidence(db: Session, business_date: str, file_ids: list[int]) -> tuple[dict, dict, list[dict]]:
    day = date.fromisoformat(business_date)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if not today - timedelta(days=1) <= day <= today:
        raise ValueError("续推仅接受今天或跨午夜的昨日批次")
    if len(file_ids) != 3 or len(set(file_ids)) != 3:
        raise ValueError("必须指定三份不同的已导入报表")
    roles, evidence = {}, []
    for fid in file_ids:
        rec = db.get(ImportedFile, fid)
        st = (rec.row_summary or {}) if rec else {}
        role = st.get("agent_report_role")
        stamp = rec.created_at if rec else None
        if stamp and stamp.tzinfo:
            stamp = stamp.astimezone(ZoneInfo("Asia/Shanghai"))
        if (rec is None or rec.kind != "taobao" or not stamp or stamp.date() != day
                or st.get("agent_status") != "imported" or st.get("errors")
                or role not in {"orders", "sales_detail", "shipping"} or role in roles):
            raise ValueError(f"归档{fid}日期/角色/入库状态不符合本次续推")
        raw = import_storage.read(rec.stored_path)
        if not rec.file_hash or sha256(raw).hexdigest() != rec.file_hash:
            raise ValueError(f"归档{fid}文件校验不通过")
        roles[role] = (rec, raw)
        evidence.append({"file_id": fid, "role": role, "sha256": rec.file_hash})
    if set(roles) != {"orders", "sales_detail", "shipping"}:
        raise ValueError("三种报表角色不全")
    parsed = {}
    for role in ("orders", "sales_detail"):
        rec, raw = roles[role]
        if importer.detect_report_role(rec.original_filename, raw) != role:
            raise ValueError(f"归档{rec.id}实际角色不符")
        report = importer.TaobaoImportReport()
        parsed[role] = (importer._parse_qianniu_multi(raw, report)
                       if importer.detect_format(rec.original_filename, raw) == "qianniu_multi"
                       else importer._parse_sales_detail(rec.original_filename, raw, report))
        if report.errors:
            raise ValueError(f"归档{rec.id}解析失败")
    return parsed["orders"], parsed["sales_detail"], evidence


def resume(
    db: Session, *, business_date: str, order_batch_id: str,
    file_ids: list[int], sub_order_nos: list[str], request_id: str, dry_run: bool = True,
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise ValueError("request_id必须是32位唯一请求标识")
    if not re.fullmatch("orders-" + business_date.replace("-", "") + r"-[0-9a-f]{32}", order_batch_id):
        raise ValueError("原批次标识与业务日期不符")
    if (not sub_order_nos or len(sub_order_nos) > 50
            or len(sub_order_nos) != len(set(sub_order_nos))
            or any(not re.fullmatch(r"[0-9]{10,32}", s) for s in sub_order_nos)):
        raise ValueError("必须指定1至50条唯一子单，禁止全量补推")
    scope = {"business_date": business_date, "original_batch_id": order_batch_id,
             "file_ids": sorted(file_ids), "sub_order_nos": sorted(sub_order_nos)}
    key = "order_scoped_resume:" + request_id
    prior = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if prior:
        saved = json.loads(prior.value_plain)
        if saved["scope"] != scope:
            raise ValueError("同一请求标识不能更换范围")
        return {**saved, "replayed": False, "existing_request": True}
    masters, details, evidence = _read_evidence(db, business_date, file_ids)
    detail_index = {}
    for parent, item in details.items():
        for raw_line in item.lines:
            sub = str(raw_line.get("sub_order_no") or "")
            if sub in detail_index:
                raise ValueError("报表包含重复子单，不能猜测归属")
            detail_index[sub] = (parent, raw_line)
    sent = lines.sent_line_evidence(db)
    ready, held, skipped, repairs = [], [], [], []
    for sub in sub_order_nos:
        row = db.execute(select(OrderDetail).where(OrderDetail.sub_order_no == sub)).scalar_one_or_none()
        if sub in sent:
            skipped.append(sub)
            continue
        if row is None or sub not in detail_index:
            held.append({"sub_order_no": sub, "reason": "missing_imported_child"})
            continue
        if row.factory_delivery_state not in (None, "", "failed") or row.factory_delivery_message_id:
            held.append({"sub_order_no": sub, "reason": "existing_delivery_or_unknown"})
            continue
        parent, fact = detail_index[sub]
        order = db.execute(select(Order).where(Order.order_no == row.order_no)).scalar_one_or_none()
        if (parent != row.order_no or parent not in masters or not row.factory_delivery_required
                or not lines.line_is_factory_eligible(db, row, order)):
            held.append({"sub_order_no": sub, "reason": "not_eligible_or_parent_mismatch"})
            continue
        sku = fact.get("sku_code")
        qty = importer._to_int(fact.get("qty"), 0)
        if not sku or qty <= 0 or importer._map_status(fact.get("status_text")) not in ("paid", "production"):
            held.append({"sub_order_no": sub, "reason": "source_sku_quantity_or_status_missing"})
            continue
        # Only the empty imported parent-placeholder is repaired. Never replace
        # an existing variant or silently change a normal child's quantity.
        empty_placeholder = (row.sub_order_no == row.order_no and not row.sku_code
                             and not row.product_code and not row.sku_name)
        if empty_placeholder:
            master_skus = {x.get("sku_code") for x in masters[parent].lines if x.get("sku_code")}
            if master_skus != {sku}:
                held.append({"sub_order_no": sub, "reason": "placeholder_source_conflict"})
                continue
            repairs.append({"sub_order_no": sub, "line_id": row.id,
                            "before": {"sku_code": row.sku_code, "qty": row.qty},
                            "after": {"sku_code": sku, "qty": qty,
                                      "product_code": fact.get("product_code"), "sku_name": fact.get("sku")}})
        elif row.sku_code != sku or row.qty != qty:
            held.append({"sub_order_no": sub, "reason": "source_variant_or_quantity_conflict"})
            continue
        ready.append(sub)
    result = {"scope": scope, "evidence": evidence, "ready": ready,
              "held": held, "skipped_already_sent": skipped, "repairs": repairs,
              "original_pull_terminal_unchanged": True, "dry_run": dry_run}
    if dry_run:
        return result
    claim = SystemSetting(key=key, value_plain=json.dumps({**result, "status": "running"}),
                          description="限定子单续推回执，不覆盖原取数终态", is_secret=False)
    db.add(claim)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "existing_request", "scope": scope, "replayed": False}
    try:
        for repair in repairs:
            row = db.execute(select(OrderDetail).where(OrderDetail.id == repair["line_id"]).with_for_update()).scalar_one()
            if row.factory_delivery_state not in (None, "", "failed") or row.factory_delivery_message_id:
                ready.remove(row.sub_order_no)
                held.append({"sub_order_no": row.sub_order_no, "reason": "delivery_changed_since_validation"})
                continue
            if row.sku_code or row.product_code or row.sku_name or row.qty != repair["before"]["qty"]:
                raise ValueError("占位行在核验后发生变化，未覆盖")
            for field, value in repair["after"].items():
                setattr(row, field, value)
        db.commit()
        delivery = sheets.reconcile_order_line_delivery(db, limit=len(sub_order_nos), only_sub_order_nos=set(ready))
        db.expire_all()
        receipts = []
        for sub in ready:
            row = db.execute(select(OrderDetail).where(OrderDetail.sub_order_no == sub)).scalar_one()
            if row.factory_delivery_state == "sent" and row.factory_delivery_message_id:
                receipts.append({"sub_order_no": sub, "factory_no": row.factory_no,
                                 "message_id": row.factory_delivery_message_id})
        unresolved = sorted(set(ready) - {r["sub_order_no"] for r in receipts})
        result.update(status="partial" if held or unresolved else "done", delivery=delivery,
                      receipts=receipts, unresolved_sub_order_nos=unresolved)
    except Exception as exc:
        db.rollback()
        result.update(status="needs_review", error_type=type(exc).__name__)
    claim = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one()
    claim.value_plain = json.dumps(result, ensure_ascii=False)
    db.commit()
    return result
