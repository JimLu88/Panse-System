"""Excel importer API: 上传 → 预览 → 提交."""
from __future__ import annotations

import base64
import io
from datetime import date

from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.supplier import DeliveryNote, Supplier
from app.services import auth_service


def _client():
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s = Sess()
    admin = auth_service.create_user(s, username="admin", password="x", role="admin",
                                     display_name="管理员")
    s.commit()
    token = auth_service.create_token(user_id=admin.id, username=admin.username, role="admin")
    viewer = auth_service.create_user(s, username="v", password="x", role="viewer",
                                      display_name="viewer")
    s.commit()
    viewer_token = auth_service.create_token(user_id=viewer.id, username=viewer.username,
                                              role="viewer")
    s.close()
    def override():
        ses = Sess()
        try: yield ses
        finally: ses.close()
    app.dependency_overrides[get_db] = override
    return TestClient(app), token, viewer_token, Sess


def _make_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "5月对账"
    ws.append(["供应商", "单号", "日期", "品名", "数量", "金额"])
    ws.append(["木作工厂", "N1001", "2026-05-14", "电视柜", 1, 580])
    ws.append(["木作工厂", "N1001", "2026-05-14", "斗柜", 1, 320])
    ws.append(["岩板厂", "N1002", "2026-05-15", "台面", 2, 1200])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_entity_types_endpoint():
    client, token, _, _ = _client()
    try:
        r = client.get("/api/importer/entity-types",
                       headers={"Authorization": f"Bearer {token}"})
        # entity-types 是元数据 — 当前实现没限角色, 任何已登录都可
        # 但 require_role 不在 GET 上; 此处仍要有 token
        body = r.json() if r.status_code == 200 else []
        # 至少应包含 delivery_note 与 factory_order
        assert r.status_code == 200
        values = {e["value"] for e in body}
        assert values >= {"delivery_note", "factory_order", "alipay_flow"}
        dn = next(e for e in body if e["value"] == "delivery_note")
        assert any(f["name"] == "supplier_name" and f["required"] for f in dn["fields"])
    finally:
        app.dependency_overrides.clear()


def test_preview_requires_role():
    client, _, viewer_token, _ = _client()
    try:
        r = client.post("/api/importer/preview",
                       headers={"Authorization": f"Bearer {viewer_token}"},
                       files={"file": ("x.xlsx", _make_xlsx(),
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_full_preview_then_commit_flow():
    client, token, _, Sess = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        xlsx = _make_xlsx()
        # 1) preview
        r = client.post("/api/importer/preview", headers=h,
                        files={"file": ("5月.xlsx", xlsx,
                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["sheets"]) == 1
        sheet = body["sheets"][0]
        assert sheet["sheet_name"] == "5月对账"
        assert sheet["row_count"] == 3
        assert sheet["column_names"] == ["供应商", "单号", "日期", "品名", "数量", "金额"]
        # AI 未配置, suggested_mapping 应该是 {}, notes 含说明
        assert sheet["suggested_mapping"] == {}
        # file_b64 应回传
        assert body["file_b64"]
        # base64 解码长度应等于上传大小
        assert len(base64.b64decode(body["file_b64"])) == len(xlsx)

        # 2) commit (用户在 UI 选好 mapping 后)
        commit_payload = {
            "file_b64": body["file_b64"],
            "sheet_name": "5月对账",
            "entity_type": "delivery_note",
            "mapping": {
                "supplier_name": "供应商",
                "note_no": "单号",
                "delivery_date": "日期",
                "item_name": "品名",
                "qty": "数量",
                "amount": "金额",
            },
            "auto_create_suppliers": True,
            "auto_match_orders": False,
            "dry_run": False,
        }
        r = client.post("/api/importer/commit", headers=h, json=commit_payload)
        assert r.status_code == 200, r.text
        rep = r.json()
        assert rep["entity_type"] == "delivery_note"
        assert rep["total_rows"] == 3
        assert rep["inserted_parents"] == 2     # N1001, N1002
        assert rep["inserted_children"] == 3
        assert set(rep["auto_created_suppliers"]) == {"木作工厂", "岩板厂"}

        # DB 落盘检查
        ses = Sess()
        notes = ses.execute(select(DeliveryNote).order_by(DeliveryNote.note_no)).scalars().all()
        assert len(notes) == 2
        suppliers = ses.execute(select(Supplier)).scalars().all()
        assert {s.name for s in suppliers} == {"木作工厂", "岩板厂"}
        # 自动创建的 type=other
        assert all(s.supplier_type == "other" for s in suppliers)
        ses.close()
    finally:
        app.dependency_overrides.clear()


def test_commit_missing_required_returns_400():
    client, token, _, _ = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        xlsx = _make_xlsx()
        r = client.post("/api/importer/preview", headers=h,
                        files={"file": ("x.xlsx", xlsx,
                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        b64 = r.json()["file_b64"]
        r = client.post("/api/importer/commit", headers=h, json={
            "file_b64": b64,
            "sheet_name": "5月对账",
            "entity_type": "delivery_note",
            "mapping": {"item_name": "品名"},   # 漏了 supplier_name / delivery_date / qty
        })
        assert r.status_code == 400
        assert "必填" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_commit_dry_run_does_not_persist():
    client, token, _, Sess = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        xlsx = _make_xlsx()
        r = client.post("/api/importer/preview", headers=h,
                        files={"file": ("x.xlsx", xlsx,
                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        b64 = r.json()["file_b64"]
        r = client.post("/api/importer/commit", headers=h, json={
            "file_b64": b64, "sheet_name": "5月对账",
            "entity_type": "delivery_note",
            "mapping": {
                "supplier_name": "供应商", "note_no": "单号",
                "delivery_date": "日期", "item_name": "品名",
                "qty": "数量", "amount": "金额",
            },
            "dry_run": True,
        })
        rep = r.json()
        assert rep["inserted_parents"] == 2   # 报告里有数字
        # 但 DB 是空的
        ses = Sess()
        assert ses.execute(select(DeliveryNote)).first() is None
        assert ses.execute(select(Supplier)).first() is None
        ses.close()
    finally:
        app.dependency_overrides.clear()


def test_commit_invalid_file_b64_returns_400():
    client, token, _, _ = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = client.post("/api/importer/commit", headers=h, json={
            "file_b64": "not-base64-!@#$",
            "sheet_name": "S", "entity_type": "delivery_note",
            "mapping": {"supplier_name": "x", "delivery_date": "y",
                        "item_name": "z", "qty": "q"},
        })
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
