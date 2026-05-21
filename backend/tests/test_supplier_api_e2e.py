"""供应商对账端到端: 创建 → 上传 OCR(mock) → 自动匹配 → 月度对账."""
from __future__ import annotations

import io
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.order import FactoryOrder
from app.services import auth_service, ocr_service
from app.services.ocr_service import ParsedDeliveryLine, ParsedDeliveryNote
from decimal import Decimal


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_STORAGE_ROOT", str(tmp_path))
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
    s.close()

    def override():
        ses = Sess()
        try: yield ses
        finally: ses.close()
    app.dependency_overrides[get_db] = override
    return TestClient(app), token, Sess


def test_full_flow_supplier_upload_match_statement(tmp_path, monkeypatch):
    client, token, Sess = _client(tmp_path, monkeypatch)
    h = {"Authorization": f"Bearer {token}"}
    try:
        # 1) 创建供应商
        r = client.post("/api/suppliers", headers=h, json={
            "name": "木作工厂", "supplier_type": "woodwork", "payment_terms": "月结",
        })
        assert r.status_code == 201
        supplier_id = r.json()["id"]

        # 2) 系统里先放一个候选 FactoryOrder, 用于匹配
        ses = Sess()
        ses.add(FactoryOrder(
            factory_order_no="FO-2026-001", platform_order_no="TB-001",
            factory_name="木作工厂", product_code="P1",
            sku="电视柜 1800×850 黑色", qty=1, order_date=date(2026, 5, 10),
            expected_delivery=date(2026, 5, 14),
        ))
        ses.commit(); ses.close()

        # 3) Mock OCR — 不打真 API
        fake = ParsedDeliveryNote(
            note_no="5480798", delivery_date=date(2026, 5, 14),
            total_amount=Decimal("580"),
            lines=[ParsedDeliveryLine(
                line_no=1, item_name="电视柜", spec="1800×850", unit="套",
                qty=Decimal("1"), unit_price=Decimal("580"), amount=Decimal("580"),
                raw_text="电视柜 1800×850 1套 580", warnings=[],
            )],
            warnings=[], model="claude-x", raw_response="", confidence=Decimal("95"),
        )
        with patch.object(ocr_service, "ocr_delivery_note", return_value=fake):
            r = client.post(
                f"/api/suppliers/{supplier_id}/delivery-notes",
                headers=h,
                files={"file": ("note.jpg", b"\xff\xd8\xffjpegdata", "image/jpeg")},
                data={"on_date": "2026-05-14"},
            )
        assert r.status_code == 201, r.text
        note = r.json()
        note_id = note["id"]
        assert note["note_no"] == "5480798"
        assert note["delivery_date"] == "2026-05-14"
        assert note["total_amount"] == 580.0
        assert note["status"] == "pending_review"
        assert note["ocr_confidence"] == 95.0
        assert len(note["lines"]) == 1
        line = note["lines"][0]
        # 应自动匹配到 FO-2026-001 (sku 包含 电视柜 1800×850)
        assert line["matched_order_no"] in {"TB-001", "FO-2026-001"}
        assert line["match_confidence"] is not None
        assert line["match_confidence"] >= 60
        assert len(line["match_candidates"]) >= 1

        # 4) 列表能看到
        r = client.get(f"/api/suppliers/{supplier_id}/delivery-notes", headers=h)
        assert r.status_code == 200
        assert len(r.json()) == 1

        # 5) 文件夹里能列出原图
        r = client.get(f"/api/suppliers/{supplier_id}/folders/2026/5", headers=h)
        body = r.json()
        assert body["file_count"] == 1
        assert body["files"][0]["delivery_note_id"] == note_id

        # 6) 用户手动改匹配 — 改成 manual + 100%
        line_id = line["id"]
        r = client.patch(
            f"/api/delivery-notes/{note_id}/lines/{line_id}/match",
            headers=h,
            json={"matched_order_no": "TB-CUSTOM-999"},
        )
        assert r.status_code == 200
        assert r.json()["matched_order_no"] == "TB-CUSTOM-999"
        assert r.json()["match_confidence"] == 100.0
        assert r.json()["match_method"] == "manual"

        # 7) 状态机: pending_review → confirmed → paid
        r = client.patch(f"/api/delivery-notes/{note_id}", headers=h,
                        json={"status": "confirmed"})
        assert r.status_code == 200 and r.json()["status"] == "confirmed"
        r = client.patch(f"/api/delivery-notes/{note_id}", headers=h,
                        json={"status": "paid", "alipay_flow_no": "AL202605140001"})
        assert r.status_code == 200 and r.json()["status"] == "paid"

        # 8) 月度对账表 Excel
        r = client.get(f"/api/suppliers/{supplier_id}/statements/2026/5.xlsx", headers=h)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert r.content[:2] == b"PK"
        assert len(r.content) > 2000

        # 9) HTML 打印版
        r = client.get(f"/api/suppliers/{supplier_id}/statements/2026/5.html", headers=h)
        assert r.status_code == 200
        assert "木作工厂" in r.text
        assert "5480798" in r.text
        assert "已付款" in r.text

        # 10) rematch (用户改过的 manual 不被覆盖)
        r = client.post(f"/api/delivery-notes/{note_id}/rematch", headers=h)
        assert r.status_code == 200
        # manual 标记应保留
        assert r.json()["lines"][0]["matched_order_no"] == "TB-CUSTOM-999"
        assert r.json()["lines"][0]["match_method"] == "manual"
    finally:
        app.dependency_overrides.clear()


def test_upload_when_ocr_unavailable_keeps_file_in_pending(tmp_path, monkeypatch):
    client, token, _ = _client(tmp_path, monkeypatch)
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = client.post("/api/suppliers", headers=h, json={
            "name": "岩板厂", "supplier_type": "rock_slab",
        })
        supplier_id = r.json()["id"]
        # OCR raises
        with patch.object(ocr_service, "ocr_delivery_note",
                          side_effect=ocr_service.OcrUnavailable("ocr key missing")):
            r = client.post(
                f"/api/suppliers/{supplier_id}/delivery-notes",
                headers=h,
                files={"file": ("rock.jpg", b"\xff\xd8\xff", "image/jpeg")},
            )
        assert r.status_code == 201
        body = r.json()
        assert body["status"] == "pending_review"
        assert any("OCR" in w for w in body["ocr_warnings"])
        # 原图仍在 (用户事后可以人工填或换 provider 再 rematch)
        assert body["source_file_id"] is not None
    finally:
        app.dependency_overrides.clear()


def test_supplier_create_dup_name_returns_409(tmp_path, monkeypatch):
    client, token, _ = _client(tmp_path, monkeypatch)
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = client.post("/api/suppliers", headers=h, json={"name": "玻璃厂"})
        assert r.status_code == 201
        r = client.post("/api/suppliers", headers=h, json={"name": "玻璃厂"})
        assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_supplier_seed_idempotent(db_session):
    """业务需求: 3 个默认供应商种入, 跑两次也只有 3 家."""
    from app.services.supplier_seed import seed_default_suppliers
    from app.models.supplier import Supplier
    from sqlalchemy import select

    first = seed_default_suppliers(db_session)
    db_session.commit()
    assert len(first) == 3
    assert {s.name for s in first} == {"木作工厂", "岩板厂", "玻璃厂"}

    second = seed_default_suppliers(db_session)
    assert len(second) == 0
    total = db_session.execute(select(Supplier)).scalars().all()
    assert len(total) == 3


def test_reconcile_payments_endpoint(tmp_path, monkeypatch):
    """端到端: 创建供应商 + 单据 + 流水, 调 /api/suppliers/reconcile-payments."""
    from app.models.finance import AlipayFlow
    from app.models.supplier import DeliveryNote, Supplier
    from datetime import datetime, timezone

    client, token, Sess = _client(tmp_path, monkeypatch)
    h = {"Authorization": f"Bearer {token}"}
    try:
        # 创建供应商 + 关键字
        r = client.post("/api/suppliers", headers=h, json={
            "name": "木作工厂", "supplier_type": "woodwork",
            "alipay_counterparty_keywords": ["X木业", "佛山木业"],
        })
        assert r.status_code == 201
        sup_id = r.json()["id"]
        assert r.json()["alipay_counterparty_keywords"] == ["X木业", "佛山木业"]

        # 直接 ORM 插单据 + 流水
        ses = Sess()
        n = DeliveryNote(supplier_id=sup_id, note_no="N-9001",
                         delivery_date=date(2026, 5, 14),
                         total_amount=Decimal("580"), status="confirmed")
        ses.add(n)
        f = AlipayFlow(
            account="企业号", transaction_no="TX-9001",
            transaction_time=datetime(2026, 5, 14, tzinfo=timezone.utc),
            transaction_type="转账", counterparty="X木业有限公司",
            amount=Decimal("-580"),
            reconciliation_type="factory_payment", reconciliation_status="open",
        )
        ses.add(f)
        ses.commit(); ses.close()

        # dry_run 先看一眼
        r = client.post("/api/suppliers/reconcile-payments", headers=h,
                        json={"dry_run": True, "since_days": 90})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scanned"] == 1
        assert body["matched_count"] == 1
        assert body["matches"][0]["decision"] == "exact"
        assert body["matches"][0]["supplier_name"] == "木作工厂"

        # 正式跑
        r = client.post("/api/suppliers/reconcile-payments", headers=h,
                        json={"dry_run": False})
        assert r.json()["matched_count"] == 1

        # 单据应已被标 paid
        r = client.get(f"/api/suppliers/{sup_id}/delivery-notes", headers=h)
        notes = r.json()
        assert notes[0]["status"] == "paid"
    finally:
        app.dependency_overrides.clear()


def test_reconcile_manual_match(tmp_path, monkeypatch):
    """needs_review 场景下用户手动确认."""
    from app.models.finance import AlipayFlow
    from app.models.supplier import DeliveryNote
    from datetime import datetime, timezone

    client, token, Sess = _client(tmp_path, monkeypatch)
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = client.post("/api/suppliers", headers=h, json={
            "name": "X木业", "supplier_type": "woodwork",
            "alipay_counterparty_keywords": ["X木业"],
        })
        sup_id = r.json()["id"]

        ses = Sess()
        n1 = DeliveryNote(supplier_id=sup_id, note_no="A",
                          delivery_date=date(2026, 5, 10),
                          total_amount=Decimal("200"), status="confirmed")
        n2 = DeliveryNote(supplier_id=sup_id, note_no="B",
                          delivery_date=date(2026, 5, 11),
                          total_amount=Decimal("300"), status="confirmed")
        ses.add(n1); ses.add(n2)
        f = AlipayFlow(
            account="企业号", transaction_no="TX-M1",
            transaction_time=datetime(2026, 5, 14, tzinfo=timezone.utc),
            counterparty="X木业", amount=Decimal("-500"),
            reconciliation_type="factory_payment", reconciliation_status="open",
        )
        ses.add(f); ses.commit()
        n1_id, n2_id, f_id = n1.id, n2.id, f.id
        ses.close()

        # combo 唯一 → 应该 decision=combo
        r = client.post("/api/suppliers/reconcile-payments", headers=h,
                        json={"dry_run": True})
        assert r.json()["matches"][0]["decision"] == "combo"

        # 模拟 needs_review: 用错误金额组合手动调用应失败
        r = client.post("/api/suppliers/reconcile-payments/manual", headers=h,
                        json={"flow_id": f_id, "note_ids": [n1_id]})
        assert r.status_code == 400
        assert "金额对不上" in r.json()["detail"]

        # 正确组合
        r = client.post("/api/suppliers/reconcile-payments/manual", headers=h,
                        json={"flow_id": f_id, "note_ids": [n1_id, n2_id]})
        assert r.status_code == 200
        assert r.json()["decision"] == "combo"
        assert set(r.json()["matched_note_ids"]) == {n1_id, n2_id}
    finally:
        app.dependency_overrides.clear()
