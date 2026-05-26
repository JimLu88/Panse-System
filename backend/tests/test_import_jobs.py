"""异步导入作业: submit / progress / done / failed."""
from __future__ import annotations

import io
import threading
import time
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from openpyxl import Workbook

from app.models.import_job import ImportJob
from app.models.supplier import DeliveryNote, Supplier
from app.services import excel_importer, import_job_service


def _xlsx(header, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "S"
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------- inline 模式 (测试用) ---------------- #


def test_submit_import_inline_runs_to_done(db_session):
    data = _xlsx(["供应商", "日期", "品名", "数量"], [
        ["X", "2026-05-14", "p1", 1],
        ["Y", "2026-05-14", "p2", 2],
    ])
    job = import_job_service.submit_import(
        db_session,
        file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        user_id=1,
        auto_match_orders=False,
        inline=True,
    )
    db_session.refresh(job)
    assert job.status == "done"
    assert job.total_rows == 2
    assert job.processed_rows == 2
    assert job.report["inserted_parents"] == 2
    assert job.started_at is not None
    assert job.completed_at is not None


def test_submit_import_records_initial_state(db_session):
    data = _xlsx(["供应商", "日期", "品名", "数量"], [["X", "2026-05-14", "p", 1]])
    job = import_job_service.submit_import(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        user_id=42,
        inline=True,
    )
    db_session.refresh(job)
    assert job.user_id == 42
    assert job.sheet_name == "S"
    assert job.entity_type == "delivery_note"
    assert job.mapping["supplier_name"] == "供应商"
    assert job.options_json["auto_create_suppliers"] is True


def test_submit_import_inline_failed_when_required_missing(db_session):
    data = _xlsx(["数量"], [[1]])
    job = import_job_service.submit_import(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"qty": "数量"},  # 缺必填
        inline=True,
    )
    db_session.refresh(job)
    assert job.status == "failed"
    assert "必填" in (job.error or "")
    assert job.completed_at is not None


def test_submit_import_inline_bad_excel_marks_failed(db_session):
    job = import_job_service.submit_import(
        db_session, file_bytes=b"not an xlsx", sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "x", "delivery_date": "y",
                 "item_name": "z", "qty": "q"},
        inline=True,
    )
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error


# ----------------------------- progress ------------------------- #


def test_progress_callback_called_with_total_after_completion(db_session):
    """commit_sheet 完成后 callback 应被调用一次 (done, total)."""
    data = _xlsx(["供应商", "日期", "品名", "数量"], [
        [f"X{i}", "2026-05-14", "p", 1] for i in range(10)
    ])
    calls = []
    excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        auto_match_orders=False,
        progress_callback=lambda d, t: calls.append((d, t)),
    )
    # 应至少有 (0, 10) 和 (10, 10)
    assert (0, 10) in calls
    assert (10, 10) in calls


def test_progress_callback_called_during_large_import(db_session):
    """大于 50 行时, 进度中途也会回报."""
    rows = [[f"X{i}", "2026-05-14", "p", 1] for i in range(120)]
    data = _xlsx(["供应商", "日期", "品名", "数量"], rows)
    calls = []
    excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        auto_match_orders=False,
        progress_callback=lambda d, t: calls.append((d, t)),
    )
    # 应有中途回报 (50, 120) 和 (100, 120) 至少其中之一
    middle_calls = [c for c in calls if 0 < c[0] < 120]
    assert len(middle_calls) > 0


# ----------------------------- listing -------------------------- #


def test_list_jobs_returns_newest_first(db_session):
    db_session.add_all([
        ImportJob(entity_type="delivery_note", sheet_name="A", status="done"),
        ImportJob(entity_type="delivery_note", sheet_name="B", status="done"),
        ImportJob(entity_type="alipay_flow",   sheet_name="C", status="running"),
    ])
    db_session.flush()
    rows = import_job_service.list_jobs(db_session, limit=10)
    assert len(rows) == 3
    assert rows[0].sheet_name == "C"  # 最新


def test_list_jobs_filter_by_user(db_session):
    db_session.add_all([
        ImportJob(user_id=1, entity_type="x", sheet_name="A", status="done"),
        ImportJob(user_id=2, entity_type="x", sheet_name="B", status="done"),
        ImportJob(user_id=1, entity_type="x", sheet_name="C", status="done"),
    ])
    db_session.flush()
    rows = import_job_service.list_jobs(db_session, user_id=1, limit=10)
    assert {r.sheet_name for r in rows} == {"A", "C"}


def test_get_job_returns_none_for_missing(db_session):
    assert import_job_service.get_job(db_session, 99999) is None


def test_get_job_returns_existing(db_session):
    j = ImportJob(entity_type="x", sheet_name="S", status="done")
    db_session.add(j); db_session.flush()
    got = import_job_service.get_job(db_session, j.id)
    assert got.id == j.id


# ----------------------------- shutdown ------------------------- #


def test_shutdown_executor_safe_when_not_started():
    """没起过 executor 时调 shutdown 不应崩."""
    import_job_service.shutdown_executor()
    # 再调一次也不应崩
    import_job_service.shutdown_executor()
