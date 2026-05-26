"""ImportJob 取消机制: cancel_requested → CancelledImport → status=cancelled."""
from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from app.models.import_job import ImportJob
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


# ----------------------------- 直接调 importer ---------------------- #


def test_cancel_callback_raises_cancelled_import(db_session):
    """cancel_callback() 返回 True 时 commit_sheet 抛 CancelledImport."""
    rows = [[f"X{i}", "2026-05-14", "p", 1] for i in range(200)]
    data = _xlsx(["供应商", "日期", "品名", "数量"], rows)

    counter = {"n": 0}

    def cancel() -> bool:
        counter["n"] += 1
        return counter["n"] >= 2   # 第 2 次 tick 时取消

    with pytest.raises(excel_importer.CancelledImport):
        excel_importer.commit_sheet(
            db_session, file_bytes=data, sheet_name="S",
            entity_type="delivery_note",
            mapping={"supplier_name": "供应商", "delivery_date": "日期",
                     "item_name": "品名", "qty": "数量"},
            auto_match_orders=False,
            cancel_callback=cancel,
        )


def test_cancel_callback_false_runs_normally(db_session):
    """cancel_callback 一直 False → 正常完成."""
    data = _xlsx(["供应商", "日期", "品名", "数量"], [["X", "2026-05-14", "p", 1]])
    report = excel_importer.commit_sheet(
        db_session, file_bytes=data, sheet_name="S",
        entity_type="delivery_note",
        mapping={"supplier_name": "供应商", "delivery_date": "日期",
                 "item_name": "品名", "qty": "数量"},
        auto_match_orders=False,
        cancel_callback=lambda: False,
    )
    assert report.inserted_parents == 1


# ----------------------------- import_job_service.request_cancel ---- #


def test_request_cancel_pending_marks_cancelled(db_session):
    """pending 状态的 job 直接标 cancelled."""
    job = ImportJob(entity_type="delivery_note", sheet_name="S", status="pending")
    db_session.add(job); db_session.flush()
    result = import_job_service.request_cancel(db_session, job.id)
    assert result.status == "cancelled"
    assert result.cancel_requested is True
    assert result.completed_at is not None


def test_request_cancel_running_only_sets_flag(db_session):
    """running 的 job 只设旗标; worker 自己改 status."""
    job = ImportJob(entity_type="delivery_note", sheet_name="S", status="running")
    db_session.add(job); db_session.flush()
    result = import_job_service.request_cancel(db_session, job.id)
    assert result.status == "running"
    assert result.cancel_requested is True


def test_request_cancel_done_is_noop(db_session):
    """done 的 job 调 cancel 不改 status."""
    job = ImportJob(entity_type="delivery_note", sheet_name="S", status="done")
    db_session.add(job); db_session.flush()
    result = import_job_service.request_cancel(db_session, job.id)
    assert result.status == "done"
    assert result.cancel_requested is False


def test_request_cancel_unknown_returns_none(db_session):
    assert import_job_service.request_cancel(db_session, 99999) is None


# ----------------------------- 端到端 API ------------------------- #


def test_cancel_endpoint(db_session):
    """POST /api/importer/jobs/{id}/cancel 走 admin token."""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import get_db
    from app.main import app
    from app.models import Base
    from app.services import auth_service

    engine = create_engine("sqlite:///:memory:", future=True,
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s = Sess()
    admin = auth_service.create_user(s, username="admin", password="x", role="admin",
                                     display_name="A")
    s.commit()
    token = auth_service.create_token(user_id=admin.id, username=admin.username, role="admin")
    job = ImportJob(entity_type="delivery_note", sheet_name="S", status="running")
    s.add(job); s.commit()
    job_id = job.id
    s.close()

    def override():
        ses = Sess()
        try: yield ses
        finally: ses.close()
    app.dependency_overrides[get_db] = override

    try:
        client = TestClient(app)
        r = client.post(f"/api/importer/jobs/{job_id}/cancel",
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == job_id

        # 重新查 DB 确认旗标被设
        check = Sess()
        try:
            j = check.get(ImportJob, job_id)
            assert j.cancel_requested is True
        finally:
            check.close()

        # 404
        r = client.post("/api/importer/jobs/99999/cancel",
                        headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()
