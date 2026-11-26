"""Reconcile exported purchase rows against ERP, factory receipts and Feishu.

Read the immutable imported workbooks, not another projection of ERP. Repair
missing identities once; never overwrite changed facts or replay unknown sends.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256
import json
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models.import_file import ImportedFile
from app.models.order import Order, OrderDetail
from app.models.settings import SystemSetting
from app.services import agent_ingest_service as ingest
from app.services import factory_dispatch_feishu_service as table
from app.services import import_storage
from app.services import order_line_delivery_service as delivery
from app.services import order_sheet_archive_service as sheets
from app.services import taobao_order_import as importer
from app.services import taobao_listing_service

PREFIX = "order_post_push_audit:"


def _sources(db, batch_id=None, file_ids=None):
    state = ingest._load_json(db, ingest.KEY_STATE)
    if not batch_id:
        records = db.scalars(select(SystemSetting).where(
            SystemSetting.key.startswith("order_pull_completion_")).order_by(SystemSetting.id.desc())).all()
        completed = [json.loads(r.value_plain) for r in records]
        if state.get("taobao_orders_complete_batch_id"):
            completed.append({"order_batch_id": state["taobao_orders_complete_batch_id"],
                "order_business_date": state.get("taobao_orders_complete_business_date") or "",
                "completed_at": state.get("taobao_orders_complete") or ""})
        if completed:
            latest = max(completed, key=lambda r: (r.get("order_business_date") or "",
                                                  r.get("completed_at") or ""))
            batch_id = latest.get("order_batch_id")
    completion = ingest._load_json(db, "order_pull_completion_" + str(batch_id)) if batch_id else {}
    if not completion and batch_id == state.get("taobao_orders_complete_batch_id"):
        completion = {
            "order_batch_id": batch_id,
            "order_business_date": state.get("taobao_orders_complete_business_date"),
            "artifacts": state.get("taobao_orders_complete_artifacts"),
            "artifact_roles": state.get("taobao_orders_complete_artifact_roles"),
        }
    provided = None
    if file_ids is not None:
        if len(file_ids) != 3 or len(set(file_ids)) != 3 or not batch_id:
            raise ValueError("invalid_scoped_export_files")
        provided = [db.get(ImportedFile, fid) for fid in file_ids]
        if any(not r or r.kind != "taobao" for r in provided):
            raise ValueError("scoped_export_missing")
        day = datetime.strptime(batch_id.split("-")[1], "%Y%m%d").date()
        for rec in provided:
            stamp = rec.created_at
            if stamp and stamp.tzinfo:
                stamp = stamp.astimezone(ZoneInfo("Asia/Shanghai"))
            if not stamp or stamp.date() != day:
                raise ValueError("scoped_export_date_mismatch")
            recorded = (rec.row_summary or {}).get("automation_batch_id")
            if recorded and recorded != batch_id:
                raise ValueError("scoped_export_batch_mismatch")
        completion = {"order_business_date": day.isoformat(),
                      "artifacts": [r.original_filename for r in provided],
                      "artifact_roles": {r.original_filename: (r.row_summary or {}).get("agent_report_role")
                                         for r in provided}}
    if not completion or not batch_id:
        raise ValueError("missing_completed_export_batch")
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    day = datetime.fromisoformat(completion["order_business_date"]).date()
    if not now.date() - timedelta(days=1) <= day <= now.date():
        raise ValueError("export_batch_too_old")
    names = completion.get("artifacts") or []
    roles = completion.get("artifact_roles") or {}
    if len(names) != 3 or len(set(names)) != 3 or set(roles.values()) != {"orders", "sales_detail", "shipping"}:
        raise ValueError("incomplete_export_manifest")
    archived = db.scalars(select(ImportedFile).where(
        ImportedFile.kind == "taobao", ImportedFile.original_filename.in_(names)
    ).order_by(ImportedFile.id)).all()
    latest = ({r.original_filename: r for r in provided} if provided else
              {r.original_filename: r for r in archived
               if (r.row_summary or {}).get("automation_batch_id") == batch_id})
    parsed, evidence = {}, []
    for name in names:
        rec = latest.get(name)
        summary = (rec.row_summary or {}) if rec else {}
        role = roles.get(name)
        if (not rec or summary.get("agent_status") != "imported" or summary.get("errors")
                or summary.get("agent_report_role") != role):
            raise ValueError("export_not_imported:" + name)
        raw = import_storage.read(rec.stored_path)
        if not rec.file_hash or sha256(raw).hexdigest() != rec.file_hash:
            raise ValueError("export_hash_mismatch:" + name)
        evidence.append({"file_id": rec.id, "role": role, "sha256": rec.file_hash})
        if role == "shipping":
            continue  # already imported; never request/retry a password here
        if importer.detect_report_role(name, raw) != role:
            raise ValueError("export_role_mismatch:" + name)
        report = importer.TaobaoImportReport()
        parsed[role] = (importer._parse_qianniu_multi(raw, report)
                        if importer.detect_format(name, raw) == "qianniu_multi"
                        else importer._parse_sales_detail(name, raw, report))
        if report.errors:
            raise ValueError("export_parse_failed:" + name)
    return batch_id, parsed["orders"], parsed["sales_detail"], evidence


def _source_rows(db, masters, details, only_order_nos, only_sub_order_nos=None):
    resolver = taobao_listing_service.build_resolver(db)
    seen, selected, excluded, remaining = set(), [], [], []
    for parent, master in masters.items():
        if parent in details or (only_order_nos is not None and parent not in only_order_nos):
            continue
        if importer._map_status(master.status_text) in ("paid", "production"):
            remaining.append({"order_no": parent, "reason": "sales_detail_missing_from_export"})
    for parent, order in details.items():
        if only_order_nos is not None and parent not in only_order_nos:
            continue
        for raw in order.lines:
            sub = str(raw.get("sub_order_no") or "")
            if only_sub_order_nos is not None and sub not in only_sub_order_nos:
                continue
            if not sub or sub in seen or not raw.get("explicit_child"):
                raise ValueError("ambiguous_export_child")
            seen.add(sub)
            info = {"order_no": parent, "sub_order_no": sub}
            status, recognized = importer._resolve_status(raw.get("status_text"))
            if not recognized:
                remaining.append({**info, "reason": "source_status_unknown"})
                continue
            name = str(raw.get("product_name") or "")
            candidate = OrderDetail(line_status=status, refund_status=raw.get("refund_status"),
                                    refund_amount=raw.get("refund"), amount=raw.get("amount"))
            reason = None
            if importer._is_service_line_name(name):
                reason = "service"
            elif any(x in name for x in ("样块", "样品", "小样", "样木")):
                reason = "sample"
            elif status not in ("paid", "production"):
                reason = "not_pending_production:" + str(status)
            elif delivery.line_is_refunded(candidate):
                reason = "refund_or_return"
            if reason:
                excluded.append({**info, "reason": reason})
                continue
            if parent not in masters:
                remaining.append({**info, "reason": "master_missing_from_export"})
                continue
            sku = importer._norm_pps_code(raw.get("sku_code"))
            product = importer._norm_pps_code(raw.get("product_code"))
            hit = taobao_listing_service.resolve_line(
                resolver, sku_id=raw.get("sku_id"), merchant_code=raw.get("sku_code"))
            if hit:
                sku, product = hit.get("sku_code") or sku, hit.get("product_code") or product
            qty = importer._to_int(raw.get("qty"), 0)
            if not sku or qty <= 0:
                remaining.append({**info, "reason": "source_sku_or_quantity_missing"})
                continue
            selected.append({**info, "sku": sku, "product": product, "qty": qty, "raw": raw})
    if only_order_nos:
        absent = only_order_nos - set(details)
        remaining.extend({"order_no": p, "reason": "order_absent_from_export"} for p in sorted(absent))
    if only_sub_order_nos:
        remaining.extend({"sub_order_no": sub, "reason": "child_absent_from_export"}
                         for sub in sorted(only_sub_order_nos - seen))
    return selected, excluded, remaining, resolver


def _run(db, *, batch_id, only_order_nos, only_sub_order_nos, file_ids, repair, limit):
    batch_id, masters, details, evidence = _sources(db, batch_id, file_ids)
    source, excluded, remaining, resolver = _source_rows(db, masters, details, only_order_nos, only_sub_order_nos)
    result = {"batch_id": batch_id, "source_files": evidence, "source_product_count": len(source),
              "source_order_count": len(masters),
              "source_child_count": sum(len(o.lines) for o in details.values()),
              "excluded": excluded, "remaining": remaining, "erp_repaired": [], "images_pushed": 0,
              "table_created": 0, "table_updated": 0, "verified": [], "repair": repair}
    sent = delivery.sent_line_evidence(db)
    legacy_parents = {str((r.row_summary or {}).get("order_no")) for r in db.scalars(
        select(ImportedFile).where(ImportedFile.kind == "order_sheet_sent"))
        if (r.row_summary or {}).get("pushed") and not (r.row_summary or {}).get("sub_order_no")
        and not (r.row_summary or {}).get("delivery_superseded")}
    tracked, ready = [], set()
    repairs = 0
    for fact in source:
        parent, sub = fact["order_no"], fact["sub_order_no"]
        info = {"order_no": parent, "sub_order_no": sub}
        order = db.scalar(select(Order).where(Order.order_no == parent))
        line = db.scalar(select(OrderDetail).where(OrderDetail.sub_order_no == sub))
        if not order or not line:
            if not repair or repairs >= limit:
                remaining.append({**info, "reason": "erp_missing"})
                continue
            if parent in legacy_parents:
                remaining.append({**info, "reason": "legacy_delivery_identity_unresolved"})
                continue
            if not order:
                report = importer.TaobaoImportReport()
                importer._commit_orders(db, {parent: masters[parent]}, "淘宝", report)
                if report.errors:
                    raise ValueError("missing_order_repair_failed")
                db.flush()
                order = db.scalar(select(Order).where(Order.order_no == parent))
            # Only missing children are repaired from the exact source row.
            importer._persist_order_lines(db, parent, [fact["raw"]], resolver, enable_factory_delivery=True)
            db.flush()
            line = db.scalar(select(OrderDetail).where(OrderDetail.sub_order_no == sub))
            repairs += 1
            result["erp_repaired"].append(info)
        if not line or not order:
            remaining.append({**info, "reason": "erp_repair_unresolved"})
            continue
        if line.order_no != parent or line.sku_code != fact["sku"] or line.qty != fact["qty"]:
            remaining.append({**info, "reason": "source_erp_conflict"})
            continue
        if order.is_historical:
            excluded.append({**info, "reason": "historical_baseline"})
            continue
        if not delivery.line_is_factory_eligible(db, line, order):
            from app.services import order_flags
            from app.services import factory_linked_topup_service as linked
            topup, cause = sheets._is_parts_topup(db, order)
            if (order.status in ('paid', 'production') and not order.is_refill
                    and not order_flags.is_remote(order) and not delivery.line_is_refunded(line)
                    and linked.audit_review_needed(db, order, line)):
                remaining.append({**info, 'reason': 'linked_topup_main_needs_review',
                    'detail': '真实主单可能被补差备注或失效关联拦截，需精确核对主/补差及定制尺寸'})
                continue
            if (topup and cause.startswith('金额') and order.status in ('paid', 'production')
                    and not order.is_refill and not order_flags.is_remote(order)
                    and not delivery.line_is_refunded(line)):
                remaining.append({**info, 'reason': 'low_value_order_needs_review', 'detail': cause})
                continue
            excluded.append({**info, "reason": "current_order_not_producible"})
            continue
        if sub not in sent and parent in legacy_parents:
            remaining.append({**info, "reason": "legacy_delivery_identity_unresolved"})
            continue
        tracked.append((order, line, info))
        if sub not in sent:
            if line.factory_delivery_state not in (None, "", "failed") or line.factory_delivery_message_id:
                remaining.append({**info, "reason": "delivery_uncertain_no_replay"})
            else:
                if repair:
                    line.factory_delivery_required = True
                ready.add(sub)
    if repair:
        db.commit()
    if repair and ready:
        # The sender owns the row claim and unknown-send protection.
        pushed = sheets.reconcile_order_line_delivery(db, limit=limit, only_sub_order_nos=ready)
        result["images_pushed"] = pushed.get("pushed", 0)
        result["image_failures"] = pushed.get("failures") or []
    db.expire_all()
    sent = delivery.sent_line_evidence(db)
    parents = {order.order_no for order, _, _ in tracked}
    # Existing sync reads each changed field and verifies entity uniqueness.
    if repair and parents:
        synced = table.sync(db, include_images=True, only_order_nos=parents)
        result["table_created"] = synced.get("created", 0)
        result["table_updated"] = synced.get("updated", 0)
        result["factory_dispatch"] = synced
        if not synced.get("ok") or synced.get("deferred_image_uploads"):
            remaining.append({"reason": "table_sync_unverified", "errors": synced.get("errors") or []})
    remote = table.feishu_client.list_records(db, *table._target(db)) if tracked else []
    by_sub = {}
    for rec in remote:
        fields = rec.get("fields") or {}
        key = str(table._norm(fields.get("子订单号")) or "")
        by_sub.setdefault(key, []).append(fields)
    projections = {str(r.get("子订单号")): r for r in table.build_rows(db)
                   if r.get("订单号") in parents} if tracked else {}
    bindings = table._load_image_bindings(db)
    for order, line, info in tracked:
        sub = line.sub_order_no
        failures = []
        if sub not in sent:
            failures.append("factory_image_not_sent")
        else:
            checked = delivery.audit_sent_line_content([line], sent)
            if checked["mismatches"] or checked["unverified"]:
                failures.append("factory_image_content_unverified")
        matches = by_sub.get(sub, [])
        row = projections.get(sub)
        if len(matches) != 1:
            failures.append("table_row_missing_or_duplicate")
        elif not row:
            failures.append("erp_projection_missing")
        else:
            fields = matches[0]
            # Compare actual production quantity, not the pricing-unit purchase count.
            for key in ("订单号", "子订单号", "SKU编码", "订购数量", "工厂下单号"):
                if not table._equivalent(fields.get(key), row.get(key)):
                    failures.append("table_field_mismatch:" + key)
            if not table._remote_attachment_matches(fields,
                    signature=str(row.get("_sheet_signature") or ""),
                    expected_name=str(row.get("_sheet_name") or ""),
                    bound_signature=str(bindings.get(sub) or "")):
                failures.append("table_image_missing_or_stale")
        if failures:
            remaining.append({**info, "factory_no": line.factory_no, "reasons": failures,
                              "detail": line.factory_delivery_error})
        else:
            result["verified"].append({**info, "factory_no": line.factory_no})
    result["verified_count"] = len(result["verified"])
    result["excluded_count"] = len(excluded)
    result["remaining_count"] = len({x.get("sub_order_no") or x.get("order_no") or x["reason"]
                                    for x in remaining})
    result["ok"] = not remaining
    return result


def audit(db, *, batch_id=None, only_order_nos=None, only_sub_order_nos=None,
          file_ids=None, repair=True, limit=500):
    """One bounded repair pass and independent source/remote readback."""
    try:
        result = _run(db, batch_id=batch_id, only_order_nos=only_order_nos,
                      only_sub_order_nos=only_sub_order_nos, file_ids=file_ids,
                      repair=repair, limit=max(1, min(int(limit), 500)))
    except Exception as exc:
        db.rollback()
        # Do not leak customer fields or credentials from arbitrary exceptions.
        result = {"ok": False, "batch_id": batch_id, "error_type": type(exc).__name__,
                  "reason": str(exc) if isinstance(exc, ValueError) else "audit_execution_failed",
                  "remaining_count": 1}
    result["checked_at"] = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    result["audit_id"] = uuid4().hex
    if repair:
        db.add(SystemSetting(key=PREFIX + result["audit_id"], value_plain=json.dumps(result, ensure_ascii=False),
                             is_secret=False, description="推送后原始报表、ERP、飞书表与工厂图逐子单核对"))
        db.commit()
    return result


def attach(db, result, *, batch_id=None, only_order_nos=None, only_sub_order_nos=None,
           file_ids=None, limit=500):
    checked = audit(db, batch_id=batch_id, only_order_nos=only_order_nos,
                    only_sub_order_nos=only_sub_order_nos, file_ids=file_ids, limit=limit)
    result["post_push_audit"] = checked
    result["post_push_added_image_count"] = int(checked.get("images_pushed") or 0)
    if not checked.get("ok"):
        missing, uncertain, review = set(), set(), set()
        for item in checked.get('remaining', []):
            identity = item.get('sub_order_no') or item.get('order_no') or item.get('reason')
            reasons = set(item.get('reasons') or []) | {item.get('reason')}
            if 'factory_image_not_sent' in reasons:
                missing.add(identity)
            elif 'delivery_uncertain_no_replay' in reasons:
                uncertain.add(identity)
            else:
                review.add(identity)
        checked['delivery_summary'] = {'missing_images': len(missing),
            'uncertain_delivery': len(uncertain), 'fact_review': len(review)}
        result["_run_status"] = "fail"
        previous = str(result.get("_error") or "")
        result["_error"] = (previous + "；" if previous else "") + (
            "推送后报表核对未完成：" + str(checked.get("reason") or
                                          f"明确缺图{len(missing)}单、发送待核实{len(uncertain)}单、资料或历史核对{len(review)}单"))
        problems = []
        for item in checked.get("remaining", [])[:5]:
            code = item.get("reason") or ",".join(item.get("reasons") or [])
            reason = {
                "source_erp_conflict": "ERP商品或购买数量与报表不一致",
                "low_value_order_needs_review": "实物商品被低金额门槛拦截，需核对后单独放行",
                "linked_topup_main_needs_review": "实物主单的补差关联或定制尺寸待核验，不能静默排除",
                "erp_missing": "ERP缺少订单或子单",
                "legacy_delivery_identity_unresolved": "旧图无法唯一对应子单，需核对",
                "delivery_uncertain_no_replay": "此前发送结果未知，不能重复发",
                "sales_detail_missing_from_export": "订单总表有记录，销售明细缺失",
                "order_absent_from_export": "本批报表没有此订单",
                "source_status_unknown": "报表订单状态无法识别",
                "source_sku_or_quantity_missing": "报表缺具体商品编码或购买数量",
                "factory_image_content_unverified": "旧工厂图缺可核实的商品数量存档",
            }.get(code, item.get("detail") or code)
            problems.append(str(item.get("sub_order_no") or item.get("order_no") or "") + "：" + reason)
        if problems:
            result["_error"] += "；" + "；".join(problems)
    return checked
